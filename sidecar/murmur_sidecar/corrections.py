"""Pronunciation / accent adaptation: learn the user's word corrections and
auto-fix the recognizer's mistakes.

Two layers, both lexical (we can't retrain Whisper's acoustics):
  * exact learned substitutions ("glass bar" -> "glassbar") — deterministic,
    grown from the user's corrections via a difflib diff;
  * phonetic + fuzzy correction toward the known vocabulary, so accent-driven
    mishearings of *known* terms get fixed even before they're explicitly taught.

Precision is prioritized over recall: a missed correction is harmless (the user
can teach it), but a wrong correction mangles their words — so the phonetic stage
requires a shared Double-Metaphone key of length >=2 AND a jaro-winkler gate, and
the fuzzy stage uses fuzz.ratio (not WRatio, which rewards substrings).

Intended for *rare* custom-vocabulary terms (names, jargon). Adding very common
words to the dictionary can cause over-correction.
"""
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

import jellyfish
from metaphone import doublemetaphone
from rapidfuzz import fuzz, process

log = logging.getLogger("murmur.corrections")

DM_MIN_KEY = 2       # ignore 1-char phonetic keys (too loose, e.g. "L")
JW_GATE = 0.80       # jaro-winkler floor for a phonetic-key candidate
FUZZY_CUTOFF = 85    # fuzz.ratio floor for the fuzzy fallback
MIN_TOKEN_LEN = 3    # don't try to correct 1-2 char tokens

_LETTERS = re.compile(r"[^A-Za-z]")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']*")


# ---------- store ----------
def load_store(path):
    """Return the list of correction entries. Missing/corrupt -> []."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    entries = data.get("entries", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("wrong") and e.get("right")]


def save_store(path, entries):
    Path(path).write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def upsert(entries, wrong, right, source="learned"):
    """Add a (wrong->right) entry or bump its count if it already exists."""
    wrong_n, right_n = wrong.strip(), right.strip()
    for e in entries:
        if e["wrong"].lower() == wrong_n.lower() and e["right"] == right_n:
            e["count"] = e.get("count", 0) + 1
            return entries
    entries.append({"wrong": wrong_n, "right": right_n, "count": 1, "source": source})
    return entries


# ---------- learning ----------
def learn_substitutions(raw, corrected):
    """Diff raw STT vs the user's corrected text -> [(heard, intended), ...]."""
    a, b = raw.split(), corrected.split()
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        heard_toks, intended_toks = a[i1:i2], b[j1:j2]
        if len(heard_toks) == len(intended_toks):
            # 1:1 alignment -> learn per word (reusable beyond this exact phrase)
            spans = zip(heard_toks, intended_toks)
        else:
            # differing counts (e.g. "glass bar" -> "glassbar") -> one phrase pair
            spans = [(" ".join(heard_toks), " ".join(intended_toks))]
        for heard, intended in spans:
            if heard and intended and heard.lower() != intended.lower():
                pairs.append((heard, intended))
    return pairs


def learn_from_correction(entries, raw, corrected, source="learned"):
    """Learn substitutions from a correction and persist them into entries."""
    pairs = learn_substitutions(raw, corrected)
    for heard, intended in pairs:
        entries = upsert(entries, heard, intended, source)
    return entries, pairs


# ---------- biasing ----------
def build_bias_terms(dictionary, entries, limit=80):
    """Terms to feed the recognizer: dictionary first, then high-count
    correction targets. Deduped (case-insensitive) and capped."""
    terms, seen = [], set()

    def add(t):
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            terms.append(t)

    for t in dictionary or []:
        add(t)
    for e in sorted(entries or [], key=lambda e: -e.get("count", 0)):
        add(e.get("right"))
    return terms[:limit]


