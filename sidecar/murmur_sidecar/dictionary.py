"""Custom-vocabulary biasing.

The same user terms (names, jargon: "glassbar", "Rojo", "Luau", ...) feed two
places: the STT prompt — which biases the recognizer toward the correct
spelling — and a formatter instruction, so the cleanup pass never "corrects"
them away. This is the lever for the "extremely accurate to my words" goal.
"""


def _clean(terms):
    return [t.strip() for t in (terms or []) if t and t.strip()]


def build_stt_prompt(terms):
    """A Whisper/cloud ``prompt`` string that biases recognition toward terms."""
    terms = _clean(terms)
    if not terms:
        return ""
    return "Vocabulary: " + ", ".join(terms) + "."


def protect_clause(terms):
    """A formatter instruction telling it to keep these terms verbatim."""
    terms = _clean(terms)
    if not terms:
        return ""
    return "Preserve these terms exactly, including their capitalization: " + ", ".join(terms) + "."
