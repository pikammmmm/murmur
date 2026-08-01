# murmur

Self-hosted, Wispr-Flow-class **voice dictation for Windows and Linux**. Hold a
key, speak, release — and accurate, correctly-punctuated,
context-appropriately-formatted text is typed into whatever field is focused
(email, chat, code editor, browser, anywhere). No subscription; running cost is a
few cents a month — or **$0** fully offline with no API keys at all.

Hold **`\`** and speak. A quick tap of `\` still types a backslash — it is a
dual-function key on both platforms.

## Install

### Windows 10/11, 64-bit

- **[Installer (.msi)](https://github.com/pikammmmm/murmur/releases/latest/download/murmur-Setup.msi)** — recommended.
- **[Portable (.zip)](https://github.com/pikammmmm/murmur/releases/latest/download/murmur-portable-x64.zip)** — unzip and run `murmur.exe`, no install.
- [All releases](https://github.com/pikammmmm/murmur/releases)

### Linux (Arch, KDE Plasma / Wayland)

Built from source — the Linux build needs system integration (a keyboard grab
and an input-injection daemon) that a portable binary cannot set up for you:

```bash
git clone https://github.com/pikammmmm/murmur.git
cd murmur
bash linux/install.sh
```

That installs the system packages, builds the shell, creates the Python sidecar
venv, configures the push-to-talk trigger and keystroke injection, and enables
systemd user services so murmur starts at login. It is idempotent — re-run it
after a `git pull` to rebuild. Undo everything with `bash linux/uninstall.sh`.

It will ask for `sudo` three times: to install packages, to write
`/etc/keyd/default.conf`, and to add you to the `keyd` and `input` groups.

> Developed and tested on Arch + KDE Plasma 6 (Wayland). Other distributions
> need the same four pieces by hand — see
> [docs/LINUX-PORT-NOTES.md](docs/LINUX-PORT-NOTES.md).

## How it works

A native **Rust/Tauri shell** drives a **Python sidecar** over a tiny
`stdin`-command / `stdout`-JSON-event protocol:

```
hold  \  (held, not tapped)        Rust/Tauri core
   │                                ├─ global hotkey (see below)
   ▼                                ├─ tray icon + settings/dictionary UI
 record ──────────────────────────▶├─ owns config.json, supervises sidecar
   │ release                        │      │ start / stop / reload / quit (stdin)
   ▼                                │      ▼
 transcribe + format + type ◀───────┴─ Python sidecar:
                                       recorder → context → STT → correct → format → inject
