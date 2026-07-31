"""Insert the final text into the focused window.

Two modes:
  * "type"  — types it character by character (default; works in most apps).
  * "paste" — put it on the clipboard and send Ctrl+V, restoring the previous
    clipboard afterward. More reliable in apps that drop or mangle synthetic
    keystrokes (some Electron/terminal/game UIs), and much faster for long text.

The OS-specific parts (which typing mechanism, which clipboard tool) come from
the platform backend; everything here is platform-free. All of it is injectable
so the logic is unit-testable without touching a real keyboard or clipboard.
"""
import logging
import time

from .backends import get_backend

log = logging.getLogger("murmur.injector")

# Seconds between characters when typing. Bulk typing fires key events as fast
# as the injection API allows; apps drop keystrokes that arrive faster than
# their message pump drains them — and a dropped space ("twowords") is the most
# visible casualty. A few ms of pacing per character lets the target keep up.
# Small enough to stay imperceptible (~0.6s for a 100-char dictation).
#
# Backends whose typing helper paces itself (xdotool/wtype take their own
# --delay, and spawn one process per call) override this with 0 — see
# ``resolve_char_delay``.
DEFAULT_CHAR_DELAY = 0.006


def resolve_char_delay(char_delay_ms=None):
    """Seconds to wait between characters.

    Explicit config wins; otherwise the backend gets to pick (a subprocess-based
    Linux typer wants bulk, i.e. 0), and failing that we use DEFAULT_CHAR_DELAY.
    """
    if char_delay_ms is not None:
        return char_delay_ms / 1000.0
    try:
        preferred = get_backend().default_char_delay()
    except Exception:
        preferred = None
    return DEFAULT_CHAR_DELAY if preferred is None else preferred


def type_text(text, controller=None, char_delay=DEFAULT_CHAR_DELAY, sleep=None):
    if not text:
        return
    if controller is None:
        controller = get_backend().make_controller()
        if controller is None:
            log.warning("no text-injection backend available; dropping %d chars", len(text))
            return
    if not char_delay:
        controller.type(text)  # unpaced (opt-out): one bulk call
        return
    nap = sleep or time.sleep
    for ch in text:
        controller.type(ch)
        nap(char_delay)  # let the target consume this keystroke before the next


def _get_clipboard():
    try:
        return get_backend().get_clipboard()
    except Exception:
        return None


def _set_clipboard(text):
    get_backend().set_clipboard(text)


def _ctrl_v():
    get_backend().send_paste()


def paste_text(text, get_clipboard=None, set_clipboard=None, do_paste=None, sleep=None):
    if not text:
        return
    getc = get_clipboard or _get_clipboard
    setc = set_clipboard or _set_clipboard
    paste = do_paste or _ctrl_v
    nap = sleep or time.sleep
    previous = getc()
    try:
        setc(text)
        paste()
        nap(0.12)  # let the target read the clipboard before we restore it
    finally:
        # Always restore the user's prior clipboard, even if paste raised.
        if previous is not None:
            try:
                setc(previous)
            except Exception as exc:
                log.warning("clipboard restore failed: %s", exc)


def make_injector(mode, char_delay_ms=None):
    """Return the injector function for the configured mode.

    For "type" mode, ``char_delay_ms`` sets the per-character pacing (defaults to
    the backend's preference, else DEFAULT_CHAR_DELAY). Pass 0 to opt out of
    pacing (bulk type)."""
    if mode == "paste":
        return paste_text
    delay = resolve_char_delay(char_delay_ms)

    def typer(text, controller=None, sleep=None):
        return type_text(text, controller=controller, char_delay=delay, sleep=sleep)

    return typer
