"""Typed injection must pace keystrokes so the target window doesn't drop them.

pynput's bulk ``Controller.type(text)`` fires key events as fast as SendInput
allows; apps drop keystrokes that arrive faster than their message pump drains,
and a dropped space ("twowords") is the most visible casualty. Pacing with a
small inter-character delay fixes it while staying universal (terminals, games,
Electron — no clipboard side-effects).
"""
from murmur_sidecar.injector import make_injector, type_text


class FakeController:
    def __init__(self):
        self.typed = []

    def type(self, s):
        self.typed.append(s)


def test_paced_typing_emits_every_character_including_spaces_in_order():
    c = FakeController()
    naps = []
    type_text("a b  c", controller=c, char_delay=0.005, sleep=naps.append)
    # Every char (spaces included) typed, in order, exactly once.
    assert "".join(c.typed) == "a b  c"
    assert c.typed == list("a b  c")
    # A delay was applied between characters (>= one nap per char).
    assert len(naps) == len("a b  c")
    assert all(d == 0.005 for d in naps)


def test_zero_delay_falls_back_to_one_bulk_type_call():
    c = FakeController()
    naps = []
    type_text("hello world", controller=c, char_delay=0, sleep=naps.append)
    assert c.typed == ["hello world"]  # single bulk call, no per-char loop
    assert naps == []


def test_empty_text_types_nothing():
    c = FakeController()
    type_text("", controller=c, char_delay=0.005, sleep=lambda d: None)
    assert c.typed == []


def test_make_injector_type_mode_respects_configured_delay():
    c = FakeController()
    naps = []
    inj = make_injector("type", char_delay_ms=8)
    inj("hi", controller=c, sleep=naps.append)
    assert "".join(c.typed) == "hi"
    assert naps == [0.008, 0.008]  # 8 ms -> 0.008 s per character
