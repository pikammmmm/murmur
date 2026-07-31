# murmur on Linux — port notes

Target of this pass: Arch, KDE Plasma 6 / KWin, Wayland session with XWayland.
Verified on Python 3.14.6, Rust 1.97.1, no root.

The short version: **the sidecar is ported and proven; the Rust core is ported
and compiles; the Tauri shell cannot be built on this machine, and global
hold-to-talk key capture is the one capability that has no unprivileged
Linux equivalent.**

---

## Status at a glance

| Area | State | Evidence |
| --- | --- | --- |
| Python sidecar pipeline | Works | 250 passed, 2 skipped |
| Text injection | Works (X11/XWayland) | Round-trip test types into a real window and reads it back |
| Audio cues | Works | Rendered PCM piped to `paplay` |
| Window context | Works, including native Wayland clients (KDE) | KWin probe returns the real title/class in ~12 ms |
| Rust platform core | Compiles + tests pass | `cargo test --lib --no-default-features` → 32 passed |
| Tauri shell | **Cannot build here** | `webkit2gtk-4.1` not installed, needs root |
| Global hotkey | **Not wired** | No unprivileged system-wide key capture; see below |

---

## Building

The platform-independent core builds and tests without a webview toolkit:

```bash
cd src-tauri
cargo check --lib --no-default-features
cargo test  --lib --no-default-features     # 32 passed
cargo clippy --lib --no-default-features --all-targets
```

`--no-default-features` drops the `shell` feature and with it the `tauri`
dependency. `build.rs` skips `tauri_build::build()` unless `CARGO_FEATURE_SHELL`
is set, because that call panics with "missing `cargo:dev` instruction" when
Tauri is not in the graph.

The full shell needs system packages this machine does not have:

```bash
sudo pacman -S webkit2gtk-4.1 libsoup3      # gtk3 is already present
cargo tauri build                            # then produces src-tauri/target/release/murmur
```

Without them `cargo check` fails in the build scripts of `webkit2gtk-sys` and
`javascriptcore-rs-sys` — third-party `*-sys` crates looking for `.pc` files.
**No error originates in murmur's own code.** That is the expected stopping
point, not a porting defect.

### Sidecar

```bash
cd sidecar
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pytest
```

`requirements.txt` now uses PEP 508 markers: `pywin32` is `sys_platform ==
"win32"`, `python-xlib` is `sys_platform == "linux"`. Previously the
unconditional `pywin32` made the file uninstallable on Linux before pip ever
reached murmur's code — the same class of bug as the unconditional `windows`
and `winreg` crates in `Cargo.toml`.

---

## Architecture change: the backend seam

`injector.py`, `context.py` and `cues.py` used to import `win32clipboard`,
`win32gui` and `winsound` directly. Those OS calls now sit behind one interface
in `murmur_sidecar/backends/`:

```
backends/base.py     the interface (+ a null backend that no-ops)
backends/win32.py    the original Windows behaviour, moved verbatim
backends/linux.py    X11/Wayland
```

`get_backend()` picks by `sys.platform`; `set_backend()` swaps one in for tests.
The pipeline modules kept their public signatures and their injectable seams, so
the pre-existing tests exercise them unchanged.

Adding macOS means adding `backends/darwin.py` and nothing else.

### Linux strategy resolution

No single Linux API covers what Windows gets from one, so each capability picks
a strategy at first use and caches it. Order is session-aware:

| Capability | Wayland order | X11 order |
| --- | --- | --- |
| typing | `wtype` → `ydotool` → `xdotool` → pynput/XTEST | `xdotool` → `wtype` → `ydotool` → pynput/XTEST |
| clipboard | `wl-copy`/`wl-paste` → `xclip` → `xsel` | `xclip` → `xsel` → `wl-*` |
| window | KWin script (KDE) → python-xlib EWMH → `xprop` | same |
| cues | `paplay` → `pw-play` → `canberra-gtk-play` | same |

Inspect what a machine resolved to:

```python
from murmur_sidecar.backends import get_backend
get_backend().diagnostics()
```

On this machine that reports:

```python
{'backend': 'linux', 'session': 'wayland', 'typing': 'pynput',
 'clipboard': None, 'audio': 'paplay', 'window': 'kwin'}
```

**Recommended install** (none of these are present here, hence the pynput
fallback and the missing clipboard):

```bash
sudo pacman -S wtype wl-clipboard xdotool xorg-xprop
```

### Injection: what is actually proven

