//! Global hold-to-talk hotkey via a `WH_KEYBOARD_LL` hook.
//!
//! The low-level hook callback must be a bare `extern "system" fn`, so shared
//! state (the PttState, the action callback, the configured trigger) lives in
//! statics. A 20ms timer thread drives `on_tick` (the hold-alone threshold) and
//! self-heals stuck state by cross-checking the physical key with
//! `GetAsyncKeyState` — a key-up that lands while an elevated window is focused
//! never reaches our hook (per glassbar's keyhook notes), so we must not trust
//! our own state blindly. The trigger key is never suppressed.
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, SetWindowsHookExW, TranslateMessage, HHOOK,
    KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

use crate::ptt::{Action, PttState};

const VK_SHIFT: u32 = 0x10;
const VK_LSHIFT: u32 = 0xA0;
const VK_RSHIFT: u32 = 0xA1;
const VK_RCONTROL: u32 = 0xA3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Trigger {
    ShiftEither = 0,
    ShiftLeft = 1,
    ShiftRight = 2,
    RightCtrl = 3,
}

pub fn trigger_from_config(key: &str, side: &str) -> Trigger {
    match key.to_lowercase().as_str() {
        "shift" => match side.to_lowercase().as_str() {
            "left" => Trigger::ShiftLeft,
            "right" => Trigger::ShiftRight,
            _ => Trigger::ShiftEither,
        },
        "rctrl" | "right-ctrl" | "rightctrl" => Trigger::RightCtrl,
        _ => Trigger::ShiftEither,
    }
}

fn is_trigger(vk: u32, t: Trigger) -> bool {
    match t {
        Trigger::ShiftEither => matches!(vk, VK_SHIFT | VK_LSHIFT | VK_RSHIFT),
        Trigger::ShiftLeft => vk == VK_LSHIFT,
        Trigger::ShiftRight => vk == VK_RSHIFT,
        Trigger::RightCtrl => vk == VK_RCONTROL,
    }
}

fn trigger_vks(t: Trigger) -> &'static [i32] {
    match t {
        Trigger::ShiftEither => &[VK_SHIFT as i32],
        Trigger::ShiftLeft => &[VK_LSHIFT as i32],
        Trigger::ShiftRight => &[VK_RSHIFT as i32],
        Trigger::RightCtrl => &[VK_RCONTROL as i32],
    }
}

static STATE: OnceLock<Mutex<PttState>> = OnceLock::new();
static CALLBACK: OnceLock<Box<dyn Fn(Action) + Send + Sync>> = OnceLock::new();
static START: OnceLock<Instant> = OnceLock::new();
static TRIG: AtomicU8 = AtomicU8::new(0);

fn now_ms() -> u64 {
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

fn current_trigger() -> Trigger {
    match TRIG.load(Ordering::Relaxed) {
        1 => Trigger::ShiftLeft,
        2 => Trigger::ShiftRight,
        3 => Trigger::RightCtrl,
        _ => Trigger::ShiftEither,
    }
}

fn physically_down(t: Trigger) -> bool {
    unsafe { trigger_vks(t).iter().any(|&vk| (GetAsyncKeyState(vk) as u16 & 0x8000) != 0) }
}

fn fire(action: Action) {
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
                st.on_trigger_up(now)
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
    let vk = kb.vkCode;
    let msg = w.0 as u32;
    let trig = current_trigger();
    if let Some(state) = STATE.get() {
        let now = now_ms();
        let action = {
            let mut st = state.lock().unwrap();
            if msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN {
                if is_trigger(vk, trig) {
                    st.on_trigger_down(now);
                } else {
                    st.on_other_key(now);
                }
                None
            } else if (msg == WM_KEYUP || msg == WM_SYSKEYUP) && is_trigger(vk, trig) {
                st.on_trigger_up(now)
            } else {
                None
            }
        };
        if let Some(a) = action {
            fire(a);
        }
    }
    // Never suppress — let the keystroke through so typing/capitalization works.
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
        assert_eq!(trigger_from_config("whatever", ""), Trigger::ShiftEither);
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
