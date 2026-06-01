from murmur_sidecar.injector import make_injector, paste_text, type_text


class FakeController:
    def __init__(self):
        self.typed = []

    def type(self, text):
        self.typed.append(text)


def test_types_text():
    # Default type mode paces keystrokes (one type() call per char) so the
    # target window doesn't drop any; the concatenation is the full text.
    c = FakeController()
    type_text("hello world", controller=c, sleep=lambda _s: None)
    assert "".join(c.typed) == "hello world"
    assert c.typed == list("hello world")


def test_empty_and_none_are_noops():
    c = FakeController()
    type_text("", controller=c)
    type_text(None, controller=c)
    assert c.typed == []


def test_paste_sets_clipboard_pastes_then_restores():
    state = {"clip": "OLD"}
    order = []
    paste_text(
        "hello",
        get_clipboard=lambda: state["clip"],
        set_clipboard=lambda t: state.__setitem__("clip", t),
        do_paste=lambda: order.append(("paste-while-clip-is", state["clip"])),
        sleep=lambda _s: None,
    )
    assert order == [("paste-while-clip-is", "hello")]  # our text was on the clipboard at paste
    assert state["clip"] == "OLD"                          # previous clipboard restored


def test_paste_empty_is_noop():
    calls = []
    paste_text("", do_paste=lambda: calls.append(1), sleep=lambda _s: None)
    assert calls == []


def test_make_injector_selects_mode():
    # paste mode returns the paste function directly.
    assert make_injector("paste") is paste_text
    # type (and anything else) returns a paced typer that behaves like type mode.
    for mode in ("type", "anything-else"):
        c = FakeController()
        make_injector(mode)("hi", controller=c, sleep=lambda _s: None)
        assert "".join(c.typed) == "hi"


def test_paste_restores_clipboard_even_if_paste_raises():
    import pytest

    state = {"clip": "OLD"}

    def boom():
        raise RuntimeError("paste failed")

    with pytest.raises(RuntimeError):
        paste_text(
            "hello",
            get_clipboard=lambda: state["clip"],
            set_clipboard=lambda t: state.__setitem__("clip", t),
            do_paste=boom,
            sleep=lambda _s: None,
        )
    assert state["clip"] == "OLD"  # restored despite the paste exception
