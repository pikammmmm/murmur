//! Linux key observation: a control socket driven by an external binder.
//!
//! THIS IS THE PART THAT DOES NOT PORT CLEANLY. On Windows a `WH_KEYBOARD_LL`
//! hook lets any process observe *and suppress* every keystroke system-wide.
//! Linux has no unprivileged equivalent:
//!
//!   * evdev (`/dev/input/event*`) sees every key, but needs membership of the
//!     `input` group — and it still cannot suppress a key from other clients.
//!   * X11 `XGrabKey` only works for X11 clients, and under a Wayland session
//!     an X11 grab does not see keys typed into native Wayland windows.
//!   * The compositor is the only component that can do this properly, and it
//!     exposes that through the XDG desktop portal.
//!
//! So instead of guessing, this module accepts press/release over a Unix socket
//! and lets an external binder decide how the key is captured. The intended
//! production binder is the `org.freedesktop.portal.GlobalShortcuts` portal,
//! which is the one mechanism that (a) needs no root, (b) works on Wayland, and
//! (c) reports both `Activated` and `Deactivated` — press *and* release, which
//! hold-to-talk requires and which plain KDE `kglobalaccel` shortcuts do not
//! provide. See LINUX-PORT-NOTES.md for the wiring.
//!
//! The dual-function text key (tap `\` to type a backslash, hold it to dictate)
//! is out of reach for the portal specifically: its grab is by key, so a bare
//! printable trigger is swallowed desktop-wide and re-injecting the character
//! only feeds the grab again. It is reachable one layer lower — `keyd` grabs the
//! physical device and re-emits through its own, so it can resolve tap-vs-hold
//! before the compositor sees anything. That is the production binder here
//! (`linux/murmur-keyd-ptt.py` + `linux/keyd-murmur.conf`, root once to install);
//! the portal binder remains as the no-root fallback, on a modifier-style
//! trigger. Either way this module just reads down/up off the socket.
use std::io::{BufRead, BufReader};
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use super::{fire, now_ms, Trigger, PENDING, STATE};
use crate::ptt::{Action, PttState};

/// One line of the control protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Cmd {
    /// Trigger pressed (portal `Activated`).
    Down,
    /// Trigger released (portal `Deactivated`).
    Up,
    /// Abort an in-progress dictation (the Esc equivalent).
    Cancel,
}

pub(crate) fn parse_cmd(line: &str) -> Option<Cmd> {
    match line.trim().to_ascii_lowercase().as_str() {
        "down" | "press" | "activated" => Some(Cmd::Down),
        "up" | "release" | "deactivated" => Some(Cmd::Up),
        "cancel" | "abort" => Some(Cmd::Cancel),
        _ => None,
    }
}

/// Drive the shared PTT state machine from one control command.
///
/// Pure with respect to the socket, so the protocol is unit-testable without a
/// desktop session: the caller owns the state.
pub(crate) fn apply(st: &mut PttState, cmd: Cmd, now: u64) -> Option<Action> {
    match cmd {
        Cmd::Down => {
            PENDING.store(true, Ordering::SeqCst);
            st.on_trigger_down(now);
            None
        }
        Cmd::Up => {
            PENDING.store(false, Ordering::SeqCst);
            st.on_trigger_up(now)
        }
        Cmd::Cancel => st.cancel(),
    }
}

/// Where the control socket lives. Prefers the per-user runtime dir (tmpfs,
/// cleaned up at logout); falls back to a user-qualified name under the temp
/// dir so two users on one machine cannot collide.
pub(crate) fn socket_path() -> PathBuf {
    match std::env::var("XDG_RUNTIME_DIR") {
        Ok(dir) if !dir.is_empty() => PathBuf::from(dir).join("murmur-ptt.sock"),
        _ => {
            let user = std::env::var("USER").unwrap_or_else(|_| "user".into());
            std::env::temp_dir().join(format!("murmur-ptt-{user}.sock"))
        }
    }
}

