# murmur — pronunciation & accent adaptation (Phase 2a)

**Date:** 2026-05-31
**Status:** Design (autonomous build; specifics confirmed by research sweep)
**Builds on:** Phase 1 (the sidecar pipeline + custom dictionary).

## 1. Goal

Make murmur understand the user's pronunciation/accent of **specific words** and
**improve over time** from corrections.

## 2. Hard constraint (honest scope)

We do **not** retrain Whisper's acoustic model — practical accent fine-tuning
needs training infrastructure and data we don't have, and neither Groq nor OpenAI
expose per-user acoustic adaptation. Instead we adapt at two **lexical** layers,
which is what actually delivers "understands my words and gets better":

1. **Bias (pre/in-STT):** feed the user's vocabulary + learned terms to the
   recognizer so it prefers the right spellings.
2. **Correct + learn (post-STT):** a correction layer that rewrites known
   mis-hearings, including **phonetic/fuzzy matching** so accent-driven errors on
   *known* words get fixed even before the user explicitly corrects them — and a
   learner that grows the correction set from the user's fixes.

## 3. Components (sidecar)

### 3.1 `corrections.py` — the learning store + correction engine
- **Store** (`corrections.json` in the data dir, separate from `config.json` so it
  can grow freely; written by the sidecar):
  ```jsonc
  {
    "entries": [
      { "wrong": "glass bar", "right": "glassbar", "count": 4, "source": "learned" },
      { "wrong": "low a", "right": "Luau", "count": 1, "source": "manual" }
    ]
  }
  ```
- **`apply_corrections(text, entries)`** — post-STT rewrite, in order:
  1. **Exact / phrase** substitutions (word-boundary, case-insensitive, preserves
     the original casing pattern where sensible). Multi-word "wrong" phrases first
     (longest-first) so "glass bar" → "glassbar" beats single-word rules.
  2. **Phonetic** match: for each *intended* term (dictionary + correction
     right-sides), compare each transcript token (and adjacent bigram) by phonetic
     key (Double Metaphone). On a key match that isn't already correct, replace.
  3. **Fuzzy** fallback: `rapidfuzz` ratio ≥ threshold (~85) against intended
     terms catches near-misses the phonetic key doesn't.
  - Never touches tokens that already equal an intended term; bounded so it can't
    rewrite unrelated text.
- **`learn_from_correction(raw, corrected, store)`** — diff the raw STT against the
  user's corrected text (`difflib.SequenceMatcher.get_opcodes`), extract each
  `replace` span as a `(heard → intended)` pair, upsert into the store with an
  incremented `count`. Counts drive confidence (and bias inclusion).
- **`build_bias_terms(dictionary, entries, limit)`** — intended terms + the
  right-sides of high-count corrections, deduped, capped to a sane length for the
  STT prompt/hotwords.

### 3.2 STT integration (`stt/*` + `app.py`)
- Bias terms feed the recognizer:
  - **local faster-whisper:** the dedicated biasing parameter if available
    (`hotwords`), else `initial_prompt` — confirmed by research.
  - **cloud (Groq / OpenAI):** the `prompt` parameter (already wired for the
    dictionary; extended to include learned terms).
- **Pipeline order** in `app._process`: STT → **`apply_corrections`** → formatter →
  inject. So every dictation benefits from learned fixes, and the formatter still
  only does faithful cleanup.
- The sidecar remembers `last_raw` (raw STT of the most recent dictation) to enable
  learning from a correction of "what I just said."

### 3.3 Commands (protocol additions)
- `learn <corrected text>` — diff against `last_raw`, learn substitutions, persist,
  emit a `{"type":"learned","pairs":[...]}` event.
- Manual pronunciation entries are added via the existing config/command path
  (UI), stored as `source:"manual"` corrections with high confidence.

### 3.4 UI (Rust commands + settings page)
- A **"Pronunciations & corrections"** section: list entries (wrong → right, count),
  add a manual entry, remove an entry.
- A **"Teach the last dictation"** box: shows the last raw transcript; the user
  edits it to what they meant and saves → triggers `learn`.
- New Tauri commands: `get_corrections`, `add_correction`, `remove_correction`,
  `teach_last`.

## 4. Data flow (learning loop)

```
speak ─▶ STT raw ─▶ apply_corrections ─▶ format ─▶ type   (last_raw remembered)
                                                   │
user notices a word was wrong ──▶ Settings "Teach last" ──▶ edit ──▶ save
   └─▶ learn_from_correction(last_raw, corrected) ──▶ corrections.json grows
        ─▶ next dictation: exact + phonetic + fuzzy fix applies automatically
```

## 5. Testing

- `corrections.py` pure functions (no mic/model/network):
  - exact + phrase substitution (longest-first, case handling, word boundaries).
  - phonetic match (a deliberately mis-spelled token maps to the intended term).
  - fuzzy fallback threshold behavior (matches near-miss, rejects unrelated).
  - `learn_from_correction` extracts the right `(heard→intended)` pairs from a diff
    and increments counts; idempotent re-learn bumps count not duplicates.
  - store load/save round-trip; corrupt file → empty store.
  - `build_bias_terms` dedupe + cap + high-count ordering.
- `app.py`: a learned correction is applied to the next dictation's output
  (injected text reflects the fix) — with fakes.
- Keep the faithful-cleanup guarantee: corrections only substitute known
  word/phrase pairs; they never invent content.

## 6. Privacy
All learning is local (`corrections.json` in the app data dir; gitignored like
config). Nothing about pronunciation leaves the machine beyond the normal STT
audio upload (when a cloud provider is configured).

## 7. Out of scope (later)
- Acoustic-model fine-tuning to the accent (infra-heavy).
- Auto-detecting corrections from arbitrary edits in other apps (we use an explicit
  "teach last" flow instead — reliable and private).
- Recording reference audio per word (could augment phonetic keys later).
