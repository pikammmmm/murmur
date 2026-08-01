"""GPU transcriber via ROCm — the Linux counterpart to directml.py.

Windows reaches the Radeon RX 7800 XT through DirectML. That is a DirectX 12
API and does not exist on Linux, and the obvious substitute does not work
either: faster-whisper's CTranslate2 backend is CUDA-only, with no ROCm
support. Without this module the `gpu` provider silently degrades to the CPU
transcriber, which is roughly an order of magnitude slower and was the entire
reason dictation felt slower on Linux than on Windows.

PyTorch's ROCm build fills the gap, and openai-whisper sits on plain PyTorch,
so the same model code runs unchanged — only the device differs.

Two deliberate differences from the DirectML path:

  * fp16 is ON. DirectML has no fp16 so that path forces fp32; ROCm does
    support half precision, which is a substantial speedup on RDNA3 and is
    what makes this competitive with the Windows build.
  * No sparse-tensor densification. That workaround exists because DirectML
    cannot hold the sparse `alignment_heads` buffer; HIP has no such limit.

Heavy deps (torch, openai-whisper) are imported lazily so importing this module
stays cheap, and a machine without them falls back to CPU at runtime via
transcribe_with_fallback rather than failing at startup.
"""
import logging
import os

log = logging.getLogger("murmur.stt.rocm")

WHISPER_SR = 16000


def _resample(audio, src_sr, dst_sr=WHISPER_SR):
    import numpy as np
    if not src_sr or src_sr == dst_sr or len(audio) == 0:
        return audio
    n = int(round(len(audio) * dst_sr / src_sr))
    if n <= 0:
        return audio
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype("float32")


def available():
    """True when a ROCm GPU is actually usable, without importing whisper.

    Kept cheap and exception-proof: callers use it to decide whether to offer
    the GPU path at all, and a torch build without ROCm must not take the
    sidecar down.
    """
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception as exc:
        log.debug("ROCm probe failed: %s", exc)
        return False


class RocmTranscriber:
    def __init__(self, model_size="turbo", language="en", beam_size=5, fp16=True):
        self.model_size = model_size
        self.language = language
        self.beam_size = beam_size
        self.fp16 = fp16
        self._model = None

    def _ensure(self):
        if self._model is None:
            import torch
            import whisper
            if not torch.cuda.is_available():
                # Raise rather than limp along on CPU: the caller's fallback
                # already handles CPU, and doing it here would hide the reason.
                raise RuntimeError(
                    "no ROCm device visible to torch "
                    f"(HSA_OVERRIDE_GFX_VERSION={os.environ.get('HSA_OVERRIDE_GFX_VERSION', 'unset')})"
                )
            name = torch.cuda.get_device_name(0)
            log.info("loading Whisper %s on ROCm GPU (%s), fp16=%s",
                     self.model_size, name, self.fp16)
            self._model = whisper.load_model(self.model_size, device="cuda")
        return self._model

    def warm(self):
        """Pay the model load + first-inference cost up front.

        Worth doing explicitly: the first GPU inference also builds kernels, so
        without this the user's first dictation of the session eats several
        seconds that have nothing to do with their audio.
        """
        import numpy as np
        try:
            self._ensure().transcribe(
                np.zeros(WHISPER_SR, dtype="float32"), language=self.language,
                fp16=self.fp16, beam_size=1, condition_on_previous_text=False,
            )
        except Exception as exc:
            log.warning("ROCm warmup failed: %s", exc)

    def transcribe(self, audio, sr, prompt):
        import numpy as np
        model = self._ensure()
        audio = np.asarray(audio, dtype="float32")
        audio = _resample(audio, sr)
        # Same reasoning as the DirectML path: `prompt` is deliberately NOT
        # passed as initial_prompt. An unpunctuated glossary prompt conditions
        # openai-whisper's output style and makes it drop punctuation, without
        # improving term recognition. The post-STT corrector handles taught terms.
        result = model.transcribe(
            audio, language=self.language, beam_size=self.beam_size,
            fp16=self.fp16, condition_on_previous_text=False,
        )
        return (result.get("text") or "").strip()
