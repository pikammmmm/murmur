//! Config — the single source of truth, owned by the Rust shell and *read* by
//! the Python sidecar. The on-disk JSON schema must match the sidecar's
//! `config.py` DEFAULTS (spec §7). Every field has a serde default so a partial
//! or older file always loads.
use std::path::{Path, PathBuf};

use directories::ProjectDirs;
use serde::{Deserialize, Serialize};

pub fn data_dir() -> std::io::Result<PathBuf> {
    let dirs = ProjectDirs::from("com", "murmur", "murmur")
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "no AppData dir"))?;
    let dir = dirs.data_dir().to_path_buf();
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

pub fn config_path() -> std::io::Result<PathBuf> {
    Ok(data_dir()?.join("config.json"))
}

fn d_key() -> String { "backslash".into() }
fn d_side() -> String { "either".into() }
fn d_threshold() -> u64 { 350 }
fn d_stt_provider() -> String { "groq".into() }
fn d_lang() -> String { "en".into() }
fn d_groq_model() -> String { "whisper-large-v3-turbo".into() }
fn d_openai_model() -> String { "gpt-4o-transcribe".into() }
fn d_local_model() -> String { "base".into() }
fn d_beam() -> u32 { 5 }
fn d_true() -> bool { true }
fn d_fmt_provider() -> String { "anthropic".into() }
fn d_fmt_model() -> String { "claude-haiku-4-5-20251001".into() }
fn d_fmt_mode() -> String { "grammar".into() }
fn d_max_tokens() -> u32 { 1024 }
fn d_max_rec() -> u32 { 60 }
fn d_inject_mode() -> String { "type".into() }
fn d_profiles() -> serde_json::Value { serde_json::json!({}) }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Hotkey {
    #[serde(default = "d_key")]
    pub key: String,
    #[serde(default = "d_side")]
    pub side: String,
    #[serde(default = "d_threshold")]
    pub hold_threshold_ms: u64,
}
impl Default for Hotkey {
    fn default() -> Self {
        Self { key: d_key(), side: d_side(), hold_threshold_ms: d_threshold() }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Stt {
    #[serde(default = "d_stt_provider")]
    pub provider: String,
    #[serde(default)]
    pub accuracy_mode: bool,
    #[serde(default = "d_lang")]
    pub language: String,
    #[serde(default = "d_groq_model")]
    pub groq_model: String,
    #[serde(default = "d_openai_model")]
    pub openai_model: String,
    #[serde(default = "d_local_model")]
    pub local_model: String,
    #[serde(default = "d_beam")]
    pub beam_size: u32,
    #[serde(default = "d_true")]
    pub vad_filter: bool,
}
impl Default for Stt {
    fn default() -> Self {
        Self {
            provider: d_stt_provider(), accuracy_mode: false, language: d_lang(),
            groq_model: d_groq_model(), openai_model: d_openai_model(),
            local_model: d_local_model(), beam_size: d_beam(), vad_filter: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Formatter {
    #[serde(default = "d_fmt_provider")]
    pub provider: String,
    #[serde(default = "d_fmt_model")]
    pub model: String,
    #[serde(default = "d_fmt_mode")]
    pub mode: String,
    #[serde(default = "d_max_tokens")]
    pub max_output_tokens: u32,
}
impl Default for Formatter {
    fn default() -> Self {
        Self { provider: d_fmt_provider(), model: d_fmt_model(), mode: d_fmt_mode(), max_output_tokens: d_max_tokens() }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Keys {
    #[serde(default)]
    pub groq: Option<String>,
    #[serde(default)]
    pub openai: Option<String>,
    #[serde(default)]
    pub anthropic: Option<String>,
    #[serde(default)]
    pub cerebras: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Config {
    #[serde(default)]
    pub hotkey: Hotkey,
    #[serde(default)]
    pub stt: Stt,
    #[serde(default)]
    pub formatter: Formatter,
    #[serde(default)]
    pub keys: Keys,
    #[serde(default = "d_max_rec")]
    pub max_recording_seconds: u32,
    #[serde(default = "d_true")]
    pub voice_commands: bool,
    #[serde(default = "d_true")]
    pub audio_cues: bool,
    #[serde(default = "d_inject_mode")]
    pub inject_mode: String,
    #[serde(default = "d_true")]
    pub save_history: bool,
    #[serde(default = "d_true")]
    pub overlay: bool,
    #[serde(default)]
    pub dictionary: Vec<String>,
    #[serde(default = "d_profiles")]
    pub profiles: serde_json::Value,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            hotkey: Hotkey::default(), stt: Stt::default(), formatter: Formatter::default(),
            keys: Keys::default(), max_recording_seconds: d_max_rec(),
            voice_commands: true, audio_cues: true, inject_mode: d_inject_mode(),
            save_history: true, overlay: true, dictionary: Vec::new(), profiles: d_profiles(),
        }
    }
}

impl Config {
    pub fn load() -> Self {
        match config_path().and_then(std::fs::read_to_string) {
            Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
            Err(_) => Self::default(),
        }
    }

    pub fn load_from(path: &Path) -> Self {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self) -> std::io::Result<()> {
        let path = config_path()?;
        self.save_to(&path)
    }

    pub fn save_to(&self, path: &Path) -> std::io::Result<()> {
        let json = serde_json::to_string_pretty(self).unwrap_or_else(|_| "{}".into());
        std::fs::write(path, json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_sidecar() {
        let c = Config::default();
        assert_eq!(c.stt.provider, "groq");
        assert_eq!(c.formatter.provider, "anthropic");
        assert_eq!(c.formatter.model, "claude-haiku-4-5-20251001");
        assert_eq!(c.hotkey.key, "backslash");
        assert_eq!(c.hotkey.hold_threshold_ms, 350);
    }

    #[test]
    fn json_roundtrip() {
        let c = Config::default();
        let s = serde_json::to_string(&c).unwrap();
        let back: Config = serde_json::from_str(&s).unwrap();
        assert_eq!(c, back);
    }

    #[test]
    fn partial_json_backfills_defaults() {
        let c: Config = serde_json::from_str(r#"{"stt":{"provider":"local"}}"#).unwrap();
        assert_eq!(c.stt.provider, "local");
        assert_eq!(c.stt.language, "en");
        assert_eq!(c.formatter.provider, "anthropic");
        assert_eq!(c.hotkey.hold_threshold_ms, 350);
    }

    #[test]
    fn save_then_load_roundtrips_via_disk() {
        let dir = std::env::temp_dir().join(format!("murmur-cfg-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("config.json");
        let mut c = Config::default();
        c.dictionary.push("Rojo".into());
        c.keys.groq = Some("secret".into());
        c.save_to(&path).unwrap();
        let back = Config::load_from(&path);
        assert_eq!(back.dictionary, vec!["Rojo".to_string()]);
        assert_eq!(back.keys.groq.as_deref(), Some("secret"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
