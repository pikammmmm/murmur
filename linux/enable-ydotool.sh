#!/usr/bin/env bash
# Make keystroke injection work in NATIVE WAYLAND apps on KDE.
#
#   sudo bash ~/murmur/linux/enable-ydotool.sh
#
# Why this is needed at all:
#   wtype is the usual Wayland answer, but it requires the compositor to
#   implement zwp_virtual_keyboard_manager_v1. KWin implements neither that nor
#   zwp_input_method_manager_v2, so wtype exits with "Compositor does not
#   support the virtual keyboard protocol" and can never work here.
#
#   The XTEST path (xdotool / pynput) does work, but only reaches XWayland
#   clients. Typing into a native Wayland window silently does nothing — the
#   call reports success and the text goes nowhere.
#
#   ydotool sidesteps the compositor entirely by writing to /dev/uinput, so the
#   kernel delivers the events as if from a real keyboard. That works in every
#   client regardless of protocol support.
#
# The tradeoff, stated plainly: this grants your user account the ability to
# synthesize arbitrary input system-wide. Any process running as you can then
# type into any window. That is inherent to the approach, not to this script.
#
# The Arch package already ships the udev rule AND the systemd user unit, so
# this script only clears breakage and sets group membership. Idempotent.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0"; exit 1; fi
REAL_USER="${SUDO_USER:-pikammmmm}"

echo "=== 1. clear a stale pacman lock, if any ==="
# A pacman run killed mid-transaction leaves /var/lib/pacman/db.lck behind AND
# can leave the package half-extracted as 0-byte files. Every later pacman then
# fails with "unable to lock database", so the breakage compounds silently.
if [[ -e /var/lib/pacman/db.lck ]]; then
  if pgrep -x 'pacman|paru|yay|pamac' >/dev/null 2>&1; then
    echo "  a package manager IS running — refusing to touch the lock. Re-run later."
    exit 1
  fi
  rm -f /var/lib/pacman/db.lck
  echo "  removed stale lock"
else
  echo "  no lock present"
fi

echo
echo "=== 2. (re)install ydotool ==="
# NOT --needed: pacman's DB can claim ydotool is installed while the files on
# disk are 0 bytes from an interrupted transaction. --needed trusts the DB and
# skips the repair.
#
# --overwrite: when that interrupted transaction is rolled back with -Rdd, the
# corrupt DB entry has no file list, so pacman leaves the real files orphaned on
# disk. The next install then aborts with "exists in filesystem". These three
# paths all belong to ydotool itself, so adopting them is safe — the flag is
# scoped to this transaction, which contains only this package.
if pacman -Q ydotool >/dev/null 2>&1 && [[ ! -s /usr/bin/ydotoold ]]; then
  echo "  DB says installed but binary is empty — forcing a clean reinstall"
  pacman -Rdd --noconfirm ydotool 2>/dev/null || true
fi
pacman -S --noconfirm \
  --overwrite '/usr/lib/udev/rules.d/80-uinput.rules' \
  --overwrite '/usr/share/man/man1/ydotool.1.gz' \
  --overwrite '/usr/share/man/man8/ydotoold.8.gz' \
  ydotool

for b in /usr/bin/ydotool /usr/bin/ydotoold; do
  [[ -s "$b" ]] || { echo "  FAILED: $b still empty after reinstall." >&2; exit 1; }
done
echo "  ydotool $(pacman -Q ydotool | awk '{print $2}') installed, binaries non-empty"

echo
echo "=== 3. /dev/uinput access ==="
# The package ships /usr/lib/udev/rules.d/80-uinput.rules, which grants group
# `input` — NOT `uinput`. Do not create a `uinput` group and do not write a
# competing rule into /etc/udev/rules.d: /etc wins over /usr/lib for a
# same-named file, so a hand-rolled copy would silently override the packaged
# one and hand the device to a group nothing else uses.
usermod -aG input "$REAL_USER"
echo "  $REAL_USER added to 'input'"

# `|| true` on both: udevadm returns non-zero in containers and on transient
# reload races. Under `set -e` that aborts the script here, before anything
# below runs — which is how an earlier version of this script died silently.
udevadm control --reload-rules || true
udevadm trigger --name-match=uinput || true
echo "  /dev/uinput -> $(stat -c '%a %G' /dev/uinput 2>/dev/null || echo 'absent until reboot')"

cat <<EOF

=== DONE — two steps are yours ===

  1. REBOOT (or at minimum log out and back in). Group membership is applied at
     login, and the udev rule's static_node= only takes effect as the uinput
     module loads. Until then 'id -nG' won't list input and ydotool fails with a
     permission error on /dev/uinput.

  2. Then start the daemon — the unit is the packaged one, named 'ydotool':
         systemctl --user enable --now ydotool

  Verify (types into whatever window has focus):
         ydotool type 'hello from ydotool'

  murmur's Linux backend already prefers ydotool over the XTEST fallback once
  the binary is present and the daemon is up — no config change needed.
EOF