`wtype`/`ydotool`/`xdotool` are all missing on this machine, so the resolved
path is pynput, which uses the X11 **XTEST** extension. That was verified
empirically, not assumed: a test creates a real X11 window, has KWin activate
it, types through the production `injector.type_text()`, and reads the
keystrokes back off the X event queue.

```bash
cd sidecar && ./.venv/bin/python -m pytest -m desktop
```

It is opt-in (`addopts = "-m 'not desktop'"`) because it injects real keystrokes
and steals focus.

**Limitation:** XTEST reaches X11 and XWayland clients. Keystrokes injected this
way are *not* guaranteed to reach native Wayland clients — that was not verified
either way here, and the honest expectation is that they will not. Install
`wtype` for a compositor-native path.

**Paste mode is unavailable** until a clipboard tool is installed;
`set_clipboard` raises a clear error rather than silently dropping the
dictation. Type mode is the default and is unaffected.

### Window context

Wayland deliberately forbids clients from inspecting each other, so the X11
route (`_NET_ACTIVE_WINDOW` → `_NET_WM_NAME` / `_NET_WM_PID` →
`/proc/<pid>/exe`, falling back to `WM_CLASS`) is structurally blind to native
Wayland windows — which on Plasma 6 is most of them. Observed live: with a
Wayland terminal focused the EWMH probe returned `('', '')`.

The compositor is not blind, though, and KWin exposes a scripting engine over
D-Bus. On KDE the backend loads a one-shot script, reads what it printed out of
the journal, and unloads it. Verified live against a native Wayland window:

```
_active_window_kwin() -> ('xdg-desktop-portal-kde',
                          'Import Passwords to Firefox - Passwords — Mozilla Firefox')
took 0.012s
```

So context-aware formatting **works on KDE**, including native Wayland apps. The
EWMH probes remain as the answer on real X11 sessions and as a fallback.

On a non-KDE Wayland compositor there is no such escape hatch and `detect()`
falls back to `("generic", "", "")` — degraded, not broken.

`classify()` learned Linux process/WM_CLASS names (`code`, `firefox`,
`thunderbird`, `discord`, …) alongside the Windows `*.exe` names.

Deliberately *not* changed: terminals still classify as `generic`, matching
Windows behaviour. Mapping them to the `code` profile is a product decision, not
a port decision.

### Audio cues

`winsound.Beep(freq, ms)` has no Linux equivalent, so the tones are rendered to
signed-16-bit PCM in `_sine_pcm()` (with a ~4 ms fade so they do not click) and
piped to the sound server. Same four cues, same frequencies.

### Config location

`default_config_path()` follows the XDG basedir spec on Linux
(`$XDG_CONFIG_HOME/murmur/config.json`, else `~/.config/murmur/config.json`).
The Rust side already used `directories::ProjectDirs`, which does the right
thing per-platform with no change.

---

## The hotkey — the part that does not port

