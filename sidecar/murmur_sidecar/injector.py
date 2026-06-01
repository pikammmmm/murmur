"""Insert the final text into the focused window.

Two modes:
  * "type"  — pynput types it character by character (default; works in most apps).
  * "paste" — put it on the clipboard and send Ctrl+V, restoring the previous
    clipboard afterward. More reliable in apps that drop or mangle synthetic
    keystrokes (some Electron/terminal/game UIs), and much faster for long text.

All Win32/pynput bits are injectable so the logic is unit-testable.
"""
import logging
import time

log = logging.getLogger("murmur.injector")

# Seconds between characters when typing. pynput's bulk type() fires key events
# as fast as SendInput allows; apps drop keystrokes that arrive faster than their
# message pump drains them — and a dropped space ("twowords") is the most visible
# casualty. A few ms of pacing per character lets the target keep up. Small
# enough to stay imperceptible (~0.6s for a 100-char dictation).
DEFAULT_CHAR_DELAY = 0.006


def type_text(text, controller=None, char_delay=DEFAULT_CHAR_DELAY, sleep=None):
    if not text:
        return
    if controller is None:
        from pynput.keyboard import Controller
        controller = Controller()
    if not char_delay:
        controller.type(text)  # unpaced (opt-out): one bulk call
        return
    nap = sleep or time.sleep
    for ch in text:
        controller.type(ch)
        nap(char_delay)  # let the target consume this keystroke before the next


def _get_clipboard():
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _set_clipboard(text):
    import win32clipboard
    import win32con
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def _ctrl_v():
    from pynput.keyboard import Controller, Key
    controller = Controller()
    with controller.pressed(Key.ctrl):
        controller.press("v")
        controller.release("v")


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
    DEFAULT_CHAR_DELAY when None). Pass 0 to opt out of pacing (bulk type)."""
    if mode == "paste":
        return paste_text
    delay = DEFAULT_CHAR_DELAY if char_delay_ms is None else char_delay_ms / 1000.0

    def typer(text, controller=None, sleep=None):
        return type_text(text, controller=controller, char_delay=delay, sleep=sleep)

    return typer
