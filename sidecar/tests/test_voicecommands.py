import pytest

from murmur_sidecar.voicecommands import apply_voice_commands


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("first point new paragraph second point", "first point\n\nsecond point"),
        ("line one new line line two", "line one\nline two"),
        ("line one next line line two", "line one\nline two"),
        ("buy milk new bullet buy eggs", "buy milk\n- buy eggs"),
        ("buy milk bullet point buy eggs", "buy milk\n- buy eggs"),
        ("send it tomorrow scratch that send it today", "send it today"),
        ("a delete that b delete that c", "c"),  # last scratch wins
    ],
)
def test_commands(raw, expected):
    assert apply_voice_commands(raw) == expected


@pytest.mark.parametrize(
    "plain",
    [
        "hello world",
        "the new design looks great",   # "new" without paragraph/line/bullet
        "draw a line under it",         # "line" without new/next
    ],
)
def test_leaves_plain_text_alone(plain):
    assert apply_voice_commands(plain) == plain


def test_empty():
    assert apply_voice_commands("") == ""
    assert apply_voice_commands(None) is None