# Per-language priming lead-ins for the STT prompt. Whisper conditions on the
# prompt's language and orthography, so a short natural fragment in the target
# language nudges auto-detect toward it AND makes the model emit the right
# accented characters. For Slovenian this fragment deliberately carries all three
# šumniki (č, š, ž) plus everyday accented words — without it, a clip is often
# mis-detected as a neighbouring Slavic language or has its diacritics dropped.
# Add other languages here as needed; an unlisted language gets no priming.
PRIMING = {
    "sl": (
        "Posnetek v slovenščini. Pogoste besede: računalnik, številka, "
        "želim, čeprav, prošnja, mogoče, današnji, hvala."
    ),
}


def build_bias_string(terms, language=None):
    """The Whisper/cloud ``prompt`` string. ``language`` (e.g. ``"sl"``) prepends a
    priming lead-in in that language so recognition leans toward it; the proper-noun
    bias terms follow so they're still spelled correctly even mid-sentence."""
    lead = PRIMING.get((language or "").strip().lower())
    body = ", ".join(terms)
    if not lead:
        return body
    return lead + " " + body if body else lead


# ---------- correction engine ----------
def _dm_keys(word):
    primary, secondary = doublemetaphone(_LETTERS.sub("", word).upper())
    return {k for k in (primary, secondary) if k}


def _match_case(original, replacement):
    """If the original was capitalized, capitalize the replacement."""
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_phrase(text, wrong, right):
    pattern = re.compile(r"\b" + re.escape(wrong.strip()) + r"\b", re.IGNORECASE)
    return pattern.sub(lambda m: _match_case(m.group(0), right), text)


class Corrector:
    def __init__(self, dictionary=None, entries=None, jw_gate=JW_GATE, fuzzy_cutoff=FUZZY_CUTOFF):
        # Longest "wrong" phrases first so multi-word rules win over single words.
        self.entries = sorted(entries or [], key=lambda e: -len(e.get("wrong", "").split()))
        self.jw_gate = jw_gate
        self.fuzzy_cutoff = fuzzy_cutoff
        # Fuzzy/phonetic targets: the dictionary plus entry targets — EXCEPT
        # entries flagged ``fuzzy: False`` (deterministic-only casing fixes whose
        # short, collision-prone targets like RNG/LARP/FPS would otherwise
        # over-correct ordinary words, e.g. "ring"->"RNG", "lark"->"LARP"). Those
        # still apply as exact whole-word substitutions in correct() step 1.
        vocab, seen = [], set()
        for t in list(dictionary or []) + [e["right"] for e in (entries or []) if e.get("fuzzy", True)]:
            t = (t or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                vocab.append(t)
        self.vocab = vocab
        self._vocab_lower = {v.lower() for v in vocab}
        self._phon_index = {}
        for v in vocab:
            for k in _dm_keys(v):
                self._phon_index.setdefault(k, []).append(v)

    def correct(self, text):
        if not text or not text.strip():
            return text
        for e in self.entries:  # 1) deterministic learned/manual substitutions
            text = _replace_phrase(text, e["wrong"], e["right"])
        if not self.vocab:
            return text
        return _TOKEN_RE.sub(lambda m: self._correct_token(m.group(0)), text)  # 2) phonetic+fuzzy

    def _correct_token(self, core):
        if len(core) < MIN_TOKEN_LEN or core.lower() in self._vocab_lower:
            return core
        # Stage 1: shared Double-Metaphone key (len>=2) + jaro-winkler gate.
        candidates = set()
        for k in _dm_keys(core):
            if len(k) >= DM_MIN_KEY:
                candidates.update(self._phon_index.get(k, []))
        best, best_jw = None, 0.0
        for v in candidates:
            jw = jellyfish.jaro_winkler_similarity(core.lower(), v.lower())
            if jw >= self.jw_gate and jw > best_jw:
                best, best_jw = v, jw
        if best:
            return _match_case(core, best)
        # Stage 2: fuzzy fallback on pure edit-distance ratio (no substring reward).
        match = process.extractOne(
            core, self.vocab, scorer=fuzz.ratio, processor=str.lower, score_cutoff=self.fuzzy_cutoff
        )
        return _match_case(core, match[0]) if match else core
