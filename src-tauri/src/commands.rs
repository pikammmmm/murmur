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

// --- history & stats --------------------------------------------------------
// The sidecar writes history.jsonl / stats.json next to config.json; these
// commands read them for the settings view (clear goes through the sidecar).

fn data_dir_of(state: &AppState) -> std::path::PathBuf {
    state.config_path.parent().map(|p| p.to_path_buf()).unwrap_or_default()
}

#[tauri::command]
pub fn get_history(state: State<AppState>) -> Vec<serde_json::Value> {
    let path = data_dir_of(&state).join("history.jsonl");
    let mut out: Vec<serde_json::Value> = Vec::new();
    if let Ok(text) = std::fs::read_to_string(&path) {
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                out.push(v);
            }
        }
    }
    out.reverse(); // newest first
    out.truncate(50);
    out
}

#[tauri::command]
pub fn get_stats(state: State<AppState>) -> serde_json::Value {
    let path = data_dir_of(&state).join("stats.json");
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({"dictations": 0, "words": 0}))
}

#[tauri::command]
pub fn clear_history(state: State<AppState>) {
    state.supervisor.send("clearhistory");
}

// --- run at login -----------------------------------------------------------
#[tauri::command]
pub fn get_autostart() -> bool {
    crate::autostart::is_enabled()
}

#[tauri::command]
pub fn set_autostart(enabled: bool) -> Result<(), String> {
    crate::autostart::set(enabled).map_err(|e| e.to_string())
}
