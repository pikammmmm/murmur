import pytest

from murmur_sidecar.grammar import fix_grammar


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("I could of gone", "I could have gone"),
        ("she would of helped", "she would have helped"),
        ("he don't know", "he doesn't know"),
        ("it don't matter", "it doesn't matter"),
        ("you doesn't care", "you don't care"),
        ("we doesn't agree", "we don't agree"),
        ("I didn't went", "I didn't go"),
        ("they didn't saw it", "they didn't see it"),
        ("did not came", "did not come"),
        ("he didn't made it", "he didn't make it"),
    ],
)
def test_fixes_clear_errors(raw, expected):
    assert fix_grammar(raw) == expected


@pytest.mark.parametrize(
    "correct",
    [
        "the table is made of wood",      # "of" not after a modal
        "that kind of thing",
        "May of 2024 was warm",           # month May, not the modal
        "does he have time",              # "he have" correct after does
        "he doesn't know",                # already correct
        "I don't care",                   # already correct
        "did you go",                     # "you" is not an irregular past
        "I didn't go",                    # "go" is already the base form
        "we didn't need it",              # regular verb, left to the LLM
        "it will not be done",            # already correct
    ],
)
def test_leaves_correct_text_unchanged(correct):
    assert fix_grammar(correct) == correct


def test_empty_is_safe():
    assert fix_grammar("") == ""
    assert fix_grammar(None) is None
