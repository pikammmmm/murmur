from murmur_sidecar import corrections as C


# ---------- learning ----------
def test_learn_substitutions_extracts_replace_pairs():
    pairs = C.learn_substitutions("i sink zis is wery gud", "i think this is very good")
    assert ("sink", "think") in pairs
    assert ("wery", "very") in pairs
    assert ("gud", "good") in pairs


def test_upsert_increments_count_not_duplicates():
    entries = []
    entries = C.upsert(entries, "wery", "very")
    entries = C.upsert(entries, "wery", "very")
    matches = [e for e in entries if e["wrong"] == "wery"]
    assert len(matches) == 1
    assert matches[0]["count"] == 2 and matches[0]["right"] == "very"


def test_learn_from_correction_persists_pairs():
    entries, pairs = C.learn_from_correction([], "call me pika", "call me Pikammmmm")
    assert ("pika", "Pikammmmm") in pairs
    assert any(e["wrong"] == "pika" and e["right"] == "Pikammmmm" for e in entries)


# ---------- store ----------
def test_store_roundtrip(tmp_path):
    p = tmp_path / "corrections.json"
    entries = [{"wrong": "glass bar", "right": "glassbar", "count": 3, "source": "learned"}]
    C.save_store(p, entries)
    assert C.load_store(p) == entries


def test_store_missing_or_corrupt_returns_empty(tmp_path):
    assert C.load_store(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert C.load_store(bad) == []


# ---------- biasing ----------
def test_build_bias_terms_dedup_and_high_count_first():
    entries = [{"wrong": "x", "right": "Luau", "count": 9, "source": "learned"}]
    terms = C.build_bias_terms(["Rojo", "rojo"], entries, limit=10)
    assert "Rojo" in terms and "Luau" in terms
    assert len(terms) == len(set(t.lower() for t in terms))  # case-insensitive dedupe


# ---------- biasing: language priming ----------
def test_build_bias_string_plain_join_without_language():
    assert C.build_bias_string(["GitHub", "Rust"]) == "GitHub, Rust"


def test_build_bias_string_slovenian_primes_with_diacritics():
    s = C.build_bias_string(["GitHub"], "sl")
    assert "GitHub" in s                       # proper nouns still bias the recognizer
    assert "sloven" in s.lower()               # prompt establishes Slovenian context
    assert any(ch in s for ch in "čšž")         # carries the šumniki so Whisper emits them


def test_build_bias_string_unknown_language_falls_back_to_plain_join():
    # No priming text defined for English -> behaves exactly like the bare join.
    assert C.build_bias_string(["GitHub"], "en") == "GitHub"


def test_build_bias_string_slovenian_priming_without_terms_has_no_trailing_space():
    s = C.build_bias_string([], "sl")
    assert "sloven" in s.lower()
    assert s == s.strip()                       # no dangling separator when term list is empty


# ---------- corrector: exact ----------
def test_corrector_exact_phrase_substitution():
    entries = [{"wrong": "glass bar", "right": "glassbar", "count": 3, "source": "learned"}]
    c = C.Corrector(dictionary=[], entries=entries)
    assert c.correct("open the glass bar app") == "open the glassbar app"


def test_corrector_preserves_following_punctuation():
    entries = [{"wrong": "low a", "right": "Luau", "count": 1, "source": "manual"}]
    c = C.Corrector(dictionary=[], entries=entries)
    assert c.correct("i love low a.") == "i love Luau."


def test_corrector_capitalizes_at_sentence_start():
    entries = [{"wrong": "glass bar", "right": "glassbar", "count": 1, "source": "manual"}]
    c = C.Corrector(dictionary=[], entries=entries)
    assert c.correct("Glass bar is open") == "Glassbar is open"


# ---------- corrector: phonetic + fuzzy (confirmed against the libs) ----------
def test_corrector_phonetic_fixes_known_vocab():
    c = C.Corrector(dictionary=["very"], entries=[])
    assert c.correct("that is wery nice") == "that is very nice"


def test_corrector_fuzzy_fixes_close_spelling():
    c = C.Corrector(dictionary=["schedule"], entries=[])
    assert c.correct("check the shedule") == "check the schedule"


# ---------- corrector: precision (must NOT over-correct) ----------
def test_corrector_rejects_weak_single_key_match():
    # "loww" shares only the 1-char DM key "L" with "Luau" -> must stay put.
    c = C.Corrector(dictionary=["Luau"], entries=[])
    assert c.correct("loww battery") == "loww battery"


def test_corrector_leaves_unrelated_words_alone():
    c = C.Corrector(dictionary=["glassbar", "Luau"], entries=[])
    assert c.correct("the weather is nice today") == "the weather is nice today"


def test_corrector_does_not_touch_already_correct_vocab():
    c = C.Corrector(dictionary=["glassbar"], entries=[])
    assert c.correct("open glassbar now") == "open glassbar now"


def test_corrector_noop_without_vocab_or_entries():
    c = C.Corrector(dictionary=[], entries=[])
    assert c.correct("nothing to do here") == "nothing to do here"