```

The hotkey is the one genuinely platform-specific part. Windows installs a
`WH_KEYBOARD_LL` hook, which can observe *and suppress* any key. Linux has no
unprivileged equivalent, so the shell instead accepts press/release over a Unix
socket and an external binder decides how the key is captured:

| | Trigger | Injection |
|---|---|---|
| **Windows** | `WH_KEYBOARD_LL` hook, in-process | `SendInput` / clipboard paste |
| **Linux** | `keyd` — grabs the keyboard below the compositor | `ydotool` — writes to `/dev/uinput` |

Why keyd rather than the desktop portal: the XDG GlobalShortcuts portal grabs
**by key, not by device**, so binding a bare printable key like `\` swallows that
character desktop-wide, and re-injecting it on a tap only feeds the grab again.
keyd sits a layer lower — it grabs the physical device and re-emits through its
own — so it can resolve tap-vs-hold before the compositor sees anything. A
no-root portal binder (`linux/murmur-ptt-binder.py`) ships as a fallback, on a
modifier-style trigger.

## Features

- **Hold-to-talk on `\`** — hold the backslash key to record, release to type the
  result. A quick tap still types a normal `\`, only a held press records. Prefer
  a non-typing key? Shift / Right Ctrl / Right Alt / Caps Lock are all selectable
  in Settings.
- **Recording indicator** — a Wispr-style floating "Flow Bar" pill at the bottom of
  the screen: an animated **waveform + live-dot** while it listens, a **thinking
  pulse** while it transcribes. (Tray → *Preview indicator* to see it.)
- **STT** — Groq `whisper-large-v3-turbo` (cloud, fast/cheap) by default, with an
  automatic **local `faster-whisper` fallback** when offline. OpenAI
  `gpt-4o-transcribe` is available as an accuracy toggle. GPU transcription runs
  on **DirectML** (Windows, any DX12 GPU) or **ROCm** (Linux, AMD) — see
  `sidecar/requirements-gpu.txt`.
- **Formatting & grammar** — an LLM cleans the transcript, removes
  fillers/false-starts, and resolves spoken self-corrections. Two modes:
  **Grammar** (default) also fixes grammatical errors — agreement, tense,
  did/didn't, don't/doesn't, double negatives, malformed phrases (e.g. "he don't
  know" → "he doesn't know") — while preserving your meaning; **Faithful** never
  changes your words. An offline rule pass handles common fixes with no API key,
  and on any error it falls through to the raw transcript so words are never lost.
- **Content-aware** — detects email-shaped speech (greeting, sign-off, "send an
  email…") and formats it as an email; detects a shopping/to-do list and turns it
  into bullet points — on top of the per-app profile (email vs chat vs code).
- **Pronunciation / accent learning** — learns the words you correct and auto-fixes
  accent-driven mishearings of known terms (Double-Metaphone phonetic + fuzzy
  matching), so it improves the more you use it. Manage it in tray → Settings, or
  fix the last dictation in the "Teach" box.
- **Custom dictionary** — your names/jargon bias the recognizer *and* are kept
  verbatim by the formatter.
- **Voice commands** — say "new paragraph", "new line", "new bullet", or "scratch
  that" to edit as you dictate.
- **Cancel mid-dictation** — press **Esc** while recording to discard it and type
  nothing (with a distinct "cancelled" cue).
- **Multilingual** — English, Slovenian, and everything else Whisper supports,
  with auto-detect. The formatter never translates.
- **Quality-of-life** — audio cues, type-vs-paste insertion, local dictation
  history + stats, run-at-login, and a tray Pause/Resume.

## Status

Built test-first. **All tests green: 256 Python + 35 Rust.** Windows ships as a
release build (optimized `murmur.exe` + a frozen `murmur-sidecar.exe`, no Python
required) with an MSI installer. Linux builds from source via `linux/install.sh`.
Runs fully offline out of the box; cloud STT + AI formatting light up the moment
you add an API key.

## Run from source (dev)

The Rust shell auto-detects the dev sidecar (`sidecar/.venv` + `main.py`) when no
frozen sidecar is bundled, so you can run straight from a debug build. It looks
for the venv next to the binary's own checkout first, so a clone anywhere works.

<details>
<summary>Linux</summary>

```bash
cd sidecar && python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../src-tauri && cargo run
```

For the hotkey and injection you still need keyd + ydotool configured —
`linux/install.sh` is the supported path; see its source for the individual steps.
</details>

<details>
<summary>Windows</summary>

```powershell
cd sidecar
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd ..\src-tauri
cargo run
```
</details>

A tray icon appears. With **no API keys** it transcribes locally (offline) and
types the raw transcript. Add keys in **tray → Settings** (or set the
`GROQ_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables) to
enable fast cloud STT + AI cleanup.

### Try dictating

1. Focus any text field.
2. **Hold `\`** — the recording pill appears at the bottom of the screen.
3. Speak, then **release** — the pill switches to *transcribing*, then the cleaned
   text is typed in.
4. A quick **tap** of `\` still just types a `\`.
5. Misspoke? Press **Esc** while recording to discard it — nothing is typed.

> If transcripts come back empty, check your system **default input device** is a
> real microphone.

## Build a release (Windows)

```powershell
.\build.ps1 -Release
```

This freezes the Python sidecar with PyInstaller (so end users need no Python),
builds the optimized Tauri app, and produces an MSI in
`src-tauri/target/release/bundle/msi`. (Omit `-Release` for a fast debug build.)

## Layout

- `sidecar/` — Python pipeline: recorder, context detection, STT providers,
  corrections/grammar/intent, formatter, injector, orchestrator. Tests in
  `sidecar/tests`.
- `src-tauri/` — Rust/Tauri shell: hotkey, sidecar supervisor, tray, overlay,
  config, commands, autostart.
- `linux/` — Arch installer/uninstaller, keyd config, push-to-talk binders,
  systemd user units.
- `ui/` — settings/dictionary web UI + the recording-indicator overlay.
- `docs/` — [architecture](docs/ARCHITECTURE.md), the
  [Linux port notes](docs/LINUX-PORT-NOTES.md), and the original design docs in
  `docs/design/`.

See `CHANGELOG.md` for the feature history.

## Roadmap

- **Audio-reactive waveform** — drive the indicator's bars from live mic level.
- **Command mode** — select text and speak an instruction to rewrite it.
- **Streaming partials** — show text as you speak rather than on release.
