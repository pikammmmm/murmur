"""Formatter selection + the faithful-cleanup call.

A *Formatter* implements ``complete(system, user) -> str``. ``format_text`` builds
the messages, calls the model, and on ANY error or suspicious output falls back
to the raw transcript — we never lose or mangle the user's words. That fallthrough
is what lets the tool stay usable with no formatter key at all.
"""
import logging

from .prompts import build_messages

log = logging.getLogger("murmur.formatter")

# Output longer than this multiple of the input length is treated as a runaway
# generation (the model ignored "clean, don't expand") and we keep the raw text.
MAX_EXPANSION = 4


def format_text(formatter, raw, profile, dict_terms, mode="faithful"):
    """Returns ``(text, ok)``. ``ok`` is False only when the model call RAISED — e.g.
    a cloud formatter whose key ran out of credits or failed auth. The guarded
    fallbacks (empty/over-long output, or a passthrough provider) keep ok=True, since
    those aren't a cloud outage. The caller uses ``ok`` to tint the overlay."""
    if not raw or not raw.strip():
        return "", True
    system, user = build_messages(raw, profile, dict_terms, mode)
    try:
        out = formatter.complete(system, user)
    except Exception as exc:
        log.error("formatter failed, using raw transcript: %s", exc)
        return raw, False
    if not out or not out.strip():
        return raw, True
    if len(out) > MAX_EXPANSION * max(len(raw), 20):
        log.warning("formatter output suspiciously long (%d vs %d); using raw", len(out), len(raw))
        return raw, True
    return out.strip(), True


class _Passthrough:
    """Used when formatter.provider == 'off' or a required key is missing —
    returns the raw transcript unchanged so dictation still works."""

    def complete(self, system, user):
        return user


def make_formatter(cfg, keys):
    fmt = cfg["formatter"]
    provider = fmt.get("provider", "anthropic")
    if provider == "off":
        return _Passthrough()
    if provider == "anthropic":
        if not keys.get("anthropic"):
            return _Passthrough()
        from .anthropic import AnthropicFormatter
        return AnthropicFormatter(keys["anthropic"], fmt.get("model", "claude-haiku-4-5-20251001"), fmt.get("max_output_tokens", 1024))
    if provider in ("groq", "cerebras"):
        if not keys.get(provider):
            return _Passthrough()
        from .openai_compat import OpenAICompatFormatter
        base_url = "https://api.cerebras.ai/v1" if provider == "cerebras" else "https://api.groq.com/openai/v1"
        default_model = "llama-4-scout-17b-16e-instruct" if provider == "groq" else "llama-3.3-70b"
        return OpenAICompatFormatter(keys[provider], fmt.get("model") or default_model, fmt.get("max_output_tokens", 1024), base_url)
    return _Passthrough()
