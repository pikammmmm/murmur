"""Linux backend (X11 and Wayland).

Unlike Windows — where SendInput, the clipboard and the foreground window are
all one stable API — Linux has no single answer for any of them, so each
capability resolves a *strategy* at first use and caches it:

  typing     wtype (Wayland) -> ydotool -> xdotool (X11) -> pynput/XTEST
  clipboard  wl-copy/wl-paste (Wayland) -> xclip -> xsel
  window     python-xlib EWMH -> xprop
  cues       paplay -> pw-play -> canberra-gtk-play

Ordering is session-aware: on Wayland the compositor-native tools come first
because they also reach native Wayland clients; the XTEST path only reaches
X11/XWayland clients. On X11 the order flips to put xdotool first.

Nothing here needs root. The one capability Linux genuinely cannot offer to an
unprivileged process is *global* key interception (see LINUX-PORT-NOTES.md) —
but that lives in the Rust shell, not the sidecar.
"""
import array
import logging
import math
import os
import shutil
import subprocess

from .base import Backend

log = logging.getLogger("murmur.backend.linux")

_TIMEOUT = 5  # seconds; every helper process is expected to be near-instant


def _has(tool):
    return shutil.which(tool) is not None


def is_wayland():
    """True when the *session* is Wayland. Note DISPLAY is usually also set
    (XWayland), so the presence of DISPLAY proves nothing on its own."""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _run(cmd, stdin_bytes=None, capture=False):
    """Run a helper process; return stdout bytes (or b"") and never raise."""
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TIMEOUT,
            check=True,
        )
        return proc.stdout if capture else b""
    except Exception as exc:
        log.debug("helper %s failed: %s", cmd[0] if cmd else "?", exc)
        raise


# --------------------------------------------------------------------------
# Typing controllers: duck-typed objects exposing .type(str), matching what
# injector.type_text expects. Subprocess-based ones type in bulk (they pace
# internally), so they also advertise a 0 char delay.
# --------------------------------------------------------------------------


class _CommandController:
    """Types via a one-shot helper process (wtype / ydotool / xdotool)."""

    #: bulk — the helper does its own inter-key pacing, so a per-character
    #: Python loop would mean one process spawn per character.
    char_delay = 0.0

    def __init__(self, argv_for, paste_argv, name):
        self._argv_for = argv_for
        self._paste_argv = paste_argv
        self.name = name

    def type(self, text):
        if not text:
            return
        _run(self._argv_for(text))

    def paste(self):
        _run(self._paste_argv)


class _PynputController:
    """Types via pynput, which on X11/XWayland uses the XTEST extension.

    XTEST has the same keystroke-drop failure mode as SendInput, so this one
    keeps the per-character pacing (char_delay=None -> caller's default)."""

    char_delay = None
    name = "pynput"

    def __init__(self):
        from pynput.keyboard import Controller
        self._c = Controller()

    def type(self, text):
        self._c.type(text)

    def paste(self):
        from pynput.keyboard import Key
        with self._c.pressed(Key.ctrl):
            self._c.press("v")
            self._c.release("v")


def _wtype_controller():
    return _CommandController(
        lambda text: ["wtype", "--", text],
        ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
        "wtype",
    )


def _ydotool_controller():
    return _CommandController(
        lambda text: ["ydotool", "type", "--", text],
        # 29 = KEY_LEFTCTRL, 47 = KEY_V (evdev codes); 1 = down, 0 = up.
        ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
        "ydotool",
    )


def _xdotool_controller():
    return _CommandController(
        lambda text: ["xdotool", "type", "--clearmodifiers", "--delay", "6", "--", text],
        ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
        "xdotool",
    )


