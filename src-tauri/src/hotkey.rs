//! Global hold-to-talk hotkey via a `WH_KEYBOARD_LL` hook.
//!
//! The low-level hook callback must be a bare `extern "system" fn`, so shared
//! state (the PttState, the action callback, the configured trigger) lives in
//! statics. A 20ms timer thread drives `on_tick` (the hold-alone threshold) and
//! self-heals stuck state by cross-checking the physical key with
//! `GetAsyncKeyState` — a key-up that lands while an elevated window is focused
//! never reaches our hook (per glassbar's keyhook notes), so we must not trust
//! our own state blindly. The trigger key is never suppressed.
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP,
    VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, SetWindowsHookExW, TranslateMessage, HHOOK,
    KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

const LLKHF_INJECTED: u32 = 0x10;

use crate::ptt::{Action, PttState};

const VK_SHIFT: u32 = 0x10;
const VK_LSHIFT: u32 = 0xA0;
const VK_RSHIFT: u32 = 0xA1;
const VK_RCONTROL: u32 = 0xA3;
const VK_RMENU: u32 = 0xA5; // right Alt
const VK_CAPITAL: u32 = 0x14; // Caps Lock
const VK_OEM_5: u32 = 0xDC; // the '\' key (US layout)

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
fn is_text_key(t: Trigger) -> bool {
    matches!(t, Trigger::Backslash)
}

fn is_trigger(vk: u32, t: Trigger) -> bool {
    match t {
        Trigger::ShiftEither => matches!(vk, VK_SHIFT | VK_LSHIFT | VK_RSHIFT),
        Trigger::ShiftLeft => vk == VK_LSHIFT,
        Trigger::ShiftRight => vk == VK_RSHIFT,
        Trigger::RightCtrl => vk == VK_RCONTROL,
        Trigger::RightAlt => vk == VK_RMENU,
        Trigger::CapsLock => vk == VK_CAPITAL,
        Trigger::Backslash => vk == VK_OEM_5,
    }
}

fn trigger_vks(t: Trigger) -> &'static [i32] {
    match t {
        Trigger::ShiftEither => &[VK_SHIFT as i32],
        Trigger::ShiftLeft => &[VK_LSHIFT as i32],
        Trigger::ShiftRight => &[VK_RSHIFT as i32],
        Trigger::RightCtrl => &[VK_RCONTROL as i32],
        Trigger::RightAlt => &[VK_RMENU as i32],
        Trigger::CapsLock => &[VK_CAPITAL as i32],
        Trigger::Backslash => &[VK_OEM_5 as i32],
    }
}

/// Synthesize a real keypress (used to "type" the trigger char after a short
/// tap). Marked injected so our own hook ignores it.
unsafe fn synth_key(vk: u16) {
    let mk = |key_up: bool| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: 0,
                dwFlags: if key_up { KEYEVENTF_KEYUP } else { Default::default() },
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    let inputs = [mk(false), mk(true)];
    SendInput(&inputs, std::mem::size_of::<INPUT>() as i32);
}

static STATE: OnceLock<Mutex<PttState>> = OnceLock::new();
static CALLBACK: OnceLock<Box<dyn Fn(Action) + Send + Sync>> = OnceLock::new();
static START: OnceLock<Instant> = OnceLock::new();
static TRIG: AtomicU8 = AtomicU8::new(0);
static PAUSED: AtomicBool = AtomicBool::new(false);
// For a text trigger: true while the key is physically down + suppressed.
static PENDING: AtomicBool = AtomicBool::new(false);

/// Suspend/resume the hotkey without uninstalling the hook (tray Pause).
pub fn set_paused(paused: bool) {
    PAUSED.store(paused, Ordering::Relaxed);
}

