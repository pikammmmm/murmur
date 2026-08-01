//! Global hold-to-talk hotkey.
//!
//! The trigger vocabulary, the shared state and the tick loop live here; how
//! key events are actually *observed* is platform-specific and lives in the
//! `imp` submodule:
//!
//!   * Windows — a `WH_KEYBOARD_LL` hook, which can both observe and *suppress*
//!     keys, so a text key like `\` can be dual-function (tap types the
//!     character, hold dictates).
//!   * Linux — an external binder pushes press/release over a control socket,
//!     because an unprivileged process cannot intercept keys system-wide.
//!     See docs/LINUX-PORT-NOTES.md; the dual-function text-key trick does not
//!     survive the port.
//!
//! The callback and configured trigger live in statics because the Windows hook
//! callback must be a bare `extern "system" fn` with no captured environment.
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::ptt::{Action, PttState};

#[cfg_attr(not(windows), allow(dead_code))]
mod vk {
    pub const SHIFT: u32 = 0x10;
    pub const LSHIFT: u32 = 0xA0;
    pub const RSHIFT: u32 = 0xA1;
    pub const RCONTROL: u32 = 0xA3;
    pub const RMENU: u32 = 0xA5; // right Alt
    pub const CAPITAL: u32 = 0x14; // Caps Lock
    pub const OEM_5: u32 = 0xDC; // the '\' key (US layout)
    pub const ESCAPE: u32 = 0x1B; // aborts an in-progress dictation
}

#[cfg(windows)]
#[path = "windows.rs"]
mod imp;

#[cfg(not(windows))]
#[path = "linux.rs"]
mod imp;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Trigger {
    ShiftEither = 0,
    ShiftLeft = 1,
    ShiftRight = 2,
    RightCtrl = 3,
    RightAlt = 4,
    CapsLock = 5,
    Backslash = 6,
}

pub fn trigger_from_config(key: &str, side: &str) -> Trigger {
    match key.to_lowercase().as_str() {
        "shift" => match side.to_lowercase().as_str() {
            "left" => Trigger::ShiftLeft,
            "right" => Trigger::ShiftRight,
            _ => Trigger::ShiftEither,
        },
        "rctrl" | "right-ctrl" | "rightctrl" => Trigger::RightCtrl,
        "ralt" | "right-alt" | "rightalt" => Trigger::RightAlt,
        "capslock" | "caps" => Trigger::CapsLock,
        "backslash" | "\\" => Trigger::Backslash,
        _ => Trigger::Backslash,
    }
}

/// A "text" trigger produces a character, so we suppress it and synthesize a tap
/// when held only briefly (vs. observed-only modifier keys like Shift).
pub(crate) fn is_text_key(t: Trigger) -> bool {
    matches!(t, Trigger::Backslash)
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn is_trigger(vk: u32, t: Trigger) -> bool {
    match t {
        Trigger::ShiftEither => matches!(vk, vk::SHIFT | vk::LSHIFT | vk::RSHIFT),
        Trigger::ShiftLeft => vk == vk::LSHIFT,
        Trigger::ShiftRight => vk == vk::RSHIFT,
        Trigger::RightCtrl => vk == vk::RCONTROL,
        Trigger::RightAlt => vk == vk::RMENU,
        Trigger::CapsLock => vk == vk::CAPITAL,
        Trigger::Backslash => vk == vk::OEM_5,
    }
}

#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn trigger_vks(t: Trigger) -> &'static [i32] {
    match t {
        Trigger::ShiftEither => &[vk::SHIFT as i32],
        Trigger::ShiftLeft => &[vk::LSHIFT as i32],
        Trigger::ShiftRight => &[vk::RSHIFT as i32],
        Trigger::RightCtrl => &[vk::RCONTROL as i32],
        Trigger::RightAlt => &[vk::RMENU as i32],
        Trigger::CapsLock => &[vk::CAPITAL as i32],
        Trigger::Backslash => &[vk::OEM_5 as i32],
    }
}

pub(crate) static STATE: OnceLock<Mutex<PttState>> = OnceLock::new();
static CALLBACK: OnceLock<Box<dyn Fn(Action) + Send + Sync>> = OnceLock::new();
static START: OnceLock<Instant> = OnceLock::new();
static TRIG: AtomicU8 = AtomicU8::new(0);
static PAUSED: AtomicBool = AtomicBool::new(false);
/// For a text trigger: true while the key is physically down + suppressed.
/// On Linux this is the *only* record of the key being down, since there is no
/// equivalent of `GetAsyncKeyState` for a key we never see natively.
pub(crate) static PENDING: AtomicBool = AtomicBool::new(false);

/// Suspend/resume the hotkey without uninstalling the hook (tray Pause).
pub fn set_paused(paused: bool) {
    PAUSED.store(paused, Ordering::Relaxed);
}

