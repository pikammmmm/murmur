"""OpenAI-compatible chat formatter for "fast mode" (Groq / Cerebras).

Both Groq and Cerebras expose OpenAI-compatible chat endpoints, so one client
covers both via a configurable base_url. Lower latency than Haiku; needs a tight
prompt + temperature 0 to stay faithful (the contract lives in prompts.py).
"""
import logging

log = logging.getLogger("murmur.formatter.fast")


class OpenAICompatFormatter:
    def __init__(self, api_key, model, max_tokens=1024, base_url=None):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url
        self._client = None

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def complete(self, system, user):
        client = self._ensure()
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
