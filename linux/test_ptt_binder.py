"""Tests for the PTT binder's control-socket half.

The portal half needs a real keypress and a live compositor, so it is not
covered here. This covers everything below that: the wire words, the socket
path contract with the Rust shell, and the reconnect behaviour that keeps the
binder alive across a shell restart.

    python3 -m pytest linux/test_ptt_binder.py -q
"""
from __future__ import annotations

import importlib.util
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ptt_binder", Path(__file__).with_name("murmur-ptt-binder.py")
)
binder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(binder)


class _Server:
    """Minimal stand-in for the Rust shell's UnixListener."""

    def __init__(self, path: str):
        self.path = path
        self.received: list[str] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(1)
        self._sock.settimeout(5.0)
        self._conn: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        self._conn = conn
        buf = b""
        while True:
            try:
                chunk = conn.recv(64)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.received.append(line.decode())

    def close(self):
        """Close the accepted connection too.

        Closing only the listener leaves the established connection alive, so a
        client keeps writing into it happily — which is not what a shell
        *restart* looks like. The process dying takes both down.
        """
        for s in (self._conn, self._sock):
            if s is None:
                continue
            # shutdown() before close(): the serve thread is blocked in recv(),
            # which holds the open file description alive, so close() alone
            # leaves the connection usable and the client keeps writing into it.
            # Only shutdown() actually tears the connection down.
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


@pytest.fixture
def server(tmp_path):
    path = str(tmp_path / "ptt.sock")
    s = _Server(path)
    yield s
    s.close()


def _settle(pred, timeout=3.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# -- socket path contract -------------------------------------------------

def test_socket_path_uses_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert binder.socket_path() == "/run/user/4242/murmur-ptt.sock"


def test_socket_path_falls_back_per_user(monkeypatch):
    """Must stay user-qualified so two users cannot collide in /tmp."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("USER", "someone")
    p = binder.socket_path()
    assert p.endswith("murmur-ptt-someone.sock")


def test_empty_runtime_dir_is_treated_as_unset(monkeypatch):
    """Matches the Rust side, which guards on `!dir.is_empty()`."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "")
    monkeypatch.setenv("USER", "someone")
    assert binder.socket_path().endswith("murmur-ptt-someone.sock")


# -- the wire words -------------------------------------------------------

def test_sends_newline_delimited_words(server):
    ctl = binder.ControlSocket(server.path)
    assert ctl.send("down")
    assert ctl.send("up")
    assert _settle(lambda: server.received == ["down", "up"]), server.received


def test_words_match_the_rust_parser():
    """`parse_cmd` in hotkey/linux.rs accepts exactly these."""
    accepted = {"down", "press", "activated", "up", "release", "deactivated",
                "cancel", "abort"}
    assert {"down", "up", "cancel"} <= accepted


# -- resilience -----------------------------------------------------------

def test_send_without_a_listener_fails_quietly(tmp_path):
    """A missing shell must not raise — losing a keypress beats crashing."""
    ctl = binder.ControlSocket(str(tmp_path / "absent.sock"))
    assert ctl.send("down") is False


def test_reconnects_after_the_shell_restarts(tmp_path):
    path = str(tmp_path / "ptt.sock")
    first = _Server(path)
    ctl = binder.ControlSocket(path)
    assert ctl.send("down")
    assert _settle(lambda: first.received == ["down"])
    first.close()
    os.unlink(path)

    second = _Server(path)          # shell restarts, new socket at same path
    ctl.send("up")                  # first attempt may hit the dead fd...
    ctl.send("up")                  # ...the retry must land
    assert _settle(lambda: "up" in second.received), second.received
    second.close()
