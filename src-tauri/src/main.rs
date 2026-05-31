#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

#[macro_use]
mod logger;
mod commands;
mod config;
mod hotkey;
mod ptt;
mod sidecar;
mod tray;

use config::Config;
use sidecar::{Launch, SidecarEvent, Supervisor};

/// Shared state managed by Tauri and reached from commands + callbacks.
pub struct AppState {
    pub config: Mutex<Config>,
    pub config_path: PathBuf,
    pub supervisor: Supervisor,
}

/// One running instance only — a named kernel mutex survives across processes
/// (autostart + manual relaunch + cargo runs would otherwise pile up).
fn singleton_ok() -> bool {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows::Win32::System::Threading::CreateMutexW;
    let name: Vec<u16> = "Local\\murmur-singleton-9f2a"
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let handle = CreateMutexW(None, false, PCWSTR(name.as_ptr()));
        if handle.is_err() {
            return true; // can't tell — allow start
        }
        let already = GetLastError() == ERROR_ALREADY_EXISTS;
        std::mem::forget(handle); // OS releases on exit, freeing the lock
        !already
    }
}

/// Where the Python sidecar lives. Prefer a frozen `murmur-sidecar.exe` shipped
/// next to the app; fall back (dev) to the project venv running `main.py`.
fn resolve_launch(config_path: &PathBuf) -> Launch {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let frozen = dir.join("murmur-sidecar.exe");
            if frozen.exists() {
                return Launch { program: frozen, args: vec![], config_path: config_path.clone() };
            }
        }
    }
    let home = std::env::var("USERPROFILE").unwrap_or_default();
    let py = PathBuf::from(&home).join("murmur").join("sidecar").join(".venv").join("Scripts").join("python.exe");
    let main_py = PathBuf::from(&home).join("murmur").join("sidecar").join("main.py");
    Launch {
        program: py,
        args: vec![main_py.to_string_lossy().to_string()],
        config_path: config_path.clone(),
    }
}

fn open_settings(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("settings") {
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }
    let _ = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App("index.html".into()))
        .title("murmur settings")
        .inner_size(540.0, 680.0)
        .resizable(true)
        .build();
}

fn main() {
    if !singleton_ok() {
        return;
    }
    logger::init();

    let config_path = config::config_path().unwrap_or_else(|_| PathBuf::from("config.json"));
    let cfg = Config::load();
    // Materialize the file on first run so the sidecar has something to read.
    if !config_path.exists() {
        let _ = cfg.save_to(&config_path);
    }
    mlog!(
        "starting; stt={} formatter={} hotkey={}/{}",
        cfg.stt.provider, cfg.formatter.provider, cfg.hotkey.key, cfg.hotkey.side
    );

    let launch = resolve_launch(&config_path);

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::get_config,
            commands::set_config,
            commands::add_dict_term,
            commands::remove_dict_term,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            // Sidecar supervisor — forward state events to the tray tooltip.
            let evt_handle = handle.clone();
            let supervisor = Supervisor::start(launch.clone(), move |evt| match evt {
                SidecarEvent::State(s) => {
                    mlog!("sidecar state: {s}");
                    if let Some(t) = evt_handle.tray_by_id("murmur-tray") {
                        tray::set_state(&t, &s);
                    }
                }
                SidecarEvent::Error(m) => mlog!("sidecar error: {m}"),
                SidecarEvent::Transcript(_) => {}
            });

            app.manage(AppState {
                config: Mutex::new(cfg.clone()),
                config_path: config_path.clone(),
                supervisor,
            });

            // Tray icon + menu.
            let settings_i = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&settings_i, &quit_i])?;
            let _tray = TrayIconBuilder::with_id("murmur-tray")
                .tooltip(tray::tooltip_for("idle"))
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "settings" => open_settings(app),
                    "quit" => {
                        if let Some(state) = app.try_state::<AppState>() {
                            state.supervisor.shutdown();
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Global hold-to-talk hotkey -> drive the sidecar.
            let trig = hotkey::trigger_from_config(&cfg.hotkey.key, &cfg.hotkey.side);
            let hk_handle = handle.clone();
            hotkey::spawn(trig, cfg.hotkey.hold_threshold_ms, move |action| {
                if let Some(state) = hk_handle.try_state::<AppState>() {
                    match action {
                        ptt::Action::StartRecording => state.supervisor.send("start"),
                        ptt::Action::StopRecording => state.supervisor.send("stop"),
                    }
                }
            });

            mlog!("setup complete");
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error building murmur")
        .run(|_app, event| {
            // No main window — closing the settings window must NOT quit; only
            // the tray Quit (app.exit) ends the process.
            if let RunEvent::ExitRequested { api, .. } = event {
                api.prevent_exit();
            }
        });
}
