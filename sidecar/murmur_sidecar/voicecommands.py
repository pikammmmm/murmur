"""Spoken formatting commands.

Turns dictated phrases into structure: "new paragraph" -> a blank line, "new
line" -> a line break, "new bullet" -> a list item, and "scratch that" -> a
spoken do-over (drop everything before it). Conservative on purpose — only
structural commands, which have low false-positive risk. Punctuation-by-voice
("period", "comma") is intentionally NOT handled here; the formatter adds
punctuation automatically and literal "period"/"comma" collide with real speech.

Gated by the ``voice_commands`` config flag (default on); applied after grammar,
before the formatter (which is told to preserve the line breaks).
"""
import re

# "scratch/delete/strike/ignore that" -> keep only what follows the LAST one.
_SCRATCH = re.compile(r"(?i)\b(?:scratch|delete|strike|ignore)\s+that\b[\s,.:;-]*")

# phrase -> replacement, applied after scratch. Trailing spaces/commas absorbed.
_PHRASES = [
    (re.compile(r"(?i)\s*\bnew\s+paragraph\b[\s,]*"), "\n\n"),
    (re.compile(r"(?i)\s*\b(?:new|next)\s+line\b[\s,]*"), "\n"),
    (re.compile(r"(?i)\s*\b(?:new\s+bullet(?:\s+point)?|bullet\s+point)\b[\s,]*"), "\n- "),
    (re.compile(r"(?i)\s*\b(?:new\s+number|numbered\s+item|next\s+number)\b[\s,]*"), "\n1. "),
]


def apply_voice_commands(text):
    if not text:
        return text
    out = text
    matches = list(_SCRATCH.finditer(out))
    if matches:
        out = out[matches[-1].end():]
    for pattern, repl in _PHRASES:
        out = pattern.sub(repl, out)
    # tidy whitespace around inserted breaks
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
