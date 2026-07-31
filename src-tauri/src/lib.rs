//! murmur's platform-independent core, as a library target.
//!
//! `main.rs` declares these same modules itself and builds the full Tauri shell.
//! Exposing them here as well means `cargo check`/`cargo test` can be run with
//! `--no-default-features` — no Tauri, and therefore no webkit2gtk system
//! dependency — which is the only way to type-check and test this code on a
//! Linux box without root. The `#[path]` attributes point at the shared source
//! files so there is exactly one copy of each module.
//!
//! Nothing Tauri-specific belongs in here: `commands.rs` and `tray.rs` stay
//! bin-only because they take `AppHandle`/`TrayIcon` arguments.

#[macro_use]
#[path = "logger.rs"]
pub mod logger;

#[path = "autostart.rs"]
pub mod autostart;

#[path = "config.rs"]
pub mod config;

#[path = "hotkey/mod.rs"]
pub mod hotkey;

#[path = "ptt.rs"]
pub mod ptt;

#[path = "sidecar.rs"]
pub mod sidecar;
