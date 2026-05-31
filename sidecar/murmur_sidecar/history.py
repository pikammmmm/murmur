"""Local dictation history + cumulative usage stats.

Everything is local (next to config.json, gitignored). ``history.jsonl`` keeps the
most recent dictations (capped); ``stats.json`` holds lifetime counters. Used by
the settings "History & stats" view. Nothing leaves the machine.
"""
import json
import time
from pathlib import Path


def _read_jsonl(path, limit=None):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out[-limit:] if limit else out


def append_history(path, entry, cap=500):
    entries = _read_jsonl(path)
    entries.append(entry)
    entries = entries[-cap:]
    Path(path).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8"
    )
    return entries


def load_history(path, limit=50):
    """Most recent first."""
    return list(reversed(_read_jsonl(path, limit)))


def load_stats(path):
    p = Path(path)
    if not p.exists():
        return {"dictations": 0, "words": 0, "first_ts": None, "last_ts": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (ValueError, OSError):
        pass
    return {"dictations": 0, "words": 0, "first_ts": None, "last_ts": None}


def update_stats(path, words, now=None):
    now = now if now is not None else time.time()
    s = load_stats(path)
    s["dictations"] = s.get("dictations", 0) + 1
    s["words"] = s.get("words", 0) + words
    if not s.get("first_ts"):
        s["first_ts"] = now
    s["last_ts"] = now
    Path(path).write_text(json.dumps(s), encoding="utf-8")
    return s


def est_minutes_saved(words, type_wpm=40, speak_wpm=150):
    """Rough time saved vs. typing: type-time minus speak-time for `words`."""
    if words <= 0:
        return 0.0
    return max(0.0, words * (1.0 / type_wpm - 1.0 / speak_wpm))


def clear(history_path, stats_path):
    for p in (history_path, stats_path):
        try:
            Path(p).unlink()
        except OSError:
            pass
