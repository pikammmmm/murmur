"""Built-in technical vocabulary: common program/brand words the recognizer
should nail out of the box (GitHub, Discord, autostart, Roblox, ...), fed to the
STT bias — but deliberately kept OUT of the phonetic auto-corrector so it can't
over-correct common English toward a tech term."""
from murmur_sidecar import vocab
from murmur_sidecar.app import App


def _app(dict_terms):
    # _rebuild() (called in __init__) only touches dict_terms + entries, so the
    # mic/network/model collaborators can be inert here.
    return App(
        recorder=None, transcriber=None, fallback=None, formatter=None,
        type_text=lambda t: None, detect=lambda: ("generic", "", ""),
        dict_terms=dict_terms, entries=[], use_threads=False,
    )


def test_base_vocab_has_the_common_program_words():
    low = {t.lower() for t in vocab.BASE_VOCAB}
    for w in ("autostart", "github", "discord", "roblox"):
        assert w in low


def test_for_bias_prioritises_user_terms_then_adds_base():
    terms = vocab.for_bias(["glassbar"])
    low = [t.lower() for t in terms]
    assert low[0] == "glassbar"          # the user's own term comes first
    assert "autostart" in low and "roblox" in low
    assert len(low) == len(set(low))     # deduped


def test_for_bias_dedupes_user_term_already_in_base():
    terms = vocab.for_bias(["GitHub"])
    assert sum(1 for t in terms if t.lower() == "github") == 1


def test_app_bias_prompt_includes_base_vocab_with_empty_user_dict():
    app = _app([])
    assert "autostart" in app.bias_prompt.lower()
    assert "github" in app.bias_prompt.lower()


def test_corrector_is_not_polluted_by_base_vocab():
    # Precision guard: the broad built-in vocab must NOT become auto-correction
    # targets (else fuzzy matching could rewrite correct words). With no user
    # dictionary the corrector vocab stays empty.
    app = _app([])
    assert app.corrector.vocab == []