pub(crate) fn now_ms() -> u64 {
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

pub(crate) fn current_trigger() -> Trigger {
    match TRIG.load(Ordering::Relaxed) {
        1 => Trigger::ShiftLeft,
        2 => Trigger::ShiftRight,
        3 => Trigger::RightCtrl,
        4 => Trigger::RightAlt,
        5 => Trigger::CapsLock,
        6 => Trigger::Backslash,
        _ => Trigger::ShiftEither,
    }
}

pub(crate) fn fire(action: Action) {
    if PAUSED.load(Ordering::Relaxed) {
        return; // hotkey suspended from the tray
    }
    if let Some(cb) = CALLBACK.get() {
        cb(action);
    }
}

/// Install the platform listener + timer. `on_action` receives Start/Stop.
/// Idempotent-ish: call once at startup.
pub fn spawn<F>(trigger: Trigger, threshold_ms: u64, on_action: F)
where
    F: Fn(Action) + Send + Sync + 'static,
{
    STATE.get_or_init(|| Mutex::new(PttState::new(threshold_ms)));
    START.get_or_init(Instant::now);
    let _ = CALLBACK.set(Box::new(on_action));
    TRIG.store(trigger as u8, Ordering::Relaxed);

    std::thread::spawn(timer_loop);
    imp::install();
}

/// Apply a new trigger/threshold without reinstalling the hook (used when the
/// user changes the hotkey in settings).
pub fn reconfigure(trigger: Trigger, threshold_ms: u64) {
    TRIG.store(trigger as u8, Ordering::Relaxed);
    if let Some(state) = STATE.get() {
        state.lock().unwrap().set_threshold(threshold_ms);
    }
}

fn timer_loop() {
    loop {
        std::thread::sleep(Duration::from_millis(20));
        let Some(state) = STATE.get() else { continue };
        let now = now_ms();
        let trig = current_trigger();
        let action = {
            let mut st = state.lock().unwrap();
            // Detect a missed key-up so we can self-heal. CRITICAL: a *suppressed*
            // text key (we return LRESULT(1) for '\') is invisible to
            // GetAsyncKeyState — it reports the key as up even while it's held —
            // so probing it would cancel the arm ~20ms in and recording would
            // never start. For text keys we trust our own PENDING flag (we see
            // every down/up ourselves); only observed modifier keys, whose up we
            // can genuinely miss behind an elevated window, use the physical probe.
            let key_released = if is_text_key(trig) {
                !PENDING.load(Ordering::SeqCst)
            } else {
                !imp::physically_down(trig)
            };
            if st.is_cancelled() {
                // A dictation was aborted with Esc. Once the trigger is physically
                // up — even if the key-up event was missed behind an elevated
                // window — clear the latch so the next press can record again.
                if key_released {
                    st.clear_cancelled();
                }
                None
            } else if (st.is_recording() || st.is_armed()) && key_released {
                let a = st.on_trigger_up(now);
                PENDING.store(false, Ordering::SeqCst); // clear any stuck text-key state
                a
            } else {
                st.on_tick(now)
            }
        };
        if let Some(a) = action {
            fire(a);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_maps_to_trigger() {
        assert_eq!(trigger_from_config("shift", "either"), Trigger::ShiftEither);
        assert_eq!(trigger_from_config("shift", "left"), Trigger::ShiftLeft);
        assert_eq!(trigger_from_config("shift", "right"), Trigger::ShiftRight);
        assert_eq!(trigger_from_config("rctrl", ""), Trigger::RightCtrl);
        assert_eq!(trigger_from_config("ralt", ""), Trigger::RightAlt);
        assert_eq!(trigger_from_config("capslock", ""), Trigger::CapsLock);
        assert_eq!(trigger_from_config("backslash", ""), Trigger::Backslash);
        assert_eq!(trigger_from_config("\\", ""), Trigger::Backslash);
        assert_eq!(trigger_from_config("whatever", ""), Trigger::Backslash); // default is now '\'
    }

    #[test]
    fn backslash_is_a_text_key() {
        assert!(is_text_key(Trigger::Backslash));
        assert!(!is_text_key(Trigger::ShiftEither));
        assert!(is_trigger(vk::OEM_5, Trigger::Backslash));
        assert!(!is_trigger(vk::SHIFT, Trigger::Backslash));
    }

    #[test]
    fn is_trigger_matches_expected_vks() {
        assert!(is_trigger(vk::SHIFT, Trigger::ShiftEither));
        assert!(is_trigger(vk::LSHIFT, Trigger::ShiftEither));
        assert!(is_trigger(vk::RSHIFT, Trigger::ShiftEither));
        assert!(is_trigger(vk::LSHIFT, Trigger::ShiftLeft));
        assert!(!is_trigger(vk::RSHIFT, Trigger::ShiftLeft));
        assert!(is_trigger(vk::RCONTROL, Trigger::RightCtrl));
        assert!(!is_trigger(vk::SHIFT, Trigger::RightCtrl));
    }
}
