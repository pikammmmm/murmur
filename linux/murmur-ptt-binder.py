#!/usr/bin/env python3
"""Drive murmur's push-to-talk from the XDG GlobalShortcuts portal.

The Rust shell deliberately does not observe keys itself on Linux: there is no
unprivileged, Wayland-correct way to do it. `/dev/input` and `/dev/uinput` both
need root, `XGrabKey` is X11-only, and KDE's `kglobalaccel` never reports a key
*release* — fatal when the whole interaction is "hold to talk".

`org.freedesktop.portal.GlobalShortcuts` is the one mechanism that is
unprivileged, works under Wayland, and emits both `Activated` and `Deactivated`.
This process binds the shortcut, then translates those two signals into the
one-word lines the shell's control socket expects.

Run it alongside murmur:

    ./linux/murmur-ptt-binder.py

The first run pops a system dialog asking you to choose the key. KDE remembers
the binding per application, so subsequent runs are silent.

Requires python-gobject (already present on a KDE system). No new dependency is
added to the shell for this.
"""
from __future__ import annotations

import os
import socket
import sys
import time
import logging

# GObject is only needed for the portal half. Importing it lazily keeps the
# control-socket half importable (and testable) in a plain venv, and lets the
# binder give a clear diagnostic instead of an ImportError traceback.
try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib  # noqa: E402
    HAVE_GI = True
except (ImportError, ValueError):  # ValueError: gi present, Gio typelib absent
    Gio = GLib = None
    HAVE_GI = False

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SHORTCUT_ID = "push-to-talk"
# Portal syntax is XDG shortcuts spec, not Qt: modifiers are CAPS, joined by '+'.
# The user can always re-bind in the system dialog; this is only the suggestion.
DEFAULT_TRIGGER = os.environ.get("MURMUR_PTT_TRIGGER", "CTRL+ALT+SPACE")

log = logging.getLogger("murmur-ptt-binder")


