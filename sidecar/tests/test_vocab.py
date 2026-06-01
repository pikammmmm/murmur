"""Built-in technical vocabulary + deterministic brand fixes.

The broad BASE_VOCAB feeds the STT bias only. FIXES are exact, deterministic
post-STT substitutions (brand mis-splits/mis-cases) that DO run in the corrector
but can't touch ordinary words."""
from murmur_sidecar import vocab
from murmur_sidecar.app import App


def _app(dict_terms=None):
    # _rebuild() (called in __init__) only touches dict_terms + entries, so the
    # mic/network/model collaborators can be inert here.
    return App(
        recorder=None, transcriber=None, fallback=None, formatter=None,
        type_text=lambda t: None, detect=lambda: ("generic", "", ""),
        dict_terms=dict_terms or [], entries=[], use_threads=False,
    )


def _correct(text):
    return _app().corrector.correct(text)


def test_base_vocab_has_the_common_program_words():
    low = {t.lower() for t in vocab.BASE_VOCAB}
    for w in ("autostart", "github", "discord", "roblox", "commit", "rebase"):
        assert w in low


def test_for_bias_prioritises_user_terms_then_adds_base():
    terms = vocab.for_bias(["glassbar"])
    low = [t.lower() for t in terms]
    assert low[0] == "glassbar"          # the user's own term comes first
    assert "autostart" in low and "roblox" in low
    assert len(low) == len(set(low))     # deduped


def test_app_bias_prompt_includes_base_vocab_with_empty_user_dict():
    app = _app()
    assert "autostart" in app.bias_prompt.lower()
    assert "github" in app.bias_prompt.lower()


def test_corrector_excludes_broad_bias_vocab():
    # Precision guard: the broad, collision-prone bias vocab must NOT become
    # fuzzy-correction targets (else "Rust"/"Discord" could rewrite
    # "trust"/"discard"). Only the deterministic FIXES' targets may appear.
    low = {v.lower() for v in _app().corrector.vocab}
    for risky in ("rust", "discord", "blender", "electron", "spotify"):
        assert risky not in low
    fix_targets = {r.lower() for _, r in vocab.FIXES}
    assert low.issubset(fix_targets)


def test_builtin_fixes_canonicalize_brand_names():
    out = _correct("i pushed to git hub then opened github and edited the java script")
    assert "GitHub" in out and "JavaScript" in out
    assert "git hub" not in out.lower()           # the split form is gone


def test_builtin_fixes_pyinstaller_and_split_variants():
    for v in ("py installer", "pi installer", "pyinstaller", "pie installer"):
        assert "PyInstaller" in _correct(f"freeze it with {v} now")


def test_builtin_fixes_preserve_punctuation_and_apostrophes():
    assert _correct("git hub, it's great. right?") == "GitHub, it's great. right?"


def test_builtin_fixes_leave_ordinary_words_untouched():
    # The fixes (and the brand targets now in vocab) must not mangle plain speech.
    s = "I will commit and merge the branch, then discard the old changes; it's ready."
    assert _correct(s) == s
