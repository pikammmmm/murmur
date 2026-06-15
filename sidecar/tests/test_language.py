"""Language handling: Slovenian + auto-detect support, and not forcing English."""
from murmur_sidecar.app import App
from murmur_sidecar.formatter import prompts
from murmur_sidecar.stt.base import make_transcriber, norm_lang


def _app(language, bias_language="", dict_terms=None):
    return App(
        recorder=None, transcriber=None, fallback=None, formatter=None,
        type_text=lambda t: None, detect=lambda: ("generic", "", ""),
        dict_terms=dict_terms or [], entries=[], format_mode="grammar",
        language=language, bias_language=bias_language, use_threads=False,
    )


def test_norm_lang_maps_auto_and_blank_to_none():
    assert norm_lang("auto") is None
    assert norm_lang("") is None
    assert norm_lang(None) is None
    assert norm_lang("sl") == "sl"
    assert norm_lang("EN") == "en"


def test_slovenian_flows_to_the_transcriber():
    primary, _ = make_transcriber({"stt": {"provider": "local", "language": "sl"}}, keys={})
    assert primary.language == "sl"


def test_auto_detect_becomes_none_for_the_model():
    primary, _ = make_transcriber({"stt": {"provider": "local", "language": "auto"}}, keys={})
    assert primary.language is None


def test_grammar_guard_is_language_agnostic():
    assert "standard English" not in prompts.GRAMMAR_GUARD
    assert "same language" in prompts.GRAMMAR_GUARD.lower()


def test_prompt_processes_any_language_and_never_refuses():
    # A cloud formatter (Haiku/Llama) will otherwise sometimes REFUSE a Slovenian
    # transcript with an English "I can only process English" meta-message — which
    # would then get typed into the user's field. The system prompt must explicitly
    # license non-English input and forbid refusing/translating, in BOTH modes.
    for mode in ("faithful", "grammar"):
        system, _ = prompts.build_messages("kratko besedilo", "generic", [], mode)
        low = system.lower()
        assert "any language" in low      # input may be non-English
        assert "never refuse" in low      # must not bail with a meta-message
        assert "translate" in low         # translation is addressed (forbidden)


def test_offline_english_rules_skip_for_explicit_non_english():
    # English grammar fix would turn "he don't know" -> "he doesn't know"...
    assert _app("en").preview("he don't know") == "he doesn't know"
    assert _app("auto").preview("he don't know") == "he doesn't know"  # harmless under auto
    # ...but it must NOT be applied when the language is explicitly Slovenian.
    assert _app("sl").preview("he don't know") == "he don't know"


# --- STT prompt priming: nudge Whisper toward Slovenian -------------------
def test_bias_language_primes_the_stt_prompt_in_auto_mode():
    # User dictates mostly Slovenian but keeps auto-detect on for English clips:
    # the decode language stays "auto" while the recognizer is primed Slovenian.
    app = _app("auto", bias_language="sl", dict_terms=["GitHub"])
    assert "GitHub" in app.bias_prompt                       # proper nouns still bias
    assert any(ch in app.bias_prompt for ch in "čšž")         # primed for Slovenian


def test_explicit_slovenian_language_also_primes():
    app = _app("sl")
    assert any(ch in app.bias_prompt for ch in "čšž")


def test_plain_english_auto_mode_is_not_primed():
    app = _app("auto", dict_terms=["GitHub"])
    assert "sloven" not in app.bias_prompt.lower()
    assert "GitHub" in app.bias_prompt