fn now_ms() -> u64 {
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

fn current_trigger() -> Trigger {
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

fn physically_down(t: Trigger) -> bool {
    unsafe { trigger_vks(t).iter().any(|&vk| (GetAsyncKeyState(vk) as u16 & 0x8000) != 0) }
}

fn fire(action: Action) {
    if PAUSED.load(Ordering::Relaxed) {
        return; // hotkey suspended from the tray
    }
    if let Some(cb) = CALLBACK.get() {
        cb(action);
    }
}

/// Install the hook + timer. `on_action` receives Start/Stop. Idempotent-ish:
/// call once at startup.
pub fn spawn<F>(trigger: Trigger, threshold_ms: u64, on_action: F)
where
    F: Fn(Action) + Send + Sync + 'static,
{
    STATE.get_or_init(|| Mutex::new(PttState::new(threshold_ms)));
    START.get_or_init(Instant::now);
    let _ = CALLBACK.set(Box::new(on_action));
    TRIG.store(trigger as u8, Ordering::Relaxed);

    std::thread::spawn(timer_loop);

    std::thread::spawn(|| unsafe {
        match SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook_proc), None, 0) {
            Ok(hook) => {
                crate::mlog!("keyboard hook installed");
                let mut msg = MSG::default();
                while GetMessageW(&mut msg, None, 0, 0).as_bool() {
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
                let _: HHOOK = hook; // keep alive for the life of the loop
            }
            Err(e) => crate::mlog!("SetWindowsHookExW FAILED: {e} — hotkey won't work"),
        }
    });
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
            if (st.is_recording() || st.is_armed()) && !physically_down(trig) {
                // Missed key-up (e.g. elevated window had focus) — self-heal.
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

unsafe extern "system" fn hook_proc(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code != 0 {
        return CallNextHookEx(None, code, w, l);
    }
    let kb = &*(l.0 as *const KBDLLHOOKSTRUCT);
    // Ignore our own synthesized keystrokes (the tap re-injection).
    if (kb.flags.0 & LLKHF_INJECTED) != 0 {
        return CallNextHookEx(None, code, w, l);
    }
    let vk = kb.vkCode;
    let msg = w.0 as u32;
    let down = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
    let up = msg == WM_KEYUP || msg == WM_SYSKEYUP;
    let trig = current_trigger();
    let Some(state) = STATE.get() else {
        return CallNextHookEx(None, code, w, l);
    };
    let now = now_ms();

    if is_text_key(trig) {
        // Dual-function text key (e.g. '\'): suppress it. Hold past the threshold
        // = push-to-talk; a quick tap = synthesize the character so typing works.
        if is_trigger(vk, trig) {
            if down {
                if !PENDING.swap(true, Ordering::SeqCst) {
                    state.lock().unwrap().on_trigger_down(now);
                }
                return LRESULT(1); // swallow (incl. auto-repeat)
            }
            if up {
                let act = state.lock().unwrap().on_trigger_up(now);
                PENDING.store(false, Ordering::SeqCst);
                if matches!(act, Some(Action::StopRecording)) {
                    fire(Action::StopRecording);
                } else {
                    synth_key(VK_OEM_5 as u16); // it was a tap — type the '\'
                }
                return LRESULT(1);
            }
        }
        return CallNextHookEx(None, code, w, l); // other keys untouched
    }

    // Observed-only modifier trigger (Shift/Ctrl/Alt/Caps): never suppressed.
    let action = {
        let mut st = state.lock().unwrap();
        if down {
            if is_trigger(vk, trig) {
                st.on_trigger_down(now);
            } else {
                st.on_other_key(now);
            }
            None
        } else if up && is_trigger(vk, trig) {
            st.on_trigger_up(now)
        } else {
            None
        }
    };
    if let Some(a) = action {
        fire(a);
    }
    CallNextHookEx(None, code, w, l)
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
        assert!(is_trigger(VK_OEM_5, Trigger::Backslash));
        assert!(!is_trigger(VK_SHIFT, Trigger::Backslash));
    }

    #[test]
    fn is_trigger_matches_expected_vks() {
        assert!(is_trigger(VK_SHIFT, Trigger::ShiftEither));
        assert!(is_trigger(VK_LSHIFT, Trigger::ShiftEither));
        assert!(is_trigger(VK_RSHIFT, Trigger::ShiftEither));
        assert!(is_trigger(VK_LSHIFT, Trigger::ShiftLeft));
        assert!(!is_trigger(VK_RSHIFT, Trigger::ShiftLeft));
        assert!(is_trigger(VK_RCONTROL, Trigger::RightCtrl));
        assert!(!is_trigger(VK_SHIFT, Trigger::RightCtrl));
    }
}
