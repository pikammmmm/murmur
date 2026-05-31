//! Tauri commands the settings UI calls. All mutate the in-memory config, write
//! it to disk (so the sidecar can read it), and signal the sidecar to reload.
//! Hotkey changes are applied live via `hotkey::reconfigure`.
use tauri::State;

use crate::config::Config;
use crate::hotkey;
use crate::AppState;

#[tauri::command]
pub fn get_config(state: State<AppState>) -> Config {
    state.config.lock().unwrap().clone()
}

#[tauri::command]
pub fn set_config(payload: Config, state: State<AppState>) -> Result<(), String> {
    {
        let mut cfg = state.config.lock().unwrap();
        *cfg = payload;
        cfg.save_to(&state.config_path).map_err(|e| e.to_string())?;
        let trig = hotkey::trigger_from_config(&cfg.hotkey.key, &cfg.hotkey.side);
        hotkey::reconfigure(trig, cfg.hotkey.hold_threshold_ms);
    }
    state.supervisor.send("reload");
    Ok(())
}

#[tauri::command]
pub fn add_dict_term(term: String, state: State<AppState>) -> Result<Config, String> {
    let snapshot = {
        let mut cfg = state.config.lock().unwrap();
        let term = term.trim().to_string();
        if !term.is_empty() && !cfg.dictionary.contains(&term) {
            cfg.dictionary.push(term);
        }
        cfg.save_to(&state.config_path).map_err(|e| e.to_string())?;
        cfg.clone()
    };
    state.supervisor.send("reload");
    Ok(snapshot)
}

#[tauri::command]
pub fn remove_dict_term(term: String, state: State<AppState>) -> Result<Config, String> {
    let snapshot = {
        let mut cfg = state.config.lock().unwrap();
        cfg.dictionary.retain(|t| t != &term);
        cfg.save_to(&state.config_path).map_err(|e| e.to_string())?;
        cfg.clone()
    };
    state.supervisor.send("reload");
    Ok(snapshot)
}

// --- pronunciation / correction learning -----------------------------------
// The sidecar owns corrections.json (single writer); these commands send it
// stdin commands, and the cached entries/last-raw come back via events.

fn sanitize(s: &str) -> String {
    s.replace(['\n', '\r', '\t'], " ").trim().to_string()
}

#[tauri::command]
pub fn get_corrections(state: State<AppState>) -> serde_json::Value {
    state.corrections.lock().unwrap().clone()
}

#[tauri::command]
pub fn get_last_raw(state: State<AppState>) -> String {
    state.last_raw.lock().unwrap().clone()
}

#[tauri::command]
pub fn add_correction(wrong: String, right: String, state: State<AppState>) {
    let (w, r) = (sanitize(&wrong), sanitize(&right));
    if !w.is_empty() && !r.is_empty() {
        state.supervisor.send(&format!("correctadd {w}\t{r}"));
    }
}

#[tauri::command]
pub fn remove_correction(wrong: String, state: State<AppState>) {
    let w = sanitize(&wrong);
    if !w.is_empty() {
        state.supervisor.send(&format!("correctdel {w}"));
    }
}

#[tauri::command]
pub fn teach_last(text: String, state: State<AppState>) {
    let t = text.replace(['\n', '\r'], " ");
    if !t.trim().is_empty() {
        state.supervisor.send(&format!("learn {t}"));
    }
}