This is the honest gap. On Windows, `WH_KEYBOARD_LL` lets any unprivileged
process **observe and suppress** every keystroke system-wide. That is what makes
murmur's dual-function backslash work: `\` is swallowed, a quick tap synthesizes
a real `\`, a hold starts dictation.

Linux has no unprivileged equivalent:

| Option | Why it does not work here |
| --- | --- |
| evdev (`/dev/input/event*`) | Needs the `input` group. Checked: `/dev/input/event*` is `root:input 0660` and this user is in `pikammmmm wheel` only — **not readable**. Also cannot suppress a key from other clients. |
| `/dev/uinput` (ydotool daemon) | `root:root 0600`. **Not accessible** without root. |
| X11 `XGrabKey` | Only sees X11 clients. Under a Wayland session it does not see keys typed into native Wayland windows, so it is not global. |
| KDE `kglobalaccel` | Fires once on activation. **No release signal**, so it cannot express hold-to-talk. |
| **XDG GlobalShortcuts portal** | **The right answer** — see below. |

### Chosen design: the GlobalShortcuts portal

`org.freedesktop.portal.GlobalShortcuts` is the only mechanism that is (a)
unprivileged, (b) correct on Wayland, and (c) reports **both** press and
release. Confirmed present on this machine (version 2, `kde.portal` backend):

```
signal org.freedesktop.portal.GlobalShortcuts.Activated(o session_handle, s shortcut_id, t timestamp, a{sv} options)
signal org.freedesktop.portal.GlobalShortcuts.Deactivated(o session_handle, s shortcut_id, t timestamp, a{sv} options)
```

`Activated`/`Deactivated` map exactly onto `PttState::on_trigger_down` /
`on_trigger_up`.

Rather than hard-wire a D-Bus client, `src-tauri/src/hotkey/linux.rs` accepts
the events over a Unix control socket at `$XDG_RUNTIME_DIR/murmur-ptt.sock`:

```
down | press | activated      trigger pressed
up   | release | deactivated  trigger released
cancel | abort                abort the in-progress dictation (the Esc equivalent)
```

So any binder can drive it — a portal helper, or an evdev reader for a user who
*is* in the `input` group:

```bash
printf 'down\n' | nc -U "$XDG_RUNTIME_DIR/murmur-ptt.sock"
sleep 2
printf 'up\n'   | nc -U "$XDG_RUNTIME_DIR/murmur-ptt.sock"
```

The protocol and its effect on the state machine are unit-tested (6 tests in
`hotkey::imp::tests`). **The socket listener itself has never been exercised
against a real keypress** — that needs a physical key and a portal consent
dialog, neither of which can be driven from a shell. Treat the hotkey layer as
compiled and unit-tested, *not* as verified working.

### What is lost

The **dual-function text key cannot be reproduced.** Tapping `\` to type a
backslash while holding it dictates requires suppressing the original keystroke,
and no unprivileged Linux mechanism can do that. On Linux, configure a
modifier-style trigger (right Ctrl / right Alt) and bind it through the portal.

`physically_down()` also degrades: Windows cross-checks `GetAsyncKeyState` to
self-heal a key-up missed behind an elevated window. Linux has no such probe for
a key it never sees natively, so it trusts the `PENDING` flag. A binder that
drops a release event will leave murmur recording.

---

## Remaining work

1. ~~**Wire the portal binder.**~~ **DONE 2026-07-31** — `linux/murmur-ptt-binder.py`.
   Implemented as a small helper rather than a `zbus` dependency in the shell,
   so the Rust side keeps its socket seam and gains no D-Bus surface. Uses
   python-gobject (already present on KDE); `gi` is imported lazily so the
   control-socket half stays testable in a plain venv.
   **Verified live against the real portal**: `CreateSession` returned a session
   handle and `BindShortcuts` succeeded (GlobalShortcuts v2, `kde.portal`).
   7 tests in `linux/test_ptt_binder.py` cover the wire words, the socket-path
   contract with `hotkey/linux.rs`, and reconnect-after-shell-restart.
   **Still unverified: an actual keypress.** The portal reported an empty
   `trigger_description`, meaning KDE registered the shortcut but no key is
   assigned yet — set it in System Settings → Shortcuts. Until then nothing
   fires. Run with `./linux/murmur-ptt-binder.py`, or install
   `linux/murmur-ptt-binder.service` as a --user unit.
2. **Build the shell** once `webkit2gtk-4.1` is installed, then re-check
   `commands.rs`, `tray.rs` and `main.rs` — those are the only modules that
   have *not* been type-checked on Linux, because they need Tauri.
3. **Tray icon** on KDE/Wayland needs an StatusNotifierItem host; Plasma
   provides one, but this is unverified.
4. **The overlay window** uses `always_on_top`, `skip_taskbar`,
   `set_ignore_cursor_events` and absolute positioning. Under Wayland a client
   cannot position itself; expect the overlay to appear wherever KWin decides.
   This will need a KWin window rule or a layer-shell approach.
5. **`test_e2e_local.py` skips** for want of a TTS engine. `sudo pacman -S
   espeak-ng` makes it run — it is already wired to use it.
6. **`stt/directml.py`** is Windows/DirectML-only. On Linux the GPU path would
   be CUDA via faster-whisper; the `gpu` provider should be hidden or remapped
   in the settings UI.

---

## Verification log

Commands actually run on this machine, with their results:

```
$ ./.venv-linux/bin/python -m pytest -q          # sidecar
250 passed, 2 skipped, 1 deselected

$ ./.venv-linux/bin/python -m pytest -m desktop  # real keystroke injection
1 passed

$ cargo test --lib --no-default-features
test result: ok. 32 passed; 0 failed

$ cargo clippy --lib --no-default-features --all-targets
Finished (no warnings)

$ cargo check                                    # full shell
error: failed to run custom build command for `webkit2gtk-sys v2.0.2`
error: failed to run custom build command for `javascriptcore-rs-sys v1.1.1`
```

The two skips are `torch_directml` (Windows-only GPU path) and the local-STT
end-to-end test (no TTS engine to synthesize its fixture).
