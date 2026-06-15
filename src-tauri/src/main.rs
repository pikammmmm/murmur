#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use tauri::menu::{CheckMenuItem, Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, RunEvent, WebviewUrl,
    WebviewWindowBuilder,
};

#[macro_use]
mod logger;
mod autostart;
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
    /// Cached from sidecar events so commands can read them synchronously.
    pub corrections: Arc<Mutex<serde_json::Value>>,
    pub last_raw: Arc<Mutex<String>>,
    pub preview: Arc<Mutex<String>>,
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

/// Where the Python sidecar lives. Normally prefer a frozen `murmur-sidecar.exe`
/// shipped next to the app, falling back (dev) to the project venv running
/// `main.py`. EXCEPTION: the "gpu" provider needs torch-directml, which is only
/// in the dev venv (never frozen) — so when gpu is selected and the venv exists,
/// prefer the venv even if a frozen CPU sidecar sits next to us (the bundler
/// re-copies that frozen exe on every build, so we can't rely on its absence).
fn resolve_launch(config_path: &PathBuf) -> Launch {
    let home = std::env::var("USERPROFILE").unwrap_or_default();
    let venv_py = PathBuf::from(&home).join("murmur").join("sidecar").join(".venv").join("Scripts").join("python.exe");
    let main_py = PathBuf::from(&home).join("murmur").join("sidecar").join("main.py");
    let venv_launch = || Launch {
        program: venv_py.clone(),
        args: vec![main_py.to_string_lossy().to_string()],
        config_path: config_path.clone(),
    };

    if venv_py.exists() && main_py.exists() && Config::load_from(config_path).stt.provider == "gpu" {
        mlog!("sidecar launch: dev venv (gpu provider) {}", venv_py.display());
        return venv_launch();
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            // Bundled installs place the frozen sidecar next to the exe; some
            // bundlers nest resources under `resources/`. Check both.
            for frozen in [dir.join("murmur-sidecar.exe"), dir.join("resources").join("murmur-sidecar.exe")] {
                if frozen.exists() {
                    mlog!("sidecar launch: frozen {}", frozen.display());
                    return Launch { program: frozen, args: vec![], config_path: config_path.clone() };
                }
            }
        }
    }
    mlog!("sidecar launch: dev venv {}", venv_py.display());
    venv_launch()
}

const OVERLAY_W: f64 = 300.0;
const OVERLAY_H: f64 = 120.0;

/// Create the hidden recording-indicator overlay: borderless, transparent,
/// always-on-top, click-through, bottom-center. Non-fatal on failure.
fn setup_overlay(app: &AppHandle) {
    let overlay = match WebviewWindowBuilder::new(app, "overlay", WebviewUrl::App("overlay.html".into()))
        .transparent(true)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .shadow(false)
        .inner_size(OVERLAY_W, OVERLAY_H)
        .visible(false)
        .build()
    {
        Ok(w) => w,
        Err(e) => {
            mlog!("overlay create failed: {e}");
            return;
        }
    };
    let _ = overlay.set_ignore_cursor_events(true); // clicks pass through
    if let Ok(Some(monitor)) = app.primary_monitor() {
        let scale = monitor.scale_factor();
        let size = monitor.size();
        let phys = LogicalSize::new(OVERLAY_W, OVERLAY_H).to_physical::<u32>(scale);
        let margin = (40.0 * scale) as i32;
        let x = ((size.width as i32) - (phys.width as i32)) / 2;
        let y = (size.height as i32) - (phys.height as i32) - margin;
        let _ = overlay.set_position(PhysicalPosition::new(x, y));
    }
    mlog!("overlay window created");
}

