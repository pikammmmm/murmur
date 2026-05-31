"""Local faster-whisper transcriber — the offline fallback (CPU, int8).

faster-whisper/CTranslate2 accelerates on CPU and NVIDIA CUDA only; on this AMD
machine it runs on CPU, which is fine for a rarely-hit offline fallback. Heavy
imports are lazy so constructing this object (e.g. in tests) is free.
"""
import logging

log = logging.getLogger("murmur.stt.local")


class LocalTranscriber:
    def __init__(self, model_size="base", language="en", beam_size=5, vad_filter=True):
        self.model_size = model_size
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._model = None

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("loading faster-whisper %s (cpu/int8)", self.model_size)
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def warm(self):
        """Pay the lazy load + first-inference cost up front."""
        import numpy as np
        try:
            model = self._ensure()
            list(model.transcribe(np.zeros(8000, dtype="float32"), language=self.language, beam_size=1)[0])
        except Exception as exc:
            log.warning("warmup failed: %s", exc)

    def transcribe(self, audio, sr, prompt):
        model = self._ensure()
        # `hotwords` (faster-whisper >=1.0.2) biases toward rare terms better than
        # initial_prompt; it must NOT be paired with `prefix` (we don't use prefix).
        segments, _ = model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
            hotwords=(prompt or None),
        )
        return "".join(seg.text for seg in segments).strip()
