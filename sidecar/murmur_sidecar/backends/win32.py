"""Windows backend: pynput/SendInput typing, win32clipboard, Win32 foreground
window, winsound cues.

This is the original (pre-Linux-port) behaviour, moved verbatim behind the
Backend interface. All imports stay lazy so the module can be imported on any
platform — which is what lets the test suite exercise it anywhere.
"""
from .base import Backend


class Win32Backend(Backend):
    name = "win32"

    # --- text injection -------------------------------------------------
    def make_controller(self):
        from pynput.keyboard import Controller
        return Controller()

    def send_paste(self):
        from pynput.keyboard import Controller, Key
        controller = Controller()
        with controller.pressed(Key.ctrl):
            controller.press("v")
            controller.release("v")

    # --- clipboard ------------------------------------------------------
    def get_clipboard(self):
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

    def set_clipboard(self, text):
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    # --- window context -------------------------------------------------
    def active_window(self):
        try:
            import psutil
            import win32gui
            import win32process
        except Exception:
            return ("", "")
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = psutil.Process(pid).name()
            if exe.lower() == "applicationframehost.exe":
                exe = _resolve_uwp_child(hwnd, pid, exe)
            return (exe, title)
        except Exception:
            return ("", "")

    # --- audio cues -----------------------------------------------------
    def beep(self, pairs):
        try:
            import winsound
            for freq, ms in pairs:
                winsound.Beep(freq, ms)
        except Exception:
            pass


def _resolve_uwp_child(hwnd, host_pid, default):
    """UWP/packaged apps run under ApplicationFrameHost.exe; the real app is a
    child window owned by a different PID. Return that child's exe name."""
    import psutil
    import win32gui
    import win32process

    names = []

    def _cb(child, _ctx):
        try:
            _, cpid = win32process.GetWindowThreadProcessId(child)
            if cpid and cpid != host_pid:
                name = psutil.Process(cpid).name()
                if name.lower() != "applicationframehost.exe":
                    names.append(name)
        except Exception:
            pass
        return True  # keep enumerating

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return names[0] if names else default
