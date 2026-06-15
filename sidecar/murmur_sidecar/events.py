"""The stdout event channel.

One JSON object per line on stdout is the *only* thing the Rust shell parses,
so writes must be atomic across threads (the transcription worker and the stdin
loop both emit). stderr is for human/diagnostic logs; stdout is sacred.

Event schema:
  {"type": "state", "state": "loading|idle|recording|transcribing|error"}
  {"type": "transcript", "text": "..."}
  {"type": "error", "message": "..."}
"""
import json
import sys
import threading

_lock = threading.Lock()


def emit(event, stream=None):
    """Write a single JSON event line, atomic across threads.

    Writes UTF-8 *bytes* through the stream's underlying buffer when it has one.
    On Windows the stdout text layer defaults to the locale code page (cp1252),
    which can't encode non-Latin-1 text — so a transcript with Slovenian č/š/ž (or
    any non-Western script) would raise on ``write`` and be silently dropped. The
    Rust shell decodes our stdout as UTF-8, so we emit UTF-8 regardless of locale.
    Test/in-memory text streams (StringIO) have no ``buffer`` and take the text path."""
    target = stream if stream is not None else sys.stdout
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with _lock:
        try:
            buffer = getattr(target, "buffer", None)
            if buffer is not None:
                buffer.write(line.encode("utf-8"))
                buffer.flush()
            else:
                target.write(line)
                target.flush()
        except Exception:
            # A broken stdout (parent gone) must not raise inside a worker.
            pass


def state(value, stream=None):
    emit({"type": "state", "state": value}, stream)


def transcript(text, stream=None):
    emit({"type": "transcript", "text": text}, stream)


def error(message, stream=None):
    emit({"type": "error", "message": message}, stream)


def last_raw(text, stream=None):
    """The raw (pre-correction) STT of the latest dictation — lets the UI
    prefill the 'teach' box so corrections diff against what was actually heard."""
    emit({"type": "last_raw", "text": text}, stream)


def corrections(entries, stream=None):
    """The current correction/pronunciation entries, for the settings UI."""
    emit({"type": "corrections", "entries": entries}, stream)


def preview(text, stream=None):
    """Result of running sample text through the offline transforms."""
    emit({"type": "preview", "text": text}, stream)


def engine(value, stream=None):
    """Which engine actually served the last dictation: 'cloud' (a cloud API
    succeeded) or 'local' (on-device — including when a cloud key ran out/errored
    and we fell back). Drives the overlay tint (orange = cloud, white = local)."""
    emit({"type": "engine", "value": value}, stream)
