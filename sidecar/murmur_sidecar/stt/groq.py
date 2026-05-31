"""Groq whisper-large-v3-turbo transcriber — the cloud-primary path.

Cheapest + fastest for short push-to-talk clips. The SDK import is lazy so this
module can be imported (and the class constructed) without the groq package.
"""
import logging

log = logging.getLogger("murmur.stt.groq")


class GroqTranscriber:
    def __init__(self, api_key, model="whisper-large-v3-turbo", language="en"):
        self.api_key = api_key
        self.model = model
        self.language = language
        self._client = None

    def _ensure(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=self.api_key)
        return self._client

    def transcribe(self, audio, sr, prompt):
        from .wavutil import to_wav_bytes
        client = self._ensure()
        resp = client.audio.transcriptions.create(
            model=self.model,
            file=("audio.wav", to_wav_bytes(audio, sr)),
            language=self.language,
            prompt=(prompt or None),
            temperature=0,
        )
        return (getattr(resp, "text", "") or "").strip()
