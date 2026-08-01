#!/usr/bin/env bash
# murmur — Arch Linux installer.
#
#     bash linux/install.sh
#
# Builds murmur from this checkout and wires up everything it needs to work as a
# background dictation service: system packages, the Python sidecar venv, the
# keyd push-to-talk trigger, ydotool keystroke injection, and systemd user units
# that bring it all up at login.
#
# Works from wherever the repo is checked out. Run it as YOUR user, not root --
# it calls sudo only for the handful of steps that genuinely need it, and tells
# you which those are before it does.
#
# Idempotent: safe to re-run after a `git pull` to rebuild and re-apply.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
STEPS_TOTAL=8
step=0

say()  { printf '\n\033[1;36m[%d/%d] %s\033[0m\n' "$((++step))" "$STEPS_TOTAL" "$1"; }
info() { printf '      %s\n' "$1"; }
warn() { printf '\033[1;33m      ! %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "run as your normal user, not root — the script sudo's only where needed"
command -v pacman >/dev/null || die "this installer is Arch-specific (no pacman found).
See docs/LINUX-PORT-NOTES.md for what to install by hand on another distro."

# --------------------------------------------------------------------------
say "System packages"
# webkit2gtk-4.1 + libappindicator-gtk3 + librsvg are Tauri's GTK/WebKit stack;
# keyd provides the bare-backslash trigger; ydotool injects the transcript;
# python-gobject is only needed by the portal fallback binder.
PKGS=(base-devel rust webkit2gtk-4.1 libappindicator-gtk3 librsvg
      keyd ydotool python python-gobject xdg-desktop-portal)
missing=()
for p in "${PKGS[@]}"; do pacman -Q "$p" &>/dev/null || missing+=("$p"); done
if ((${#missing[@]})); then
  info "installing: ${missing[*]}"
  sudo pacman -S --needed --noconfirm "${missing[@]}"
else
  info "all present"
fi
command -v cargo >/dev/null || die "cargo not on PATH after installing rust — open a new shell and re-run"

# --------------------------------------------------------------------------
say "Python sidecar"
if [[ ! -x "$REPO/sidecar/.venv/bin/python" ]]; then
  info "creating $REPO/sidecar/.venv"
  python -m venv "$REPO/sidecar/.venv"
fi
info "installing requirements (quiet; this can take a minute)"
"$REPO/sidecar/.venv/bin/pip" install -q --upgrade pip
"$REPO/sidecar/.venv/bin/pip" install -q -r "$REPO/sidecar/requirements.txt"
info "done"

# --------------------------------------------------------------------------
say "Building the shell (release)"
# The running binary cannot be replaced while it is executing, so stop the
# service first -- otherwise cargo fails at the link step with a cryptic
# "Text file busy".
systemctl --user stop murmur.service 2>/dev/null || true
( cd "$REPO/src-tauri" && cargo build --release )
BIN="$REPO/src-tauri/target/release/murmur"
[[ -x "$BIN" ]] || die "build succeeded but $BIN is missing"
info "built $BIN"

# --------------------------------------------------------------------------
say "Push-to-talk trigger (keyd)"
# keyd grabs the physical keyboard below the compositor, which is the only way
# to get a BARE `\` that still types a backslash on a tap. See keyd-murmur.conf.
if ! diff -q "$REPO/linux/keyd-murmur.conf" /etc/keyd/default.conf &>/dev/null; then
  if [[ -e /etc/keyd/default.conf ]]; then
    warn "/etc/keyd/default.conf exists and differs — backing it up to default.conf.bak"
    sudo cp -a /etc/keyd/default.conf /etc/keyd/default.conf.bak
  fi
  sudo install -Dm644 "$REPO/linux/keyd-murmur.conf" /etc/keyd/default.conf
  info "installed /etc/keyd/default.conf"
else
  info "config already current"
fi
# v2.6.0 segfaults on `keyd reload`, and the stock unit has no Restart=, so a
# crash would silently kill the hotkey. restart (never reload) + a drop-in.
sudo mkdir -p /etc/systemd/system/keyd.service.d
sudo tee /etc/systemd/system/keyd.service.d/restart.conf >/dev/null <<'EOF'
# keyd v2.6.0 can segfault (reproduced on `keyd reload`), and the stock unit has
# no Restart=, which would leave push-to-talk silently dead. Recovering is always
# safe: keyd releases its grab on exit, so the worst case is a stock keyboard for
# RestartSec.
[Service]
Restart=on-failure
RestartSec=2
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now keyd
sudo systemctl restart keyd
id -nG "$USER" | tr ' ' '\n' | grep -qx keyd || sudo usermod -aG keyd "$USER"
info "keyd running; $USER is in the 'keyd' group"

# --------------------------------------------------------------------------
say "Keystroke injection (ydotool)"
# KWin implements neither zwp_virtual_keyboard_manager_v1 nor
# zwp_input_method_manager_v2, so wtype can never work here, and the XTEST path
# only reaches XWayland clients. ydotool writes to /dev/uinput, so the kernel
# delivers events as if from a real keyboard -- which reaches every client.
id -nG "$USER" | tr ' ' '\n' | grep -qx input || sudo usermod -aG input "$USER"
sudo udevadm control --reload-rules || true
sudo udevadm trigger --name-match=uinput || true
systemctl --user enable --now ydotool.service 2>/dev/null \
  && info "ydotool daemon running" \
  || warn "could not start ydotool.service — check 'systemctl --user status ydotool'"

# --------------------------------------------------------------------------
say "systemd user units"
mkdir -p "$USER_UNITS"
# Symlinks, not copies, so `git pull` updates the units too.
for u in murmur.service murmur-keyd-ptt.service 'app-murmur\x2dptt@.service'; do
  ln -sfn "$REPO/linux/systemd/$u" "$USER_UNITS/$u"
done
# ExecStart= is absolute, so a checkout outside ~/murmur needs the units
# rewritten to match. Do it in a drop-in rather than editing the tracked unit,
# which would leave the repo permanently dirty.
if [[ "$REPO" != "$HOME/murmur" ]]; then
  info "checkout is not ~/murmur — writing path overrides"
  for u in murmur murmur-keyd-ptt; do
    mkdir -p "$USER_UNITS/$u.service.d"
    { echo "[Service]"; echo "ExecStart="; } > "$USER_UNITS/$u.service.d/path.conf"
  done
  echo "ExecStart=$BIN" >> "$USER_UNITS/murmur.service.d/path.conf"
  echo "ExecStart=/usr/bin/sg keyd -c \"/usr/bin/python3 -u $REPO/linux/murmur-keyd-ptt.py\"" \
    >> "$USER_UNITS/murmur-keyd-ptt.service.d/path.conf"
fi
# The portal fallback binder needs a desktop entry to exist, or KDE files its
# shortcut under whatever launched the process (a terminal) instead of under
# murmur. Rendered rather than symlinked: a desktop entry has no "%h".
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
sed "s#@REPO@#$REPO#g" "$REPO/linux/murmur-ptt.desktop" > "$APPS/murmur-ptt.desktop"

systemctl --user daemon-reload
# The portal binder and the keyd binder both write to the same control socket,
# so exactly one may be enabled. keyd is the better trigger, so it wins.
systemctl --user disable --now 'app-murmur\x2dptt@autostart.service' 2>/dev/null || true
systemctl --user enable --now murmur-keyd-ptt.service
systemctl --user enable --now murmur.service
info "enabled murmur.service + murmur-keyd-ptt.service"

# --------------------------------------------------------------------------
say "Verifying"
sleep 4
fail=0
check() { # name, command
  if eval "$2" &>/dev/null; then printf '      \033[32m✓\033[0m %s\n' "$1"
  else printf '      \033[31m✗\033[0m %s\n' "$1"; fail=1; fi
}
check "keyd running"                 "systemctl is-active --quiet keyd"
check "murmur shell running"         "systemctl --user is-active --quiet murmur.service"
check "push-to-talk binder running"  "systemctl --user is-active --quiet murmur-keyd-ptt.service"
check "control socket present"       "test -S '${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/murmur-ptt.sock'"
check "starts at login"              "test -L '$USER_UNITS/graphical-session.target.wants/murmur.service'"

# --------------------------------------------------------------------------
say "Done"
if ((fail)); then
  warn "some checks failed — see 'journalctl --user -u murmur -u murmur-keyd-ptt -n 50'"
else
  info "all checks passed"
fi
cat <<EOF

  Hold  \\  and speak; release and the text is typed into the focused field.
  A quick tap of  \\  still types a backslash.

  Settings live in the tray icon. With no API keys murmur transcribes locally
  and offline; add a key in Settings for faster cloud transcription.

EOF
if ! id -nG | tr ' ' '\n' | grep -qx keyd || ! id -nG | tr ' ' '\n' | grep -qx input; then
  warn "New group membership (keyd/input) applies fully at your next login."
  warn "It works now regardless — the unit wraps the binder in 'sg keyd'."
fi
echo "  Uninstall with: bash linux/uninstall.sh"
echo