class LinuxBackend(Backend):
    name = "linux"

    def __init__(self):
        self._controller = None
        self._controller_resolved = False

    # --- text injection -------------------------------------------------
    def _typing_candidates(self):
        """(tool_name, factory) pairs in preference order for this session."""
        wayland = [
            ("wtype", _wtype_controller),
            ("ydotool", _ydotool_controller),
            ("xdotool", _xdotool_controller),
        ]
        x11 = [
            ("xdotool", _xdotool_controller),
            ("wtype", _wtype_controller),
            ("ydotool", _ydotool_controller),
        ]
        return wayland if is_wayland() else x11

    def make_controller(self):
        if self._controller_resolved:
            return self._controller
        self._controller_resolved = True
        for tool, factory in self._typing_candidates():
            if _has(tool):
                self._controller = factory()
                log.info("injection backend: %s", tool)
                return self._controller
        # Last resort: pynput/XTEST. Reaches X11 and XWayland clients; native
        # Wayland clients will not see these keystrokes.
        try:
            self._controller = _PynputController()
            log.info("injection backend: pynput/XTEST (X11/XWayland clients only)")
        except Exception as exc:
            log.warning("no text-injection backend available: %s", exc)
            self._controller = None
        return self._controller

    def default_char_delay(self):
        """Seconds between characters, or None to use the caller's default."""
        c = self.make_controller()
        return getattr(c, "char_delay", None)

    def send_paste(self):
        c = self.make_controller()
        if c is not None:
            c.paste()

    # --- clipboard ------------------------------------------------------
    def _clipboard_tools(self):
        wl = ("wl-copy", "wl-paste")
        if is_wayland() and _has(wl[0]) and _has(wl[1]):
            return ("wl", None)
        if _has("xclip"):
            return ("xclip", None)
        if _has("xsel"):
            return ("xsel", None)
        if _has(wl[0]) and _has(wl[1]):
            return ("wl", None)
        return (None, None)

    def get_clipboard(self):
        tool, _ = self._clipboard_tools()
        cmds = {
            "wl": ["wl-paste", "--no-newline"],
            "xclip": ["xclip", "-selection", "clipboard", "-o"],
            "xsel": ["xsel", "--clipboard", "--output"],
        }
        if tool is None:
            return None
        try:
            return _run(cmds[tool], capture=True).decode("utf-8", "replace")
        except Exception:
            return None  # empty clipboard exits non-zero on some tools

    def set_clipboard(self, text):
        tool, _ = self._clipboard_tools()
        if tool is None:
            raise RuntimeError("no clipboard tool available (install wl-clipboard or xclip)")
        cmds = {
            "wl": ["wl-copy"],
            "xclip": ["xclip", "-selection", "clipboard", "-i"],
            "xsel": ["xsel", "--clipboard", "--input"],
        }
        _run(cmds[tool], stdin_bytes=(text or "").encode("utf-8"))

    # --- window context -------------------------------------------------
    def active_window(self):
        for probe in (_active_window_xlib, _active_window_xprop):
            try:
                exe, title = probe()
                if exe or title:
                    return (exe, title)
            except Exception as exc:
                log.debug("active-window probe %s failed: %s", probe.__name__, exc)
        return ("", "")

    # --- audio cues -----------------------------------------------------
    def beep(self, pairs):
        try:
            pcm = _sine_pcm(pairs)
        except Exception:
            return
        if not pcm:
            return
        for cmd in (
            ["paplay", "--raw", "--format=s16le", "--rate=44100", "--channels=1"],
            ["pw-play", "--format=s16", "--rate=44100", "--channels=1", "-"],
        ):
            if not _has(cmd[0]):
                continue
            try:
                _run(cmd, stdin_bytes=pcm)
                return
            except Exception:
                continue
        # Last resort: a themed system sound (no pitch control, but audible).
        if _has("canberra-gtk-play"):
            try:
                _run(["canberra-gtk-play", "-i", "bell"])
            except Exception:
                pass

    # --- diagnostics ----------------------------------------------------
    def diagnostics(self):
        c = self.make_controller()
        clip, _ = self._clipboard_tools()
        return {
            "backend": self.name,
            "session": "wayland" if is_wayland() else "x11",
            "typing": getattr(c, "name", None),
            "clipboard": clip,
            "audio": next((t for t in ("paplay", "pw-play", "canberra-gtk-play") if _has(t)), None),
            "window": "xlib" if _xlib_ok() else ("xprop" if _has("xprop") else None),
        }


