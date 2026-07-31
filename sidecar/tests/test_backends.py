"""The platform-backend seam: selection, and that the pipeline modules really
delegate to it instead of importing OS APIs themselves."""
import os
import sys

import pytest

from murmur_sidecar import context, cues, injector
from murmur_sidecar.backends import Backend, NullBackend, get_backend, set_backend


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, exe="", title="", char_delay=None):
        self.typed = []
        self.beeps = []
        self.pastes = 0
        self.clip = None
        self._exe, self._title = exe, title
        self._char_delay = char_delay

    # a controller is just something with .type()
    def make_controller(self):
        backend = self

        class _C:
            def type(self, text):
                backend.typed.append(text)

        return _C()

    def default_char_delay(self):
        return self._char_delay

    def send_paste(self):
        self.pastes += 1

    def get_clipboard(self):
        return self.clip

    def set_clipboard(self, text):
        self.clip = text

    def active_window(self):
        return (self._exe, self._title)

    def beep(self, pairs):
        self.beeps.append(list(pairs))


@pytest.fixture
def fake_backend():
    fake = FakeBackend()
    previous = set_backend(fake)
    try:
        yield fake
    finally:
        set_backend(previous)


# --- selection ---------------------------------------------------------


def test_get_backend_is_a_singleton():
    assert get_backend() is get_backend()


def test_set_backend_swaps_and_restores():
    sentinel = FakeBackend()
    previous = set_backend(sentinel)
    try:
        assert get_backend() is sentinel
    finally:
        set_backend(previous)
    assert get_backend() is previous


def test_selection_matches_this_platform():
    from murmur_sidecar.backends import _make_backend

    expected = {"linux": "linux", "win32": "win32"}.get(
        "linux" if sys.platform.startswith("linux") else sys.platform, None
    )
    made = _make_backend()
    if expected:
        assert made.name == expected
    else:
        assert made.name == "null"


def test_null_backend_is_neutral_not_raising():
    b = NullBackend()
    assert b.make_controller() is None
    assert b.get_clipboard() is None
    assert b.active_window() == ("", "")
    assert b.default_char_delay() is None
    b.send_paste()
    b.beep([(440, 10)])  # must not raise


# --- injector delegates ------------------------------------------------


def test_type_text_uses_backend_controller(fake_backend):
    injector.type_text("hey", sleep=lambda _s: None)
    assert "".join(fake_backend.typed) == "hey"


def test_type_text_is_a_noop_when_backend_cannot_type():
    class Mute(Backend):
        def make_controller(self):
            return None

    previous = set_backend(Mute())
    try:
        injector.type_text("dropped")  # must not raise
    finally:
        set_backend(previous)


def test_paste_uses_backend_clipboard_and_paste_chord(fake_backend):
    fake_backend.clip = "OLD"
    injector.paste_text("new text", sleep=lambda _s: None)
    assert fake_backend.pastes == 1
    assert fake_backend.clip == "OLD"  # prior clipboard restored


def test_backend_can_request_bulk_typing():
    # A backend whose typing helper paces itself asks for a 0 delay, which makes
    # make_injector emit one bulk call instead of one call per character.
    fake = FakeBackend(char_delay=0.0)
    previous = set_backend(fake)
    try:
        injector.make_injector("type")("hello", sleep=lambda _s: None)
    finally:
        set_backend(previous)
    assert fake.typed == ["hello"]


def test_backend_none_delay_keeps_per_character_pacing():
    fake = FakeBackend(char_delay=None)
    previous = set_backend(fake)
    try:
        injector.make_injector("type")("hi", sleep=lambda _s: None)
    finally:
        set_backend(previous)
    assert fake.typed == ["h", "i"]


def test_resolve_char_delay_prefers_explicit_config():
    assert injector.resolve_char_delay(12) == 0.012


# --- cues delegate -----------------------------------------------------


def test_cues_play_through_the_backend(fake_backend):
    cues.play(cues.START, sync=True)
    assert fake_backend.beeps == [cues.START]


def test_cue_failure_is_swallowed():
    class Boom(Backend):
        def beep(self, pairs):
            raise RuntimeError("no audio device")

    previous = set_backend(Boom())
    try:
        cues.play(cues.ERR, sync=True)  # must not raise
    finally:
        set_backend(previous)


# --- context delegates -------------------------------------------------


def test_detect_classifies_backend_window():
    fake = FakeBackend(exe="code", title="main.rs - murmur")
    previous = set_backend(fake)
    try:
        assert context.detect() == ("code", "code", "main.rs - murmur")
    finally:
        set_backend(previous)


def test_detect_degrades_to_generic_when_backend_raises():
    class Boom(Backend):
        def active_window(self):
            raise RuntimeError("no compositor support")

    previous = set_backend(Boom())
    try:
        assert context.detect() == ("generic", "", "")
    finally:
        set_backend(previous)


def test_detect_generic_when_window_unknown(fake_backend):
    assert context.detect() == ("generic", "", "")


@pytest.mark.parametrize(
    "exe,title,expected",
    [
        # Linux process / WM_CLASS names must classify like their Windows twins.
        ("thunderbird", "Inbox - Thunderbird", "email"),
        ("discord", "#general - Discord", "chat"),
        ("slack", "Slack | general", "chat"),
        ("code", "main.py - murmur - VSCodium", "code"),
        ("obsidian", "Daily Note - Obsidian", "notes"),
        ("kate", "notes.md - Kate", "notes"),
        ("firefox", "Inbox - Gmail - Mozilla Firefox", "email"),
        ("chromium", "murmur - GitHub - Chromium", "code"),
        ("firefox", "Some random article - Mozilla Firefox", "generic"),
        ("konsole", "pikammmmm@arch:~", "generic"),
    ],
)
def test_classify_linux_names(exe, title, expected):
    assert context.classify(exe, title) == expected
