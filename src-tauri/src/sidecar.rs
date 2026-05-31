//! Python sidecar supervisor.
//!
//! Spawns the sidecar (hidden console), pumps its stdout JSON events to a
//! callback, and respawns with exponential backoff if it dies — the same
//! supervise-and-restart shape glassbar used for the old voice child. Commands
//! (`start`/`stop`/`reload`/`quit`) are written to the child's stdin.
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, PartialEq)]
pub enum SidecarEvent {
    State(String),
    Transcript(String),
    Error(String),
    /// Raw (pre-correction) STT of the last dictation — for the "teach" box.
    LastRaw(String),
    /// Current correction/pronunciation entries (a JSON array) — for the UI.
    Corrections(serde_json::Value),
    /// Result of a "Try it" preview request.
    Preview(String),
}

/// Parse one stdout line into an event. Returns None for blank/foreign lines.
pub fn parse_event(line: &str) -> Option<SidecarEvent> {
    let v: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    match v.get("type")?.as_str()? {
        "state" => Some(SidecarEvent::State(v.get("state")?.as_str()?.to_string())),
        "transcript" => Some(SidecarEvent::Transcript(v.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string())),
        "error" => Some(SidecarEvent::Error(v.get("message").and_then(|x| x.as_str()).unwrap_or("").to_string())),
        "last_raw" => Some(SidecarEvent::LastRaw(v.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string())),
        "corrections" => Some(SidecarEvent::Corrections(
            v.get("entries").cloned().unwrap_or_else(|| serde_json::Value::Array(vec![])),
        )),
        "preview" => Some(SidecarEvent::Preview(v.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string())),
        _ => None,
    }
}

/// Backoff schedule for respawns: 1s, 2s, 4s, 8s, then 10s thereafter.
pub fn backoff_delay(attempt: u32) -> std::time::Duration {
    let secs = match attempt {
        0 => 1,
        1 => 2,
        2 => 4,
        3 => 8,
        _ => 10,
    };
    std::time::Duration::from_secs(secs)
}

#[derive(Clone)]
pub struct Launch {
    pub program: std::path::PathBuf,
    pub args: Vec<String>,
    pub config_path: std::path::PathBuf,
}

pub struct Supervisor {
    stdin: Arc<Mutex<Option<std::process::ChildStdin>>>,
    stop: Arc<AtomicBool>,
}

fn spawn_child(launch: &Launch) -> std::io::Result<Child> {
    Command::new(&launch.program)
        .args(&launch.args)
        .env("MURMUR_CONFIG", &launch.config_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
}

impl Supervisor {
    /// Start supervising. `on_event` is called for every parsed stdout event.
    pub fn start<F>(launch: Launch, on_event: F) -> Self
    where
        F: Fn(SidecarEvent) + Send + 'static,
    {
        let stdin: Arc<Mutex<Option<std::process::ChildStdin>>> = Arc::new(Mutex::new(None));
        let stop = Arc::new(AtomicBool::new(false));
        let stdin_c = stdin.clone();
        let stop_c = stop.clone();
        std::thread::spawn(move || {
            let mut attempt = 0u32;
            while !stop_c.load(Ordering::Relaxed) {
                match spawn_child(&launch) {
                    Ok(mut child) => {
                        attempt = 0;
                        *stdin_c.lock().unwrap() = child.stdin.take();
                        if let Some(out) = child.stdout.take() {
                            for line in BufReader::new(out).lines() {
                                match line {
                                    Ok(line) => {
                                        if let Some(evt) = parse_event(&line) {
                                            on_event(evt);
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                        }
                        let _ = child.wait();
                        *stdin_c.lock().unwrap() = None;
                        if stop_c.load(Ordering::Relaxed) {
                            break;
                        }
                        crate::mlog!("sidecar exited; respawning");
                    }
                    Err(e) => {
                        crate::mlog!("sidecar spawn failed: {e}");
                    }
                }
                if stop_c.load(Ordering::Relaxed) {
                    break;
                }
                std::thread::sleep(backoff_delay(attempt));
                attempt = attempt.saturating_add(1);
            }
        });
        Supervisor { stdin, stop }
    }

    /// Send a one-line command to the sidecar (no-op if it's down right now).
    pub fn send(&self, cmd: &str) {
        if let Some(stdin) = self.stdin.lock().unwrap().as_mut() {
            let _ = writeln!(stdin, "{cmd}");
            let _ = stdin.flush();
        }
    }

    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::Relaxed);
        self.send("quit");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_state_transcript_error() {
        assert_eq!(parse_event(r#"{"type":"state","state":"idle"}"#), Some(SidecarEvent::State("idle".into())));
        assert_eq!(parse_event(r#"{"type":"transcript","text":"hi"}"#), Some(SidecarEvent::Transcript("hi".into())));
        assert_eq!(parse_event(r#"{"type":"error","message":"boom"}"#), Some(SidecarEvent::Error("boom".into())));
    }

    #[test]
    fn ignores_blank_and_foreign_lines() {
        assert_eq!(parse_event(""), None);
        assert_eq!(parse_event("not json"), None);
        assert_eq!(parse_event(r#"{"type":"other"}"#), None);
        assert_eq!(parse_event(r#"{"no_type":true}"#), None);
    }

    #[test]
    fn parses_last_raw_and_corrections() {
        assert_eq!(
            parse_event(r#"{"type":"last_raw","text":"open glass bar"}"#),
            Some(SidecarEvent::LastRaw("open glass bar".into()))
        );
        match parse_event(r#"{"type":"corrections","entries":[{"wrong":"a","right":"b"}]}"#) {
            Some(SidecarEvent::Corrections(v)) => assert!(v.is_array() && v.as_array().unwrap().len() == 1),
            other => panic!("expected Corrections, got {other:?}"),
        }
    }

    #[test]
    fn backoff_caps_at_ten_seconds() {
        assert_eq!(backoff_delay(0).as_secs(), 1);
        assert_eq!(backoff_delay(1).as_secs(), 2);
        assert_eq!(backoff_delay(2).as_secs(), 4);
        assert_eq!(backoff_delay(3).as_secs(), 8);
        assert_eq!(backoff_delay(4).as_secs(), 10);
        assert_eq!(backoff_delay(99).as_secs(), 10);
    }
}
