"""Claude Haiku 4.5 formatter — the faithful-cleanup default.

Best in class at literal instruction-following / not adding content, which is
exactly what the faithful-cleanup contract needs. SDK import is lazy.
"""
import logging

log = logging.getLogger("murmur.formatter.anthropic")


class AnthropicFormatter:
    def __init__(self, api_key, model="claude-haiku-4-5-20251001", max_tokens=1024):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    def _ensure(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def complete(self, system, user):
        client = self._ensure()
        resp = client.messages.create(
            model=self.model,
            system=system,
            max_tokens=self.max_tokens,
            temperature=0,
            messages=[{"role": "user", "content": user}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()
