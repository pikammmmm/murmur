#!/usr/bin/env bash
# murmur — Arch Linux uninstaller. Reverses linux/install.sh.
#
#     bash linux/uninstall.sh
#
# Stops and disables the services, removes the systemd user units and the keyd
# config, and drops the group memberships murmur added. It does NOT delete the
# checkout, the sidecar venv, or your settings/history in
# ~/.local/share/murmur -- remove those by hand if you want them gone.
#
# System packages (keyd, ydotool, …) are left installed: they are ordinary
# system tools that other things may rely on.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

info() { printf '      %s\n' "$1"; }
say()  { printf '\n\033[1;36m%s\033[0m\n' "$1"; }

[[ $EUID -eq 0 ]] && { echo "run as your normal user, not root" >&2; exit 1; }

say "Stopping services"
systemctl --user disable --now murmur.service murmur-keyd-ptt.service 2>/dev/null || true
systemctl --user disable --now 'app-murmur\x2dptt@autostart.service' 2>/dev/null || true
info "stopped"

say "Removing systemd user units"
for u in murmur.service murmur-keyd-ptt.service 'app-murmur\x2dptt@.service'; do
  rm -f "$USER_UNITS/$u"
done
rm -rf "$USER_UNITS/murmur.service.d" "$USER_UNITS/murmur-keyd-ptt.service.d"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/murmur-ptt.desktop"
systemctl --user daemon-reload
info "removed"

say "Removing the keyd trigger"
# Only touch keyd's config if it is still OURS -- a config the user has since
# customised, or one that predates murmur, is not ours to delete.
if diff -q "$REPO/linux/keyd-murmur.conf" /etc/keyd/default.conf &>/dev/null; then
  sudo rm -f /etc/keyd/default.conf
  if [[ -e /etc/keyd/default.conf.bak ]]; then
    sudo mv /etc/keyd/default.conf.bak /etc/keyd/default.conf
    info "restored the config that was there before murmur"
  else
    info "removed /etc/keyd/default.conf"
  fi
  sudo systemctl restart keyd 2>/dev/null || true
else
  info "/etc/keyd/default.conf is not murmur's — left alone"
fi
sudo rm -f /etc/systemd/system/keyd.service.d/restart.conf
sudo rmdir /etc/systemd/system/keyd.service.d 2>/dev/null || true
sudo systemctl daemon-reload

say "Group membership"
# `input` is deliberately NOT removed: ydotool and plenty of unrelated software
# depend on it, and silently revoking it would break them.
if id -nG | tr ' ' '\n' | grep -qx keyd; then
  sudo gpasswd -d "$USER" keyd >/dev/null && info "removed $USER from 'keyd'"
else
  info "not in 'keyd'"
fi
info "left 'input' alone (ydotool and other tools use it)"

cat <<EOF

  Uninstalled. Still on disk, remove by hand if you want them gone:

    $REPO                       the checkout (incl. sidecar/.venv)
    ~/.local/share/murmur       settings, dictation history, logs

EOF