def socket_path() -> str:
    """Must match `socket_path()` in src-tauri/src/hotkey/linux.rs."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.path.join(runtime, "murmur-ptt.sock")
    user = os.environ.get("USER", "user")
    return os.path.join("/tmp", f"murmur-ptt-{user}.sock")


class ControlSocket:
    """Newline-delimited client for the shell's PTT socket.

    The shell may not be running yet, or may restart under us, so every send
    tolerates a dead socket and reconnects on the next one. Losing a `down` is
    survivable; wedging the binder is not.
    """

    def __init__(self, path: str):
        self.path = path
        self._sock: socket.socket | None = None

    def _connect(self) -> bool:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(self.path)
            self._sock = s
            log.info("connected to %s", self.path)
            return True
        except OSError as e:
            log.debug("connect failed: %s", e)
            self._sock = None
            return False

    def send(self, word: str) -> bool:
        for attempt in (1, 2):
            if self._sock is None and not self._connect():
                if attempt == 1:
                    continue
                log.warning("no shell listening on %s; dropped %r", self.path, word)
                return False
            try:
                self._sock.sendall(f"{word}\n".encode())
                return True
            except OSError as e:
                log.debug("send failed (%s); reconnecting", e)
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        return False


class Binder:
    def __init__(self, bus: Gio.DBusConnection, ctl: ControlSocket):
        self.bus = bus
        self.ctl = ctl
        self.session_handle: str | None = None
        self._token_seq = 0

    # -- portal plumbing -------------------------------------------------

    def _new_token(self, prefix: str) -> str:
        self._token_seq += 1
        return f"murmur_{prefix}_{os.getpid()}_{self._token_seq}"

    def _request_path(self, token: str) -> str:
        """The Request object path the portal will use, per the spec.

        Derived rather than awaited so the Response subscription is in place
        *before* the method call — otherwise a fast portal can answer before we
        are listening.
        """
        unique = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        return f"{PORTAL_PATH}/request/{unique}/{token}"

    def _call_with_response(self, method: str, args: GLib.Variant, token: str, on_response):
        """Invoke a portal method and route its async Response to `on_response`."""
        sub_id = None

        def handler(_conn, _sender, _path, _iface, _signal, params):
            nonlocal sub_id
            code, results = params.unpack()
            if sub_id is not None:
                self.bus.signal_unsubscribe(sub_id)
            on_response(code, results)

        sub_id = self.bus.signal_subscribe(
            PORTAL_BUS, REQUEST_IFACE, "Response", self._request_path(token),
            None, Gio.DBusSignalFlags.NONE, handler,
        )
        self.bus.call(
            PORTAL_BUS, PORTAL_PATH, SHORTCUTS_IFACE, method, args, None,
            Gio.DBusCallFlags.NONE, -1, None, self._on_call_done, method,
        )

    def _on_call_done(self, conn, res, method):
        try:
            conn.call_finish(res)
        except GLib.Error as e:
            log.error("%s failed: %s", method, e.message)
            sys.exit(1)

    # -- flow ------------------------------------------------------------

    def start(self):
        token = self._new_token("session")
        # Plain dict, not a pre-built Variant: only the `v` slots take Variants.
        # Nesting a Variant where GVariant expects a{sv} fails to construct.
        opts = {
            "handle_token": GLib.Variant("s", token),
            "session_handle_token": GLib.Variant("s", self._new_token("sess")),
        }
        log.info("creating portal session")
        self._call_with_response("CreateSession", GLib.Variant("(a{sv})", (opts,)),
                                 token, self._on_session)

    def _on_session(self, code, results):
        if code != 0:
            log.error("CreateSession refused (code %s)", code)
            sys.exit(1)
        self.session_handle = results.get("session_handle")
        log.info("session: %s", self.session_handle)
        self._listen_for_activation()
        self._bind()

    def _bind(self):
        token = self._new_token("bind")
        shortcuts = [(
            SHORTCUT_ID,
            {
                "description": GLib.Variant("s", "murmur — hold to dictate"),
                "preferred_trigger": GLib.Variant("s", DEFAULT_TRIGGER),
            },
        )]
        args = GLib.Variant("(oa(sa{sv})sa{sv})", (
            self.session_handle, shortcuts, "",
            {"handle_token": GLib.Variant("s", token)},
        ))
        log.info("binding %r (suggested %s) — approve the dialog if it appears",
                 SHORTCUT_ID, DEFAULT_TRIGGER)
        self._call_with_response("BindShortcuts", args, token, self._on_bound)

    def _on_bound(self, code, results):
        if code != 0:
            log.error("BindShortcuts refused (code %s) — no shortcut is active", code)
            sys.exit(1)
        bound = results.get("shortcuts") or []
        for sid, meta in bound:
            log.info("bound %s -> %s", sid, meta.get("trigger_description", "?"))
        if not bound:
            log.warning("portal returned no shortcuts; nothing will fire")

    def _listen_for_activation(self):
        def on_signal(_c, _sender, _p, _i, signal, params):
            session, shortcut_id, _ts, _opts = params.unpack()
            if session != self.session_handle or shortcut_id != SHORTCUT_ID:
                return
            word = "down" if signal == "Activated" else "up"
            log.info("%s -> %s", signal, word)
            self.ctl.send(word)

        for sig in ("Activated", "Deactivated"):
            self.bus.signal_subscribe(
                PORTAL_BUS, SHORTCUTS_IFACE, sig, PORTAL_PATH, None,
                Gio.DBusSignalFlags.NONE, on_signal,
            )


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("MURMUR_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )
    if not HAVE_GI:
        log.error("python-gobject is missing — install it with: "
                  "sudo pacman -S python-gobject")
        return 1
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        log.error("no desktop session (WAYLAND_DISPLAY/DISPLAY unset)")
        return 1

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    ctl = ControlSocket(socket_path())
    if not os.path.exists(ctl.path):
        log.warning("shell socket %s does not exist yet — start murmur; "
                    "this binder will connect on the first keypress", ctl.path)

    Binder(bus, ctl).start()
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
