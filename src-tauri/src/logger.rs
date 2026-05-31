//! Lightweight file logger -> `%APPDATA%\murmur\murmur\data\debug.log`.
//!
//! Background threads (the keyboard hook, the sidecar supervisor) have no
//! visible stderr under `windows_subsystem = "windows"`, so this on-disk log is
//! the durable diagnostic channel. Use the `mlog!` macro. Silent on I/O error —
//! logging must never crash the host.
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_BYTES: u64 = 1_000_000;

fn log_path() -> Option<PathBuf> {
    crate::config::data_dir().ok().map(|d| d.join("debug.log"))
}

fn stamp() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

pub fn init() {
    log("====================== murmur session start ======================");
}

pub fn log(msg: &str) {
    static MUTEX: OnceLock<Mutex<()>> = OnceLock::new();
    let _guard = MUTEX.get_or_init(|| Mutex::new(())).lock().unwrap();
    let Some(path) = log_path() else { return };
    let line = format!("[{}] {}\n", stamp(), msg);
    let truncate = std::fs::metadata(&path).map(|m| m.len() > MAX_BYTES).unwrap_or(false);
    let _ = if truncate {
        std::fs::write(&path, line.as_bytes())
    } else {
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .and_then(|mut f| f.write_all(line.as_bytes()))
    };
}

#[macro_export]
macro_rules! mlog {
    ($($arg:tt)*) => {
        $crate::logger::log(&format!($($arg)*))
    };
}
