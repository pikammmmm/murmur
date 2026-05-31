"""Conservative, offline rule-based grammar fixes.

HIGH-PRECISION ONLY: each rule fires solely where the input is unambiguously
wrong, so already-correct text is never altered (honoring the verbatim spirit).
Broad, context-dependent grammar correction is the LLM formatter's job in
"grammar" mode; this pass is the offline safety net that runs before it.

Deliberately NOT handled here (left to the LLM, too error-prone for regex):
regular -ed past after did/didn't, a/an, "may of" (collides with the month May),
"he have"->"has" (correct after do/does/did), and anything needing real parsing.
"""
import re

# Common irregular past -> base form, for "did/didn't <past>" -> "<base>".
IRREGULARS = {
    "went": "go", "saw": "see", "came": "come", "gave": "give", "took": "take",
    "got": "get", "knew": "know", "made": "make", "said": "say", "found": "find",
    "thought": "think", "told": "tell", "became": "become", "left": "leave",
    "felt": "feel", "brought": "bring", "began": "begin", "kept": "keep",
    "held": "hold", "wrote": "write", "ran": "run", "ate": "eat", "drank": "drink",
    "drove": "drive", "broke": "break", "spoke": "speak", "chose": "choose",
    "forgot": "forget", "sold": "sell", "paid": "pay", "met": "meet", "sent": "send",
    "built": "build", "taught": "teach", "caught": "catch", "bought": "buy",
    "fought": "fight", "lost": "lose", "won": "win", "did": "do", "had": "have",
}

_MODALS = r"(could|would|should|must|might)"  # NOT "may" — collides with month
_SIMPLE_RULES = [
    # modal + "of" -> modal + "have"  ("could of" -> "could have")
    (re.compile(rf"\b{_MODALS}\s+of\b", re.IGNORECASE), r"\1 have"),
    # 3rd-person-singular + "don't" -> "doesn't"
    (re.compile(r"\b(he|she|it|this|that)\s+don't\b", re.IGNORECASE), r"\1 doesn't"),
    # non-3rd-person + "doesn't" -> "don't"
    (re.compile(r"\b(i|you|we|they)\s+doesn't\b", re.IGNORECASE), r"\1 don't"),
]

# "did" / "didn't" / "did not" followed immediately by an irregular PAST form.
_DID = re.compile(r"\b(did(?:n't| not)?)\s+([A-Za-z]+)\b", re.IGNORECASE)


def _fix_did(m):
    head, verb = m.group(1), m.group(2)
    base = IRREGULARS.get(verb.lower())
    return f"{head} {base}" if base else m.group(0)


def fix_grammar(text):
    """Apply the high-precision rule set. Returns text unchanged if no rule fires."""
    if not text:
        return text
    out = text
    for pattern, repl in _SIMPLE_RULES:
        out = pattern.sub(repl, out)
    out = _DID.sub(_fix_did, out)
    return out
