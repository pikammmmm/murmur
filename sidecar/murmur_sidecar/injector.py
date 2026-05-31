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


def type_text(text, controller=None):
    if not text:
        return
    if controller is None:
        from pynput.keyboard import Controller
        controller = Controller()
    controller.type(text)


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
    setc(text)
    paste()
    nap(0.12)  # let the target read the clipboard before we restore it
    if previous is not None:
        try:
            setc(previous)
        except Exception as exc:
            log.warning("clipboard restore failed: %s", exc)


def make_injector(mode):
    """Return the injector function for the configured mode."""
    return paste_text if mode == "paste" else type_text
