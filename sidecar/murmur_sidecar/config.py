"""Config loading for the sidecar.

The Rust/Tauri shell *owns* writing ``config.json``; the sidecar only reads it.
We deep-merge the on-disk file over a complete set of defaults so a partial or
older config never crashes the sidecar — every key the code reads is guaranteed
to exist. API keys are resolved from the config first, then the environment.
"""
import json
import os
from pathlib import Path

# A complete default config. Mirrors docs/superpowers/specs §7. Every key the
# sidecar reads MUST appear here so deep-merge guarantees its presence.
DEFAULTS = {
    "hotkey": {"key": "backslash", "side": "either", "hold_threshold_ms": 350},
    "stt": {
        "provider": "groq",            # groq | openai | gpu | local
        "accuracy_mode": False,        # true -> use the openai gpt-4o-transcribe path
        "language": "en",              # decode language; "auto" lets Whisper detect
        "bias_language": "",           # prime the STT prompt toward this language under auto-detect (e.g. "sl")
        "groq_model": "whisper-large-v3-turbo",
        "openai_model": "gpt-4o-transcribe",
        "local_model": "small",        # faster-whisper size for the offline (CPU) fallback (small > base accuracy)
        "gpu_model": "turbo",          # openai-whisper model for the "gpu" provider (DirectML); turbo = large-v3-turbo (~2x faster, ~large accuracy)
        "beam_size": 3,                # 3 matched 5's accuracy at ~27% less GPU time
        "vad_filter": True,
    },
    "formatter": {
        "provider": "anthropic",       # anthropic | groq | cerebras | off
        "model": "claude-haiku-4-5-20251001",
        "mode": "grammar",             # grammar | faithful (verbatim) | (provider off = raw)
        "max_output_tokens": 1024,
    },
    "keys": {"groq": None, "openai": None, "anthropic": None, "cerebras": None},
    "max_recording_seconds": 60,
    "voice_commands": True,        # spoken "new paragraph"/"scratch that"/etc.
    "audio_cues": True,            # beep on record start/stop/error
    "inject_mode": "paste",        # paste (clipboard + Ctrl+V, all-at-once) | type (per-char)
    "inject_char_delay_ms": 6,     # type-mode per-key pacing; stops dropped chars/spaces (0 = unpaced)
    "save_history": True,          # keep a local dictation history + stats
    "overlay": True,               # floating recording-indicator blob (Rust-side)
    "dictionary": [],
    "profiles": {},                    # optional per-exe profile overrides
}

# Provider name -> environment variable fallback for its API key.
ENV_KEYS = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}


def _deep_merge(base, over):
    """Recursively merge ``over`` onto a copy of ``base`` (dicts only recurse)."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path):
    """Return the merged config dict. Missing/invalid file -> pure defaults."""
    path = Path(path)
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy so DEFAULTS stays pristine
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = _deep_merge(cfg, data)
        except (ValueError, OSError):
            # Corrupt or unreadable config should never take the tool down;
            # fall back to defaults. The Rust shell surfaces config errors.
            pass
    return cfg


def resolve_keys(cfg):
    """API keys: config value wins, else the matching environment variable."""
    keys = dict(cfg.get("keys") or {})
    for name, env_var in ENV_KEYS.items():
        if not keys.get(name):
            env_val = os.environ.get(env_var)
            if env_val:
                keys[name] = env_val
    return keys
