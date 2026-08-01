#!/usr/bin/env python3
"""Drive murmur's push-to-talk from keyd, giving bare-key hold-to-talk.

This is the alternative to murmur-ptt-binder.py (the portal route). Use one or
the other, never both -- they would both write `down`/`up` to the same socket.

Why not the portal:

  * Its grab is by KEY, not by device, so binding a bare printable key swallows
    that character everywhere. Re-injecting it on a tap does not help: the
    injected keystroke is grabbed too (measured -- a Qt input field stayed
    empty) and re-enters as another tap, looping.
  * Binding Meta+backslash avoids that but leaks a stray `\\`: release Meta a
    moment before the still-held `\\` and the combination stops matching while
    the key is physically down, so the bare key reaches the focused window.

keyd sits below the compositor -- it grabs the physical keyboard and re-emits
through its own virtual device -- so it can classify tap-vs-hold before anything
else sees the key, and emits nothing at all while held. See keyd-murmur.conf.

`keyd listen` streams layer transitions; the empty `dictate` layer exists purely
to be observed here.

Setup (needs root once):

    sudo install -Dm644 ~/murmur/linux/keyd-murmur.conf /etc/keyd/default.conf
    sudo systemctl enable --now keyd
    sudo usermod -aG keyd $USER      # then log out and back in
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module  # noqa: E402

# Reuse the socket client rather than duplicating its reconnect handling. The
# module name has a hyphen, so it cannot be imported with normal syntax.
_binder = import_module("murmur-ptt-binder")
ControlSocket = _binder.ControlSocket
socket_path = _binder.socket_path

LAYER = os.environ.get("MURMUR_KEYD_LAYER", "dictate")

log = logging.getLogger("murmur-keyd-ptt")

# `keyd listen` emits lines like "+dictate" / "-dictate", optionally prefixed
# with a device id. Match the sign and name anywhere on the line so a prefix
# does not break parsing.
_LINE = re.compile(r"([+-])(\w+)")


def run() -> int:
    ctl = ControlSocket(socket_path())
    if not os.path.exists(ctl.path):
        log.warning("shell socket %s does not exist yet — start murmur; "
                    "this will connect on the first hold", ctl.path)

    try:
        proc = subprocess.Popen(["keyd", "listen"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        log.error("keyd is not installed — sudo pacman -S keyd")
        return 1

    log.info("watching keyd for layer %r", LAYER)
    active = False
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _LINE.search(line.strip())
        if not m:
            continue
        sign, name = m.groups()
        if name != LAYER:
            continue
        if sign == "+" and not active:
            active = True
            log.info("hold -> down")
            ctl.send("down")
        elif sign == "-" and active:
            active = False
            log.info("release -> up")
            ctl.send("up")

    err = (proc.stderr.read() if proc.stderr else "").strip()
    code = proc.wait()
    # Exiting quietly on a permission error would look like "the hotkey just
    # stopped working", so say which of the two setup steps is missing.
    if "permission" in err.lower() or code != 0:
        log.error("keyd listen ended (%s): %s", code, err or "no output")
        log.error("is keyd running (systemctl status keyd) and are you in the "
                  "'keyd' group (id -nG)?")
    return code or 1


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("MURMUR_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )
    return run()


if __name__ == "__main__":
    sys.exit(main())
