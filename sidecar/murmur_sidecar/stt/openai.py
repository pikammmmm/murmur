"""OpenAI gpt-4o-transcribe transcriber — the high-accuracy mode.

Highest WER accuracy and best on technical jargon; ~9x Groq's cost (still
pennies). Enabled via ``stt.accuracy_mode``. SDK import is lazy.
"""
import logging

log = logging.getLogger("murmur.stt.openai")


class OpenAITranscriber:
    def __init__(self, api_key, model="gpt-4o-transcribe", language="en"):
        self.api_key = api_key
        self.model = model
        self.language = language
        self._client = None

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def transcribe(self, audio, sr, prompt):
        from .wavutil import to_wav_bytes
        client = self._ensure()
        kwargs = dict(
            model=self.model,
            file=("audio.wav", to_wav_bytes(audio, sr)),
            prompt=(prompt or None),
        )
        if self.language:  # omit -> the API auto-detects the language
            kwargs["language"] = self.language
        resp = client.audio.transcriptions.create(**kwargs)
        return (getattr(resp, "text", "") or "").strip()
