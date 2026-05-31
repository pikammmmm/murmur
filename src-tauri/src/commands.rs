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
