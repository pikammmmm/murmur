"""Linux backend internals: strategy resolution, cue synthesis, window parsing.

These run on any platform (the module imports cleanly everywhere and every OS
call is behind a helper we monkeypatch). The genuinely-needs-a-desktop test at
the bottom is marked ``desktop`` and deselected by default.
"""
import os
import sys

import pytest

from murmur_sidecar.backends import linux as lx


@pytest.fixture
def no_tools(monkeypatch):
    """Nothing installed, X11 session."""
    monkeypatch.setattr(lx, "_has", lambda tool: False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def installed(*tools):
    return lambda tool: tool in set(tools)


@pytest.fixture(autouse=True)
def usable_probes(monkeypatch):
    """Make the "installed AND actually works" probes say yes by default.

    `_typing_candidates` gates wtype and ydotool behind a runtime probe, so
    stubbing `_has` alone is not enough to isolate a test: on KWin the real
    `_wtype_works` shells out and fails, and preference-order tests then quietly
    assert the host's compositor rather than the ordering they name. Tests that
    care about a probe failing override it explicitly.
    """
    monkeypatch.setattr(lx, "_wtype_works", lambda: True)
    monkeypatch.setattr(lx, "_ydotool_works", lambda: True)


# --- session detection --------------------------------------------------


def test_wayland_detected_from_session_type(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert lx.is_wayland() is True


def test_x11_session_with_display_is_not_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert lx.is_wayland() is False


def test_wayland_display_alone_implies_wayland(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert lx.is_wayland() is True


# --- typing strategy ----------------------------------------------------


def test_wayland_prefers_wtype_over_xdotool(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(lx, "_has", installed("wtype", "xdotool"))
    assert lx.LinuxBackend().make_controller().name == "wtype"


def test_installed_but_unusable_wtype_is_skipped(monkeypatch):
    """The KWin case, which is the whole reason the probe exists.

    wtype installs cleanly there and then cannot type: KWin implements neither
    zwp_virtual_keyboard_manager_v1 nor zwp_input_method_manager_v2. Picking it
    on presence alone yields a backend that reports success and types nothing.
    """
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(lx, "_has", installed("wtype", "ydotool"))
    monkeypatch.setattr(lx, "_wtype_works", lambda: False)
    assert lx.LinuxBackend().make_controller().name == "ydotool"


def test_x11_prefers_xdotool_over_wtype(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(lx, "_has", installed("wtype", "xdotool"))
    assert lx.LinuxBackend().make_controller().name == "xdotool"


def test_ydotool_is_used_when_it_is_the_only_option(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(lx, "_has", installed("ydotool"))
    assert lx.LinuxBackend().make_controller().name == "ydotool"


def test_controller_is_resolved_once_and_cached(monkeypatch):
    monkeypatch.setattr(lx, "_has", installed("xdotool"))
    b = lx.LinuxBackend()
    assert b.make_controller() is b.make_controller()


def test_helper_typers_ask_for_bulk_typing(monkeypatch):
    # xdotool/wtype pace themselves via --delay; a per-character Python loop
    # would mean one process spawn per character.
    monkeypatch.setattr(lx, "_has", installed("xdotool"))
    assert lx.LinuxBackend().default_char_delay() == 0.0


def test_command_controller_builds_expected_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(lx, "_run", lambda cmd, **kw: calls.append(cmd) or b"")
    c = lx._xdotool_controller()
    c.type("hi there")
    c.paste()
    assert calls[0] == ["xdotool", "type", "--clearmodifiers", "--delay", "6", "--", "hi there"]
    assert calls[1] == ["xdotool", "key", "--clearmodifiers", "ctrl+v"]


def test_wtype_argv_uses_double_dash_so_leading_dashes_are_text(monkeypatch):
    calls = []
    monkeypatch.setattr(lx, "_run", lambda cmd, **kw: calls.append(cmd) or b"")
    lx._wtype_controller().type("-- not a flag")
    assert calls[0] == ["wtype", "--", "-- not a flag"]


def test_empty_text_spawns_no_process(monkeypatch):
    calls = []
    monkeypatch.setattr(lx, "_run", lambda cmd, **kw: calls.append(cmd) or b"")
    lx._xdotool_controller().type("")
    assert calls == []


def test_no_typing_backend_returns_none_rather_than_raising(no_tools, monkeypatch):
    # Also make the pynput last resort unavailable.
    monkeypatch.setitem(sys.modules, "pynput.keyboard", None)
    b = lx.LinuxBackend()
    assert b.make_controller() is None
    b.send_paste()  # must not raise


# --- clipboard ----------------------------------------------------------


def test_wayland_session_picks_wl_clipboard(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(lx, "_has", installed("wl-copy", "wl-paste", "xclip"))
    assert lx.LinuxBackend()._clipboard_tools()[0] == "wl"


def test_x11_session_picks_xclip(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(lx, "_has", installed("xclip"))
    assert lx.LinuxBackend()._clipboard_tools()[0] == "xclip"


def test_set_clipboard_raises_a_clear_error_when_unavailable(no_tools):
    with pytest.raises(RuntimeError, match="no clipboard tool"):
        lx.LinuxBackend().set_clipboard("text")


def test_get_clipboard_returns_none_when_unavailable(no_tools):
    assert lx.LinuxBackend().get_clipboard() is None


def test_set_clipboard_pipes_utf8_to_the_tool(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(lx, "_has", installed("xclip"))
    seen = {}

    def fake_run(cmd, stdin_bytes=None, capture=False):
        seen["cmd"], seen["stdin"] = cmd, stdin_bytes
        return b""

    monkeypatch.setattr(lx, "_run", fake_run)
    lx.LinuxBackend().set_clipboard("čšž")
    assert seen["cmd"][0] == "xclip"
    assert seen["stdin"] == "čšž".encode("utf-8")


# --- audio cue synthesis ------------------------------------------------


def test_sine_pcm_length_matches_requested_duration():
    pcm = lx._sine_pcm([(600, 100)], rate=8000)
    assert len(pcm) == 800 * 2  # 0.1s @ 8 kHz, 16-bit mono


def test_sine_pcm_is_not_silent_and_stays_in_range():
    import array

    pcm = lx._sine_pcm([(600, 50)], rate=8000)
    samples = array.array("h")
    samples.frombytes(pcm)
    assert max(abs(s) for s in samples) > 1000       # audible
    assert all(-32768 <= s <= 32767 for s in samples)  # no wrap-around


def test_sine_pcm_fades_in_to_avoid_a_click():
    import array

    samples = array.array("h")
    samples.frombytes(lx._sine_pcm([(600, 50)], rate=8000))
    assert abs(samples[0]) < 100  # starts near zero


def test_multiple_tones_concatenate():
    one = len(lx._sine_pcm([(600, 50)], rate=8000))
    two = len(lx._sine_pcm([(600, 50), (900, 50)], rate=8000))
    assert two == 2 * one


def test_beep_without_any_player_is_silent_not_fatal(no_tools):
    lx.LinuxBackend().beep([(600, 10)])  # must not raise


def test_beep_pipes_pcm_to_paplay(monkeypatch):
    monkeypatch.setattr(lx, "_has", installed("paplay"))
    seen = {}

    def fake_run(cmd, stdin_bytes=None, capture=False):
        seen["cmd"], seen["len"] = cmd, len(stdin_bytes or b"")
        return b""

    monkeypatch.setattr(lx, "_run", fake_run)
    lx.LinuxBackend().beep([(600, 20)])
    assert seen["cmd"][0] == "paplay"
    assert "--raw" in seen["cmd"]
    assert seen["len"] > 0


# --- foreground window --------------------------------------------------

_XPROP_ROOT = b'_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1000007\n'
_XPROP_WIN = (
    b'_NET_WM_NAME(UTF8_STRING) = "main.rs - murmur - VSCodium"\n'
    b'_NET_WM_PID(CARDINAL) = 4242\n'
    b'WM_CLASS(STRING) = "vscodium", "VSCodium"\n'
)


def test_xprop_parsing_extracts_title_and_exe(monkeypatch):
    def fake_run(cmd, stdin_bytes=None, capture=False):
        return _XPROP_ROOT if "-root" in cmd else _XPROP_WIN

    monkeypatch.setattr(lx, "_run", fake_run)
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "codium" if pid == 4242 else "")
    exe, title = lx._active_window_xprop()
    assert exe == "codium"
    assert title == "main.rs - murmur - VSCodium"


def test_xprop_falls_back_to_wm_class_without_a_pid(monkeypatch):
    def fake_run(cmd, stdin_bytes=None, capture=False):
        return _XPROP_ROOT if "-root" in cmd else b'WM_CLASS(STRING) = "vscodium", "VSCodium"\n'

    monkeypatch.setattr(lx, "_run", fake_run)
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "")
    assert lx._active_window_xprop()[0] == "VSCodium"


def test_no_active_window_yields_empty(monkeypatch):
    monkeypatch.setattr(lx, "_run", lambda cmd, **kw: b"_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n")
    # 0x0 is a valid hex id but names no window; the id parse still succeeds and
    # the follow-up query returns nothing useful.
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "")


def _fake_kwin(monkeypatch, fields):
    """Drive _active_window_kwin without KWin.

    The nonce is generated inside the function, so the fake reads it back out of
    the script file handed to loadScript and echoes it in the journal output —
    which also asserts that the script we write and the line we look for agree.
    """
    import re

    seen = {}

    def fake_run(cmd, **kw):
        if "loadScript" in cmd:
            js = open(cmd[8], encoding="utf-8").read()
            seen["nonce"] = re.search(r"MURMURFOCUS[0-9a-f]+", js).group(0)
            seen["plugin"] = cmd[9]
            return b""
        if cmd[0] == "journalctl":
            if fields is None:
                return b"unrelated kwin chatter\n"
            body = "\x1f".join(str(f) for f in fields)
            return ("noise\n%s\t%s\n" % (seen["nonce"], body)).encode("utf-8")
        if "unloadScript" in cmd:
            seen["unloaded"] = cmd[-1]
        return b""

    monkeypatch.setattr(lx, "_run", fake_run)
    return seen


def test_kwin_probe_parses_class_pid_and_caption(monkeypatch):
    seen = _fake_kwin(monkeypatch, ("org.kde.konsole", 4012, "~ : murmur — Konsole"))
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "konsole" if pid == 4012 else "")
    # The pid's real process name wins over resourceClass, which is inconsistent
    # between the Wayland and XWayland backends.
    assert lx._active_window_kwin() == ("konsole", "~ : murmur — Konsole")
    # The script must always be unloaded, under the same name it was loaded with.
    assert seen["unloaded"] == seen["plugin"]


def test_kwin_probe_falls_back_to_resource_class_without_a_pid(monkeypatch):
    _fake_kwin(monkeypatch, ("firefox", 0, "Mozilla Firefox"))
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "")
    assert lx._active_window_kwin() == ("firefox", "Mozilla Firefox")


def test_kwin_probe_treats_the_lock_screen_as_no_window(monkeypatch):
    # KWin's own captionless surface is the lock screen / an OSD — never a
    # dictation target, so it must not become a formatting context.
    _fake_kwin(monkeypatch, ("kwin_wayland", 996, ""))
    monkeypatch.setattr(lx, "_exe_for_pid", lambda pid: "kwin_wayland")
    assert lx._active_window_kwin() == ("", "")


def test_kwin_probe_gives_up_quietly_when_nothing_is_printed(monkeypatch):
    _fake_kwin(monkeypatch, None)
    assert lx._active_window_kwin() == ("", "")


def test_active_window_returns_empty_when_all_probes_fail(monkeypatch):
    """Every probe raising must degrade to the generic profile, not propagate.

    All three are broken explicitly — including the KWin one, which runs first on
    a KDE session and would otherwise answer for real and mask the fallback.
    """
    def boom():
        raise RuntimeError("no X server")

    monkeypatch.setattr(lx, "_active_window_kwin", boom)
    monkeypatch.setattr(lx, "_active_window_xlib", boom)
    monkeypatch.setattr(lx, "_active_window_xprop", boom)
    assert lx.LinuxBackend().active_window() == ("", "")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
def test_exe_for_pid_resolves_this_interpreter():
    # Real /proc read — no mocking. This process is a Python interpreter.
    assert "python" in lx._exe_for_pid(os.getpid()).lower()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")
def test_exe_for_pid_is_empty_for_a_bogus_pid():
    assert lx._exe_for_pid(0) == ""
    assert lx._exe_for_pid(999_999_99) == ""


# --- diagnostics --------------------------------------------------------


def test_diagnostics_reports_resolved_strategies(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(lx, "_has", installed("wtype", "wl-copy", "wl-paste", "paplay"))
    d = lx.LinuxBackend().diagnostics()
    assert d["backend"] == "linux"
    assert d["session"] == "wayland"
    assert d["typing"] == "wtype"
    assert d["clipboard"] == "wl"
    assert d["audio"] == "paplay"


# --- the real thing (opt-in) -------------------------------------------


@pytest.mark.desktop
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop only")
def test_round_trip_injection_into_a_real_window():
    """Type through the production injector into a real focused X11 window and
    read the keystrokes back. Requires a running X11/XWayland display.

    Run with:  pytest -m desktop
    """
    Xlib = pytest.importorskip("Xlib")
    import time

    from Xlib import X, XK, display
    import Xlib.protocol.event

    from murmur_sidecar import injector

    try:
        d = display.Display()
    except Exception as exc:
        pytest.skip("no X display: %s" % exc)

    root = d.screen().root
    win = root.create_window(
        100, 100, 400, 200, 0, d.screen().root_depth,
        X.InputOutput, X.CopyFromParent,
        background_pixel=d.screen().white_pixel,
        event_mask=X.KeyPressMask | X.KeyReleaseMask | X.StructureNotifyMask,
    )
    try:
        win.set_wm_name("murmur-injection-target")
        win.set_wm_class("murmurprobe", "MurmurProbe")
        win.map()
        d.sync()
        root.send_event(
            Xlib.protocol.event.ClientMessage(
                window=win,
                client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                data=(32, [1, X.CurrentTime, 0, 0, 0]),
            ),
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )
        d.sync()
        for _ in range(30):
            time.sleep(0.1)
            if getattr(d.get_input_focus().focus, "id", None) == win.id:
                break
        else:
            pytest.skip("window manager would not focus the probe window")

        phrase = "hello murmur two words"
        injector.type_text(phrase)
        time.sleep(0.6)

        typed = []
        for _ in range(d.pending_events()):
            ev = d.next_event()
            if ev.type == X.KeyPress:
                ks = d.keycode_to_keysym(ev.detail, 0)
                s = XK.keysym_to_string(ks)
                typed.append(s if s else (" " if ks == XK.XK_space else ""))
        assert "".join(typed) == phrase
    finally:
        win.destroy()
        d.close()
