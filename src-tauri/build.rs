fn main() {
    // `tauri_build::build()` inspects the Tauri crate in the dependency graph and
    // panics with "missing `cargo:dev` instruction" when it isn't there. With
    // `--no-default-features` the `shell` feature is off and `tauri` is dropped,
    // which is exactly how the platform-independent core gets type-checked and
    // unit-tested on Linux without a webkit2gtk development package. So only run
    // the Tauri build step when we are actually building the shell.
    if std::env::var_os("CARGO_FEATURE_SHELL").is_some() {
        tauri_build::build();
    }
}
