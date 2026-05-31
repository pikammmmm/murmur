"""Content-based intent detection.

Formats by what was SAID, not just which app is focused: if you dictate something
email-shaped ("dear ...", "regards", "send an email") it becomes an email; if you
say a shopping/to-do list it becomes bullet points. High-precision cues so normal
speech is never reshaped — detection only fires on explicit signals, and the LLM
formatter does the heavy lifting for the resulting profile.
"""
import re

_EMAIL_CUES = re.compile(
    r"(?i)("
    r"^\s*dear\s+\w+"                                   # greeting at the start
    r"|\bto whom it may concern\b"
    r"|\b(kind|best|warm)\s+regards\b"
    r"|\b(regards|sincerely|cheers)\s*,"                # sign-off, comma-terminated
    r"|\byours\s+(truly|faithfully)\b"
    r"|\b(write|send|compose|draft)\s+(an?\s+|this\s+)?email\b"
    r"|\bsubject\s*:"                                   # no trailing \b (':' isn't a word char)
    r")"
)

_LIST_CUES = re.compile(
    r"(?i)\b("
    r"shopping list|grocery list|groceries"
    r"|to-?do list|task list|packing list|bucket list"
    r"|make a list|here'?s my list|add (this|these|that|it) to (my|the) list"
    r"|i need to (buy|get|grab|pick up)|we need to (buy|get|grab|pick up)"
    r")\b"
)

_LIST_PREFIX = re.compile(
    r"(?i)^(here'?s\s+|this is\s+)?(my|the)?\s*"
    r"(shopping list|grocery list|groceries|to-?do list|task list|packing list|bucket list|list)\b[\s:,.\-]*"
)
_BUY_PREFIX = re.compile(r"(?i)^(i|we)\s+need to\s+(buy|get|grab|pick up)\b[\s:,.\-]*")
_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\n)\s*", re.IGNORECASE)


def detect_profile(text, fallback="generic"):
    """Return 'email' | 'list' | fallback based on explicit content cues."""
    t = (text or "").strip()
    if not t:
        return fallback
    if _LIST_CUES.search(t):
        return "list"
    if _EMAIL_CUES.search(t):
        return "email"
    return fallback


def listify(text):
    """Best-effort offline bullet formatting for list-shaped dictation. Strips a
    leading list cue and splits enumerated items. If it can't confidently split
    (e.g. no commas/"and"), returns the text unchanged for the LLM to format."""
    t = (text or "").strip()
    body = _BUY_PREFIX.sub("", _LIST_PREFIX.sub("", t))
    items = [p.strip(" .,-") for p in _SPLIT.split(body) if p.strip(" .,-")]
    if len(items) <= 1:
        return text
    return "\n".join(f"- {item}" for item in items)
