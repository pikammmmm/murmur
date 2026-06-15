"""Formatter prompt composition.

Two modes shape the system prompt:
  * "faithful" — clean only (punctuation, caps, fillers, self-corrections);
    NEVER change the speaker's words or meaning.
  * "grammar"  — faithful cleanup PLUS grammatical-error correction to standard
    English (agreement, tense, did/didn't, don't/doesn't, double negatives,
    malformed constructions), while still not paraphrasing already-correct text.

Either way the output is only the cleaned transcript — never an answer or an
addition. The offline rule pass in grammar.py handles a few high-precision cases
before this; the LLM does the broad work.
"""
BASE = (
    "You are a transcription formatter. You receive a raw speech-to-text "
    "transcript and a context label. Return ONLY the cleaned transcript, with no "
    "preamble, commentary, or surrounding quotation marks. "
    "The transcript may be in ANY language (for example Slovenian, Croatian, "
    "German, or English) and may mix languages. Always process it and clean it IN "
    "ITS ORIGINAL LANGUAGE: never translate it, never refuse, and never add any "
    "note, apology, or explanation about the language or about your ability to "
    "handle it. Output only the cleaned transcript text itself, even if you are "
    "unsure of the language."
)

CLEANUP = (
    " You may fix punctuation, capitalization, and spacing; remove filler words "
    "(um, uh, like, you know) and false starts; resolve explicit self-corrections "
    "(if the speaker says 'actually', 'I mean', 'no wait' and then restates, keep "
    "only the final version); and apply formatting appropriate to the context label. "
    "Preserve any line breaks and list bullets already present in the text."
)

FAITHFUL_GUARD = (
    " You MUST NOT paraphrase, reword, summarize, translate, add information, "
    "answer questions, or change the speaker's meaning or vocabulary. Preserve the "
    "speaker's own words; when in doubt, keep the original wording."
)

GRAMMAR_GUARD = (
    " Also correct grammatical errors — subject-verb agreement, verb tense and "
    "auxiliaries, double negatives, and malformed constructions (for example, in "
    "English, 'he don't know' -> 'he doesn't know', 'I didn't went' -> 'I didn't "
    "go') — to standard, grammatical form IN THE SAME LANGUAGE AS THE TRANSCRIPT. "
    "NEVER translate to another language. Preserve the speaker's intended meaning; "
    "do NOT paraphrase or reword text that is already correct, add information, or "
    "answer questions."
)

PROFILE_GUIDANCE = {
    "email": (
        "Context: an email being composed. Use sentence case and proper "
        "paragraphs; keep a greeting/sign-off only if the speaker clearly "
        "dictated one."
    ),
    "chat": (
        "Context: a casual chat/instant message. Keep it casual and brief; do not "
        "impose formal capitalization or structure beyond basic readability."
    ),
    "code": (
        "Context: a code editor. Keep it terse. Preserve identifiers, "
        "camelCase/snake_case, and technical terms exactly; do not add prose."
    ),
    "notes": (
        "Context: notes. Produce clean sentences and paragraphs without adding "
        "structure the speaker did not dictate."
    ),
    "list": (
        "Context: a list (e.g. shopping or to-do). Format as a bullet list with one "
        "item per line starting with '- '. Split the items the speaker enumerated "
        "(commas, 'and', or pauses); drop any leading phrase like 'shopping list' or "
        "'I need to buy'. Keep items short; add no prose."
    ),
    "generic": "Context: general text. Produce clean, well-punctuated sentences.",
}


def build_messages(raw, profile, dict_terms, mode="faithful"):
    """Return ``(system, user)`` for the formatter call, shaped by mode."""
    from ..dictionary import protect_clause

    guard = GRAMMAR_GUARD if mode == "grammar" else FAITHFUL_GUARD
    guidance = PROFILE_GUIDANCE.get(profile, PROFILE_GUIDANCE["generic"])
    system = BASE + CLEANUP + guard + "\n" + guidance
    protect = protect_clause(dict_terms)
    if protect:
        system += "\n" + protect
    return system, raw
