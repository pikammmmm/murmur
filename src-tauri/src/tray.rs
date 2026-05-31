//! Tray icon state reflection. The tray is built in `main.rs` (it needs the app
//! handle + menu wiring); this module owns the tooltip mapping so it stays
//! testable and the state labels live in one place.
use tauri::tray::TrayIcon;

pub fn tooltip_for(state: &str) -> String {
    let label = match state {
        "recording" => "recording",
        "transcribing" => "transcribing",
        "error" => "error",
        "loading" => "loading…",
        _ => "idle",
    };
    format!("murmur — {label}")
}

pub fn set_state(tray: &TrayIcon, state: &str) {
    let _ = tray.set_tooltip(Some(tooltip_for(state)));
}

#[cfg(test)]
mod tests {
    use super::tooltip_for;

    #[test]
    fn known_states_map() {
        assert!(tooltip_for("recording").contains("recording"));
        assert!(tooltip_for("transcribing").contains("transcribing"));
        assert!(tooltip_for("error").contains("error"));
    }

    #[test]
    fn unknown_state_falls_back_to_idle() {
        assert!(tooltip_for("whatever").contains("idle"));
    }
}