# --------------------------------------------------------------------------
# Audio cue synthesis: winsound.Beep(freq, ms) has no Linux equivalent, so we
# render the tones ourselves and pipe raw PCM to the sound server.
# --------------------------------------------------------------------------

_RATE = 44100
_FADE = 0.004  # seconds of fade in/out, so tones don't click


def _sine_pcm(pairs, rate=_RATE, amplitude=0.28):
    """Render [(freq_hz, duration_ms), ...] to signed-16-bit little-endian mono."""
    samples = array.array("h")
    for freq, ms in pairs:
        n = int(rate * (ms / 1000.0))
        fade = max(1, int(rate * _FADE))
        for i in range(n):
            env = min(1.0, i / fade, max(0.0, (n - i) / fade))
            value = amplitude * env * math.sin(2.0 * math.pi * freq * (i / rate))
            samples.append(int(max(-1.0, min(1.0, value)) * 32767))
    if sys_is_big_endian():
        samples.byteswap()
    return samples.tobytes()


def sys_is_big_endian():
    import sys
    return sys.byteorder == "big"


# --------------------------------------------------------------------------
# Foreground window: EWMH _NET_ACTIVE_WINDOW -> title + PID -> process name.
# Works for X11 and XWayland clients. Native Wayland clients are deliberately
# not introspectable by other clients, so those degrade to the generic profile.
# --------------------------------------------------------------------------


def _xlib_ok():
    try:
        import Xlib  # noqa: F401
        return True
    except Exception:
        return False


def _exe_for_pid(pid):
    """Process name for a pid: /proc/<pid>/exe wins (untruncated), then comm."""
    if not pid:
        return ""
    try:
        return os.path.basename(os.readlink("/proc/%d/exe" % pid))
    except Exception:
        pass
    try:
        with open("/proc/%d/comm" % pid, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def _active_window_xlib():
    from Xlib import display as _display
    from Xlib import Xatom

    d = _display.Display()
    try:
        root = d.screen().root
        active = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW)
        if not active or not active.value:
            return ("", "")
        win = d.create_resource_object("window", active.value[0])

        title = ""
        prop = win.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)  # UTF8_STRING
        if prop and prop.value:
            title = prop.value.decode("utf-8", "replace") if isinstance(prop.value, bytes) else str(prop.value)
        if not title:
            prop = win.get_full_property(Xatom.WM_NAME, 0)
            if prop and prop.value:
                title = prop.value.decode("utf-8", "replace") if isinstance(prop.value, bytes) else str(prop.value)

        pid = 0
        prop = win.get_full_property(d.intern_atom("_NET_WM_PID"), Xatom.CARDINAL)
        if prop and prop.value:
            pid = int(prop.value[0])
        exe = _exe_for_pid(pid)
        if not exe:
            # No _NET_WM_PID (common for some toolkits): fall back to WM_CLASS.
            try:
                cls = win.get_wm_class()
                if cls:
                    exe = cls[-1]
            except Exception:
                pass
        return (exe, title)
    finally:
        try:
            d.close()
        except Exception:
            pass


def _active_window_xprop():
    out = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture=True).decode("utf-8", "replace")
    wid = out.rsplit("#", 1)[-1].strip() if "#" in out else out.rsplit(" ", 1)[-1].strip()
    if not wid.startswith("0x"):
        return ("", "")
    info = _run(["xprop", "-id", wid, "_NET_WM_NAME", "_NET_WM_PID", "WM_CLASS"], capture=True)
    info = info.decode("utf-8", "replace")
    title, pid, wm_class = "", 0, ""
    for line in info.splitlines():
        if line.startswith("_NET_WM_NAME") and '"' in line:
            title = line.split('"', 1)[1].rsplit('"', 1)[0]
        elif line.startswith("_NET_WM_PID"):
            try:
                pid = int(line.rsplit("=", 1)[-1].strip())
            except ValueError:
                pass
        elif line.startswith("WM_CLASS") and '"' in line:
            wm_class = line.rsplit('"', 2)[-2]
    return (_exe_for_pid(pid) or wm_class, title)
