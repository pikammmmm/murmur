//! Windows key observation: a `WH_KEYBOARD_LL` hook.
//!
//! The hook can both observe and *suppress* keystrokes, which is what makes the
//! dual-function text trigger possible: `\` is swallowed, and on a short tap we
//! synthesize a real `\` so ordinary typing still works. A 20ms timer thread
//! (in the parent module) drives the hold threshold and self-heals stuck state
//! by cross-checking the physical key with `GetAsyncKeyState` — a key-up that
//! lands while an elevated window is focused never reaches our hook (per
//! glassbar's keyhook notes), so we must not trust our own state blindly.
use std::sync::atomic::Ordering;

use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP,
    VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, SetWindowsHookExW, TranslateMessage, HHOOK,
    KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

use super::{
    current_trigger, fire, is_text_key, is_trigger, now_ms, trigger_vks, vk, Trigger, PENDING,
    STATE,
};
use crate::ptt::Action;

const LLKHF_INJECTED: u32 = 0x10;

/// Synthesize a real keypress (used to "type" the trigger char after a short
/// tap). Marked injected so our own hook ignores it.
unsafe fn synth_key(key: u16) {
    let mk = |key_up: bool| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(key),
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

pub(super) fn physically_down(t: Trigger) -> bool {
    unsafe { trigger_vks(t).iter().any(|&k| (GetAsyncKeyState(k) as u16 & 0x8000) != 0) }
}

pub(super) fn install() {
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

unsafe extern "system" fn hook_proc(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code != 0 {
        return CallNextHookEx(None, code, w, l);
    }
    let kb = &*(l.0 as *const KBDLLHOOKSTRUCT);
    // Ignore our own synthesized keystrokes (the tap re-injection).
    if (kb.flags.0 & LLKHF_INJECTED) != 0 {
        return CallNextHookEx(None, code, w, l);
    }
    let key = kb.vkCode;
    let msg = w.0 as u32;
    let down = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
    let up = msg == WM_KEYUP || msg == WM_SYSKEYUP;
    let trig = current_trigger();
    let Some(state) = STATE.get() else {
        return CallNextHookEx(None, code, w, l);
    };
    let now = now_ms();

    // Esc aborts an in-progress dictation: discard the audio, type nothing. We act
    // only while actually recording (cancel() returns an action), and swallow that
    // Esc so the focused window doesn't also receive it. A plain Esc with nothing
    // in progress falls through untouched (and behaves normally). When merely armed,
    // cancel() silently disarms + latches but returns None, so Esc still passes.
    if down && key == vk::ESCAPE {
        let act = state.lock().unwrap().cancel();
        if let Some(a) = act {
            fire(a);
            return LRESULT(1);
        }
    }

    if is_text_key(trig) {
        // Dual-function text key (e.g. '\'): suppress it. Hold past the threshold
        // = push-to-talk; a quick tap = synthesize the character so typing works.
        if is_trigger(key, trig) {
            if down {
                if !PENDING.swap(true, Ordering::SeqCst) {
                    state.lock().unwrap().on_trigger_down(now);
                }
                return LRESULT(1); // swallow (incl. auto-repeat)
            }
            if up {
                // Capture the cancel latch BEFORE on_trigger_up clears it: a
                // release that ends an Esc-aborted dictation must type nothing,
                // not fall through to the tap path and emit a stray '\'.
                let (act, was_cancelled) = {
                    let mut st = state.lock().unwrap();
                    let c = st.is_cancelled();
                    (st.on_trigger_up(now), c)
                };
                PENDING.store(false, Ordering::SeqCst);
                if matches!(act, Some(Action::StopRecording)) {
                    fire(Action::StopRecording);
                } else if !was_cancelled {
                    synth_key(vk::OEM_5 as u16); // it was a tap — type the '\'
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
            if is_trigger(key, trig) {
                st.on_trigger_down(now);
            } else {
                st.on_other_key(now);
            }
            None
        } else if up && is_trigger(key, trig) {
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