/// The trigger is only ever "down" when the binder told us so — there is no
/// `GetAsyncKeyState` equivalent for a key we never observe natively, so unlike
/// Windows we cannot cross-check and self-heal a missed release.
pub(super) fn physically_down(_t: Trigger) -> bool {
    PENDING.load(Ordering::SeqCst)
}

pub(super) fn install() {
    std::thread::spawn(|| {
        let path = socket_path();
        // A socket left behind by a crashed run would block bind().
        let _ = std::fs::remove_file(&path);
        let listener = match UnixListener::bind(&path) {
            Ok(l) => l,
            Err(e) => {
                crate::mlog!("hotkey: cannot bind {} — {e}; hotkey won't work", path.display());
                return;
            }
        };
        crate::mlog!("hotkey: listening on {}", path.display());
        for stream in listener.incoming() {
            let Ok(stream) = stream else { continue };
            for line in BufReader::new(stream).lines() {
                let Ok(line) = line else { break };
                let Some(cmd) = parse_cmd(&line) else {
                    crate::mlog!("hotkey: ignoring unknown command {line:?}");
                    continue;
                };
                let Some(state) = STATE.get() else { continue };
                let action = {
                    let mut st = state.lock().unwrap();
                    apply(&mut st, cmd, now_ms())
                };
                if let Some(a) = action {
                    fire(a);
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_protocol_including_portal_signal_names() {
        assert_eq!(parse_cmd("down"), Some(Cmd::Down));
        assert_eq!(parse_cmd("Activated\n"), Some(Cmd::Down));
        assert_eq!(parse_cmd(" UP "), Some(Cmd::Up));
        assert_eq!(parse_cmd("deactivated"), Some(Cmd::Up));
        assert_eq!(parse_cmd("cancel"), Some(Cmd::Cancel));
        assert_eq!(parse_cmd("nonsense"), None);
        assert_eq!(parse_cmd(""), None);
    }

    #[test]
    fn hold_past_the_threshold_starts_then_stops_recording() {
        let mut st = PttState::new(300);
        assert_eq!(apply(&mut st, Cmd::Down, 0), None);
        // The tick loop is what actually starts recording once the hold passes
        // the threshold; simulate that tick here.
        assert_eq!(st.on_tick(400), Some(Action::StartRecording));
        assert!(st.is_recording());
        assert_eq!(apply(&mut st, Cmd::Up, 500), Some(Action::StopRecording));
        assert!(!st.is_recording());
    }

    #[test]
    fn a_quick_tap_does_not_record() {
        let mut st = PttState::new(300);
        apply(&mut st, Cmd::Down, 0);
        assert_eq!(apply(&mut st, Cmd::Up, 100), None); // released before threshold
        assert!(!st.is_recording());
    }

    #[test]
    fn cancel_aborts_an_in_progress_dictation() {
        let mut st = PttState::new(300);
        apply(&mut st, Cmd::Down, 0);
        st.on_tick(400);
        assert!(st.is_recording());
        assert_eq!(apply(&mut st, Cmd::Cancel, 450), Some(Action::Cancel));
        assert!(!st.is_recording());
    }

    #[test]
    fn down_sets_pending_and_up_clears_it() {
        let mut st = PttState::new(300);
        apply(&mut st, Cmd::Down, 0);
        assert!(PENDING.load(Ordering::SeqCst));
        apply(&mut st, Cmd::Up, 10);
        assert!(!PENDING.load(Ordering::SeqCst));
    }

    #[test]
    fn socket_path_prefers_the_runtime_dir() {
        // SAFETY: single-threaded test; restores nothing because the fallback
        // branch is covered by the assertion on the returned path shape.
        let previous = std::env::var("XDG_RUNTIME_DIR").ok();
        std::env::set_var("XDG_RUNTIME_DIR", "/run/user/1000");
        assert_eq!(socket_path(), PathBuf::from("/run/user/1000/murmur-ptt.sock"));
        std::env::set_var("XDG_RUNTIME_DIR", "");
        assert!(socket_path().to_string_lossy().contains("murmur-ptt-"));
        match previous {
            Some(v) => std::env::set_var("XDG_RUNTIME_DIR", v),
            None => std::env::remove_var("XDG_RUNTIME_DIR"),
        }
    }
}