/// Drive the overlay through its states (recording -> transcribing -> hidden)
/// with no mic involved, so the indicator can be eyeballed from the tray. Runs
/// off-thread so the menu handler returns immediately.
fn preview_overlay(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        let Some(w) = app.get_webview_window("overlay") else { return };
        let _ = w.show();
        let cloud = app
            .try_state::<AppState>()
            .map(|st| {
                let c = st.config.lock().unwrap();
                c.stt.accuracy_mode || c.stt.provider == "groq" || c.stt.provider == "openai"
            })
            .unwrap_or(false);
        let _ = app.emit_to("overlay", "murmur:engine", if cloud { "cloud" } else { "local" });
        for (state, ms) in [("recording", 1800u64), ("transcribing", 1300)] {
            let _ = app.emit_to("overlay", "murmur:state", state);
            std::thread::sleep(std::time::Duration::from_millis(ms));
        }
        let _ = app.emit_to("overlay", "murmur:state", "idle");
        std::thread::sleep(std::time::Duration::from_millis(250)); // let it fade
        let _ = w.hide();
    });
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
            commands::get_corrections,
            commands::get_last_raw,
            commands::add_correction,
            commands::remove_correction,
            commands::teach_last,
            commands::get_history,
            commands::get_stats,
            commands::clear_history,
            commands::get_autostart,
            commands::set_autostart,
            commands::do_preview,
            commands::get_preview,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            // Shared caches updated from sidecar events (read by commands).
            let corrections = Arc::new(Mutex::new(serde_json::Value::Array(vec![])));
            let last_raw = Arc::new(Mutex::new(String::new()));
            let preview = Arc::new(Mutex::new(String::new()));

            // Sidecar supervisor — forward events to the tray + caches.
            let evt_handle = handle.clone();
            let corr_c = corrections.clone();
            let raw_c = last_raw.clone();
            let prev_c = preview.clone();
            let supervisor = Supervisor::start(launch.clone(), move |evt| match evt {
                SidecarEvent::State(s) => {
                    mlog!("sidecar state: {s}");
                    if let Some(t) = evt_handle.tray_by_id("murmur-tray") {
                        tray::set_state(&t, &s);
                    }
                    if let Some(w) = evt_handle.get_webview_window("overlay") {
                        let _ = evt_handle.emit_to("overlay", "murmur:state", s.clone());
                        // Read overlay-enabled + cloud-vs-local in one lock; tell the
                        // overlay which engine is active so it colors the waveform
                        // (orange = cloud STT, white = on-device).
                        let (overlay_on, cloud) = evt_handle
                            .try_state::<AppState>()
                            .map(|st| {
                                let c = st.config.lock().unwrap();
                                let cloud = c.stt.accuracy_mode
                                    || c.stt.provider == "groq"
                                    || c.stt.provider == "openai";
                                (c.overlay, cloud)
                            })
                            .unwrap_or((true, false));
                        let _ = evt_handle.emit_to(
                            "overlay",
                            "murmur:engine",
                            if cloud { "cloud" } else { "local" },
                        );
                        let want = matches!(s.as_str(), "recording" | "transcribing") && overlay_on;
                        let _ = if want { w.show() } else { w.hide() };
                    }
                }
                SidecarEvent::Error(m) => mlog!("sidecar error: {m}"),
                SidecarEvent::LastRaw(t) => *raw_c.lock().unwrap() = t,
                SidecarEvent::Corrections(v) => *corr_c.lock().unwrap() = v,
                SidecarEvent::Preview(t) => *prev_c.lock().unwrap() = t,
                SidecarEvent::Transcript(_) => {}
            });

            app.manage(AppState {
                config: Mutex::new(cfg.clone()),
                config_path: config_path.clone(),
                supervisor,
                corrections,
                last_raw,
                preview,
            });

            // Tray icon + menu.
            let settings_i = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let test_i = MenuItem::with_id(app, "test-indicator", "Preview indicator", true, None::<&str>)?;
            let pause_i = CheckMenuItem::with_id(app, "pause", "Pause dictation", true, false, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&settings_i, &test_i, &pause_i, &quit_i])?;
            let pause_for_evt = pause_i.clone();
            let mut tray_builder = TrayIconBuilder::with_id("murmur-tray")
                .tooltip(tray::tooltip_for("idle"))
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "settings" => open_settings(app),
                    "test-indicator" => preview_overlay(app),
                    "pause" => {
                        // muda has already toggled the check; checked == paused.
                        let paused = pause_for_evt.is_checked().unwrap_or(false);
                        hotkey::set_paused(paused);
                        if let Some(t) = app.tray_by_id("murmur-tray") {
                            tray::set_state(&t, if paused { "paused" } else { "idle" });
                        }
                        if paused {
                            if let Some(state) = app.try_state::<AppState>() {
                                state.supervisor.send("stop"); // abort any in-progress capture
                            }
                        }
                    }
                    "quit" => {
                        if let Some(state) = app.try_state::<AppState>() {
                            state.supervisor.shutdown();
                        }
                        app.exit(0);
                    }
                    _ => {}
                });
            // Icon is embedded at compile time, but don't panic the whole app if
            // it's somehow unavailable — a tray without a custom icon still works.
            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }
            let _tray = tray_builder.build(app)?;

            // Recording-indicator overlay (the floating blob).
            setup_overlay(&handle);

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
