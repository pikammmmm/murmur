"""STT provider selection + graceful fallback.

A *Transcriber* implements ``transcribe(audio, sr, prompt) -> str`` where
``audio`` is a float32 mono numpy array at ``sr`` Hz. ``transcribe_with_fallback``
tries the primary, then the fallback, then returns "" — it never raises, so a
dead network or a missing local model can't crash a dictation.
"""
import logging

log = logging.getLogger("murmur.stt")


def transcribe_with_fallback(primary, fallback, audio, sr, prompt):
    """Try primary, then fallback. Returns ``(text, used_fallback)`` — text is "" if
    both fail (never raises). ``used_fallback`` is True when the primary raised and
    the fallback produced the result, i.e. a cloud primary 'ran out' / errored and we
    dropped to the local engine (the caller tints the overlay white for that case)."""
    try:
        return (primary.transcribe(audio, sr, prompt) or ""), False
    except Exception as exc:
        log.warning("primary STT failed: %s", exc)
    if fallback is not None and fallback is not primary:
        try:
            return (fallback.transcribe(audio, sr, prompt) or ""), True
        except Exception as exc:
            log.error("fallback STT failed: %s", exc)
    return "", True


def norm_lang(value):
    """Config language -> model language. 'auto' or empty -> None, which tells
    Whisper to auto-detect the spoken language (handy for multilingual users)."""
    v = (value or "").strip().lower()
    return None if v in ("", "auto") else v


def _build(provider, cfg, keys):
    """Construct one transcriber, or None if a cloud key is missing."""
    stt = cfg["stt"]
    lang = norm_lang(stt.get("language"))
    if provider == "local":
        from .local import LocalTranscriber
        return LocalTranscriber(
            stt.get("local_model", "base"),
            lang,
            stt.get("beam_size", 5),
            stt.get("vad_filter", True),
        )
    if provider == "gpu":
        # GPU Whisper via DirectML (AMD/any DX12 GPU). Lazy: needs torch-directml
        # + openai-whisper in the venv; if absent it raises at transcribe time and
        # transcribe_with_fallback drops to the local CPU transcriber.
        from .directml import DirectMLTranscriber
        return DirectMLTranscriber(
            stt.get("gpu_model", "large-v3"),
            lang,
            stt.get("beam_size", 5),
        )
    if provider == "groq":
        if not keys.get("groq"):
            return None
        from .groq import GroqTranscriber
        return GroqTranscriber(keys["groq"], stt.get("groq_model", "whisper-large-v3-turbo"), lang)
    if provider == "openai":
        if not keys.get("openai"):
            return None
        from .openai import OpenAITranscriber
        return OpenAITranscriber(keys["openai"], stt.get("openai_model", "gpt-4o-transcribe"), lang)
    return None


def make_transcriber(cfg, keys):
    """Return ``(primary, fallback)``.

    A cloud primary always gets a local fallback. If a cloud provider is chosen
    but its key is missing, the primary *becomes* local (and there's no fallback).
    ``accuracy_mode`` forces the OpenAI gpt-4o-transcribe path.
    """
    stt = cfg["stt"]
    provider = "openai" if stt.get("accuracy_mode") else stt["provider"]
    primary = _build(provider, cfg, keys)
    if primary is None:                       # cloud chosen but no key present
        return (_build("local", cfg, keys), None)
    if provider == "local":
        return (primary, None)
    return (primary, _build("local", cfg, keys))
