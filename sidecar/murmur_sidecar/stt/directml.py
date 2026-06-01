"""GPU transcriber via DirectML — runs full Whisper (openai-whisper) on any
DirectX 12 GPU on Windows, including AMD (no CUDA needed). This is the high-
accuracy offline path on this machine (Radeon RX 7800 XT).

Heavy deps (torch, torch-directml, openai-whisper) are optional and imported
lazily, so importing this module and constructing the object stay cheap — and a
build without those deps (e.g. the frozen CPU sidecar) just falls back to the
CPU transcriber at runtime via transcribe_with_fallback.
"""
import logging

log = logging.getLogger("murmur.stt.directml")

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


class DirectMLTranscriber:
    def __init__(self, model_size="large-v3", language="en", beam_size=5):
        self.model_size = model_size
        self.language = language
        self.beam_size = beam_size
        self._model = None
        self._device = None

    def _ensure(self):
        if self._model is None:
            import torch_directml
            import whisper
            self._device = torch_directml.device()
            log.info("loading Whisper %s on DirectML GPU (%s)",
                     self.model_size, torch_directml.device_name(0))
            model = whisper.load_model(self.model_size, device="cpu")
            # openai-whisper stores `alignment_heads` as a SPARSE buffer (used
            # only for word-timestamp DTW, which we don't use); DirectML can't
            # hold sparse tensors, so densify it before moving to the GPU.
            ah = getattr(model, "alignment_heads", None)
            if ah is not None and ah.is_sparse:
                model.alignment_heads = ah.to_dense()
            self._model = model.to(self._device)
        return self._model

    def warm(self):
        """Pay the (large) model load + first-inference cost up front."""
        import numpy as np
        try:
            self._ensure().transcribe(
                np.zeros(WHISPER_SR, dtype="float32"), language=self.language,
                fp16=False, beam_size=1, condition_on_previous_text=False,
            )
        except Exception as exc:
            log.warning("DirectML warmup failed: %s", exc)

    def transcribe(self, audio, sr, prompt):
        import numpy as np
        model = self._ensure()
        audio = np.asarray(audio, dtype="float32")
        audio = _resample(audio, sr)
        # DirectML has no fp16; initial_prompt biases the decoder toward our vocab.
        result = model.transcribe(
            audio, language=self.language, beam_size=self.beam_size, fp16=False,
            condition_on_previous_text=False, initial_prompt=(prompt or None),
        )
        return (result.get("text") or "").strip()
