//! Run-at-login via the HKCU Run key (same approach as glassbar). The registry
//! IS the source of truth — there's no config mirror — so the settings toggle
//! reads/writes it directly.
use winreg::enums::HKEY_CURRENT_USER;
use winreg::RegKey;

const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const VALUE_NAME: &str = "murmur";

fn exe_path() -> Option<String> {
    std::env::current_exe().ok().map(|p| p.to_string_lossy().to_string())
}

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_then_clear_roundtrips() {
        // Preserve any pre-existing value so we never clobber a real entry.
        let prior: Option<String> = RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey(RUN_KEY)
            .ok()
            .and_then(|k| k.get_value(VALUE_NAME).ok());

        set(true).unwrap();
        assert!(is_enabled());
        set(false).unwrap();
        assert!(!is_enabled());

        if let Some(p) = prior {
            let (run, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey(RUN_KEY).unwrap();
            let _ = run.set_value(VALUE_NAME, &p);
        }
    }
}
