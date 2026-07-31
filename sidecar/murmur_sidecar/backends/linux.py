"""Linux backend (X11 and Wayland).

Unlike Windows — where SendInput, the clipboard and the foreground window are
all one stable API — Linux has no single answer for any of them, so each
capability resolves a *strategy* at first use and caches it:

  typing     wtype (Wayland) -> ydotool -> xdotool (X11) -> pynput/XTEST,
             skipping any tool that is installed but non-functional here
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
import time

from .base import Backend

log = logging.getLogger("murmur.backend.linux")

_TIMEOUT = 5  # seconds; every helper process is expected to be near-instant


def _has(tool):
    return shutil.which(tool) is not None


def _wtype_works():
    """True when the compositor actually implements the virtual keyboard protocol.

    Being installed is not evidence that wtype can do anything. It needs
    zwp_virtual_keyboard_manager_v1, which wlroots compositors and Mutter
    provide but KWin does not; on KWin every call exits 1 with "Compositor does
    not support the virtual keyboard protocol". Selecting on presence alone
    picks a tool that reports success upstream while typing nothing, which is
    indistinguishable from dictation being broken.

    Typing the empty string is the probe: it emits no keystrokes, so this is
    safe to run at startup, and it exercises the same protocol handshake a real
    call would.
    """
    try:
        return subprocess.run(
            ["wtype", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TIMEOUT,
        ).returncode == 0
    except Exception as exc:
        log.debug("wtype probe failed: %s", exc)
        return False


def _ydotool_works():
    """True when ydotoold is up and owns /dev/uinput.

    The client is useless without its daemon, and the daemon is useless without
    write access to /dev/uinput (udev grants group `input`). Probing the socket
    avoids the only alternative -- injecting a keystroke to find out.
    """
    sock = os.environ.get("YDOTOOL_SOCKET") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket"
    )
    return os.path.exists(sock) or os.path.exists("/tmp/.ydotool_socket")


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


# ydotool defaults to --key-delay 20 AND --key-hold 20, i.e. 40ms per character:
# a 120-character dictation takes ~4.8s to appear, typing itself out visibly. That
# dwarfed transcription and formatting and was the single largest cost in the
# pipeline.
#
# Measured lossless at every setting down to 0/0, in both a VTE terminal and a Qt
# input field (298-char sample, exact round-trip compare). 0/0 does the same
# sample in 0.03s.
#
# Shipping hold=1 rather than 0 is a deliberate margin: at hold=0 a key's press
# and release land in the same microsecond, and a toolkit that coalesces or
# debounces them drops the character silently -- which in a dictation reads as a
# transcription error, not a timing bug. GTK and Electron are untested. 1ms costs
# 0.33s per 298 characters, which is imperceptible next to the ~1s STT leg.
_YDOTOOL_KEY_DELAY_MS = 0
_YDOTOOL_KEY_HOLD_MS = 1


def _ydotool_controller():
    return _CommandController(
        lambda text: [
            "ydotool", "type",
            "-d", str(_YDOTOOL_KEY_DELAY_MS),
            "-H", str(_YDOTOOL_KEY_HOLD_MS),
            "--", text,
        ],
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
        """(tool_name, factory, usable) triples in preference order.

        `usable` is an extra check beyond "the binary exists", for tools that
        install cleanly and then cannot function -- see _wtype_works.
        """
        wayland = [
            ("wtype", _wtype_controller, _wtype_works),
            ("ydotool", _ydotool_controller, _ydotool_works),
            ("xdotool", _xdotool_controller, None),
        ]
        x11 = [
            ("xdotool", _xdotool_controller, None),
            ("wtype", _wtype_controller, _wtype_works),
            ("ydotool", _ydotool_controller, _ydotool_works),
        ]
        return wayland if is_wayland() else x11

    def make_controller(self):
        if self._controller_resolved:
            return self._controller
        self._controller_resolved = True
        for tool, factory, usable in self._typing_candidates():
            if not _has(tool):
                continue
            if usable is not None and not usable():
                log.info("injection backend: %s installed but not usable here, skipping", tool)
                continue
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
        # KWin first on KDE: it is the only probe that sees native Wayland
        # clients, which the EWMH probes are structurally blind to. The X11
        # probes still run after it, both as the answer on real X11 sessions and
        # as a fallback if the compositor query fails.
        probes = (_active_window_xlib, _active_window_xprop)
        if _kwin_ok():
            probes = (_active_window_kwin,) + probes
        for probe in probes:
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
            "window": "kwin" if _kwin_ok() else ("xlib" if _xlib_ok() else ("xprop" if _has("xprop") else None)),
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


def _kwin_ok():
    """KWin scripting is reachable: a KDE session with busctl + journalctl."""
    return (
        "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        and _has("busctl")
        and _has("journalctl")
    )


# KWin evaluates this, printing one tab-separated line we recover from the
# journal. Fields are unit-separator joined so a caption containing a tab (or
# anything else) cannot desync the parse.
_KWIN_JS = (
    'var w = workspace.activeWindow;\n'
    'print("%s\\t" + (w ? [w.resourceClass, w.pid, w.caption].join("\\u001f") : ""));\n'
)


def _active_window_kwin():
    """Ask KWin directly, the only route that sees native Wayland clients.

    Wayland deliberately forbids clients from inspecting each other, so the EWMH
    probes below are blind to every non-XWayland window — which on Plasma 6 is
    most of them. The compositor itself is not blind, and KWin exposes a
    scripting engine over D-Bus, so we load a one-shot script, read what it
    printed out of the journal, and unload it. Measured at roughly 10ms.

    The script is loaded under a nonce-suffixed plugin name because `loadScript`
    returns -1 for a name that is already registered, and `start()` starts every
    loaded-but-idle script — so concurrent callers would otherwise read each
    other's output. The same nonce tags the printed line.
    """
    import secrets
    import tempfile

    nonce = "MURMURFOCUS" + secrets.token_hex(6)
    plugin = "murmurfocus" + nonce[-8:]
    path = None
    loaded = False
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", prefix="murmur-focus-", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(_KWIN_JS % nonce)
            path = fh.name  # loadScript requires an absolute path

        _run([
            "busctl", "--user", "call", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting", "loadScript", "ss", path, plugin,
        ], capture=True)
        loaded = True
        _run([
            "busctl", "--user", "call", "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting", "start",
        ])

        # print() reaches the journal asynchronously; poll briefly rather than
        # sleeping a fixed worst case.
        deadline = time.time() + 0.5
        while time.time() < deadline:
            out = _run(
                ["journalctl", "--user", "-b", "-t", "kwin_wayland", "-o", "cat", "-n", "80"],
                capture=True,
            ).decode("utf-8", "replace")
            for line in reversed(out.splitlines()):
                if nonce not in line:
                    continue
                payload = line.split(nonce + "\t", 1)[-1].strip()
                if not payload:
                    return ("", "")  # nothing focused
                parts = payload.split("\x1f")
                cls = parts[0] if parts else ""
                pid = 0
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        pid = 0
                title = parts[2] if len(parts) > 2 else ""
                # KWin's own surfaces (lock screen, OSDs, the switcher) are not
                # dictation targets; treat them as "no window" so the caller
                # falls back to the generic profile.
                if cls == "kwin_wayland" and not title:
                    return ("", "")
                # The pid's real process name beats resourceClass, which differs
                # between backends (org.kde.konsole on Wayland, konsole on X11).
                return (_exe_for_pid(pid) or cls, title)
            time.sleep(0.02)
        return ("", "")
    except Exception as exc:
        log.debug("kwin focus probe failed: %s", exc)
        return ("", "")
    finally:
        if loaded:
            try:
                _run([
                    "busctl", "--user", "call", "org.kde.KWin", "/Scripting",
                    "org.kde.kwin.Scripting", "unloadScript", "s", plugin,
                ], capture=True)
            except Exception:
                log.debug("kwin script %s left loaded", plugin)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


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
