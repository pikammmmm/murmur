//! Run-at-login.
//!
//! Windows: the HKCU Run key (same approach as glassbar). Linux: an XDG
//! autostart desktop entry under `$XDG_CONFIG_HOME/autostart` (KDE Plasma,
//! GNOME and friends all honour it).
//!
//! On BOTH platforms the OS is the single source of truth — there is no config
//! mirror — so the settings toggle reads/writes it directly and `is_enabled`
//! answers by inspecting the real thing.

const VALUE_NAME: &str = "murmur";

fn exe_path() -> Option<String> {
    std::env::current_exe().ok().map(|p| p.to_string_lossy().to_string())
}

// ---------------------------------------------------------------- Windows ---
#[cfg(windows)]
mod imp {
    use super::{exe_path, VALUE_NAME};
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;

    pub(super) const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

    pub fn set(enabled: bool) -> std::io::Result<()> {
        let (run, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey(RUN_KEY)?;
        if enabled {
            if let Some(path) = exe_path() {
                run.set_value(VALUE_NAME, &format!("\"{path}\""))?;
            }
        } else {
            let _ = run.delete_value(VALUE_NAME);
        }
        Ok(())
    }

    pub fn is_enabled() -> bool {
        RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey(RUN_KEY)
            .and_then(|k| k.get_value::<String, _>(VALUE_NAME))
            .is_ok()
    }
}

// ------------------------------------------------------------------ Linux ---
#[cfg(not(windows))]
mod imp {
    use super::{exe_path, VALUE_NAME};
    use std::path::PathBuf;

    /// `$XDG_CONFIG_HOME/autostart/murmur.desktop`, falling back to
    /// `~/.config/autostart` when XDG_CONFIG_HOME is unset (the usual case).
    fn desktop_file() -> Option<PathBuf> {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| !p.as_os_str().is_empty())
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
        Some(base.join("autostart").join(format!("{VALUE_NAME}.desktop")))
    }

    pub fn set(enabled: bool) -> std::io::Result<()> {
        let Some(path) = desktop_file() else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "neither XDG_CONFIG_HOME nor HOME is set",
            ));
        };
        if !enabled {
            // Absent file == disabled; a missing file is success, not an error.
            return match std::fs::remove_file(&path) {
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                other => other,
            };
        }
        let exec = exe_path().ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::NotFound, "cannot resolve current exe")
        })?;
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        // Exec is quoted per the Desktop Entry spec so a path with spaces works.
        let entry = format!(
            "[Desktop Entry]\n\
             Type=Application\n\
             Name=murmur\n\
             Comment=Hold-to-talk voice dictation\n\
             Exec=\"{exec}\"\n\
             Terminal=false\n\
             X-GNOME-Autostart-enabled=true\n"
        );
        std::fs::write(&path, entry)
    }

    pub fn is_enabled() -> bool {
        desktop_file().map(|p| p.is_file()).unwrap_or(false)
    }
}

pub use imp::{is_enabled, set};

/// Test-only accessor for the autostart entry path (Linux), so the round-trip
/// test can snapshot/restore a pre-existing entry.
#[cfg(all(test, not(windows)))]
fn imp_desktop_file_for_test() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(std::path::PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
        .or_else(|| std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".config")))?;
    Some(base.join("autostart").join(format!("{VALUE_NAME}.desktop")))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Round-trips through the REAL per-user autostart location, so it asserts
    /// the actual OS mechanism rather than a mock. Any pre-existing entry is
    /// captured first and restored at the end, so a real install is never lost.
    #[test]
    fn set_then_clear_roundtrips() {
        let prior = snapshot();

        set(true).unwrap();
        assert!(is_enabled(), "enabling autostart should be observable");
        set(false).unwrap();
        assert!(!is_enabled(), "disabling autostart should be observable");

        restore(prior);
    }

    /// Disabling when already disabled must succeed rather than error on the
    /// missing key/file.
    #[test]
    fn clearing_when_absent_is_ok() {
        let prior = snapshot();
        set(false).unwrap();
        set(false).unwrap();
        assert!(!is_enabled());
        restore(prior);
    }

    #[cfg(windows)]
    fn snapshot() -> Option<String> {
        use winreg::enums::HKEY_CURRENT_USER;
        use winreg::RegKey;
        RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey(imp::RUN_KEY)
            .ok()
            .and_then(|k| k.get_value(VALUE_NAME).ok())
    }

    #[cfg(windows)]
    fn restore(prior: Option<String>) {
        use winreg::enums::HKEY_CURRENT_USER;
        use winreg::RegKey;
        if let Some(p) = prior {
            let (run, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey(imp::RUN_KEY).unwrap();
            let _ = run.set_value(VALUE_NAME, &p);
        }
    }

    #[cfg(not(windows))]
    fn snapshot() -> Option<String> {
        super::imp_desktop_file_for_test().and_then(|p| std::fs::read_to_string(p).ok())
    }

    #[cfg(not(windows))]
    fn restore(prior: Option<String>) {
        if let (Some(path), Some(body)) = (super::imp_desktop_file_for_test(), prior) {
            if let Some(dir) = path.parent() {
                let _ = std::fs::create_dir_all(dir);
            }
            let _ = std::fs::write(path, body);
        }
    }
}
