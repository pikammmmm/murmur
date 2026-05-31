#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[macro_use]
mod logger;
mod config;
mod ptt;

fn main() {
    logger::init();
    let cfg = config::Config::load();
    mlog!(
        "murmur starting; stt={} formatter={} hotkey={}",
        cfg.stt.provider, cfg.formatter.provider, cfg.hotkey.key
    );
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running murmur");
}
