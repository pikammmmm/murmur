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

set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0"; exit 1; fi
REAL_USER="${SUDO_USER:-pikammmmm}"

echo "=== 1. install ydotool ==="
pacman -S --needed --noconfirm ydotool

echo
echo "=== 2. uinput group + device access ==="
getent group uinput >/dev/null || groupadd uinput
usermod -aG uinput "$REAL_USER"
echo "  added $REAL_USER to 'uinput'"

# Load at boot, and now.
echo uinput > /etc/modules-load.d/uinput.conf
modprobe uinput || true

cat > /etc/udev/rules.d/80-uinput.rules <<'EOF'
# ydotool needs a writable /dev/uinput. Default is root:root 0600.
KERNEL=="uinput", GROUP="uinput", MODE="0660", OPTIONS+="static_node=uinput"
EOF
udevadm control --reload-rules
udevadm trigger --name-match=uinput || true
echo "  /dev/uinput -> $(stat -c '%a %G' /dev/uinput 2>/dev/null || echo 'not present until reboot')"

echo
echo "=== 3. ydotoold as a user service ==="
# ydotool talks to a daemon that owns the uinput fd. Running it per-user (not
# system-wide) keeps the socket owned by you rather than root.
install -d -o "$REAL_USER" -g "$REAL_USER" "/home/$REAL_USER/.config/systemd/user"
cat > "/home/$REAL_USER/.config/systemd/user/ydotoold.service" <<'EOF'
[Unit]
Description=ydotool daemon (uinput backend for murmur injection)

[Service]
Type=simple
ExecStart=/usr/bin/ydotoold
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
chown "$REAL_USER:$REAL_USER" "/home/$REAL_USER/.config/systemd/user/ydotoold.service"
echo "  wrote ydotoold.service"

cat <<EOF

=== DONE — but two steps are yours ===

  1. LOG OUT AND BACK IN. Group membership is applied at login; until then
     'id -Gn' won't list uinput and ydotool will fail with a permission error.

  2. Then start the daemon:
         systemctl --user enable --now ydotoold

  Verify it works (should type into whatever window has focus):
         ydotool type 'hello from ydotool'

  murmur's Linux backend already prefers ydotool over the XTEST fallback once
  the binary is present and the daemon is up — no config change needed.
EOF
