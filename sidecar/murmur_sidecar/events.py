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
    """Write a single JSON event line, atomic across threads."""
    target = stream if stream is not None else sys.stdout
    line = json.dumps(event, ensure_ascii=False)
    with _lock:
        try:
            target.write(line + "\n")
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
