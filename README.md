# murmur

Self-hosted, Wispr-Flow-class **voice dictation for Windows**. Hold a key, speak,
release — and accurate, correctly-punctuated, context-appropriately-formatted
text is typed into whatever field is focused (email, chat, code editor, browser,
anywhere). No subscription; running cost is a few cents a month — or **$0** fully
offline with no API keys at all.

## Download

Windows 10/11, 64-bit:

- **[Installer (.msi)](https://github.com/pikammmmm/murmur/releases/latest/download/murmur-Setup.msi)** — recommended.
- **[Portable (.zip)](https://github.com/pikammmmm/murmur/releases/latest/download/murmur-portable-x64.zip)** — unzip and run `murmur.exe`, no install.
- [All releases](https://github.com/pikammmmm/murmur/releases)

A tray icon appears; hold **`\`** and speak. Works **offline** out of the box
(local CPU transcription); add a Groq or Anthropic key in tray → Settings for
faster cloud STT + AI formatting. GPU transcription (large-v3-turbo on your own
GPU) is a from-source option — see [Run from source](#run-from-source-dev) and
`sidecar/requirements-gpu.txt`.

## How it works

A native **Rust/Tauri shell** drives a **Python sidecar** over a tiny
`stdin`-command / `stdout`-JSON-event protocol:

```
hold  \  (held, not tapped)        Rust/Tauri core
   │                                ├─ Win32 WH_KEYBOARD_LL global hotkey
   ▼                                ├─ tray icon + settings/dictionary UI
 record ──────────────────────────▶├─ owns config.json, supervises sidecar
   │ release                        │      │ start / stop / reload / quit (stdin)
   ▼                                │      ▼
 transcribe + format + type ◀───────┴─ Python sidecar:
                                       recorder → context → STT → correct → format → inject
```

## Features

- **Hold-to-talk on `\`** — hold the backslash key to record, release to type the
  result. It's a *dual-function* key: a quick tap still types a normal `\`, only a
  held press records. Prefer a non-typing key? Shift / Right Ctrl / Right Alt /
  Caps Lock are all selectable in Settings.
- **Recording indicator** — a Wispr-style floating "Flow Bar" pill at the bottom of
  the screen: an animated **waveform + live-dot** while it listens, a **thinking
  pulse** while it transcribes. (Tray → *Preview indicator* to see it.)
- **STT** — Groq `whisper-large-v3-turbo` (cloud, fast/cheap) by default, with an
  automatic **local `faster-whisper` fallback** when offline. OpenAI
  `gpt-4o-transcribe` is available as an accuracy toggle.
- **Formatting & grammar** — Claude Haiku 4.5 cleans the transcript, removes
  fillers/false-starts, and resolves spoken self-corrections. Two modes:
  **Grammar** (default) also fixes grammatical errors to standard English —
  agreement, tense, did/didn't, don't/doesn't, double negatives, malformed phrases
  (e.g. "he don't know" → "he doesn't know", "it don't be done" → "it won't be
  done") — while preserving your meaning; **Faithful** never changes your words. An
  offline rule pass handles common fixes with no API key, and on any error it falls
  through to the raw transcript so words are never lost.
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
  nothing (with a distinct "cancelled" cue), instead of releasing and letting a
  misspoken take type out.
- **Quality-of-life** — audio cues, type-vs-paste insertion, local dictation
  history + stats, run-at-login, and a tray Pause/Resume. Cloud users dictate the
  instant the app is ready — the rarely-used local fallback warms in the
  background instead of blocking startup.

## Status

Built test-first. **All tests green: 197 Python + 28 Rust.** Ships as a release
build: an optimized `murmur.exe` plus a frozen `murmur-sidecar.exe` (no Python
required for end users) and an MSI installer. Runs fully offline out of the box;
cloud STT + AI formatting light up the moment you add an API key.

## Run from source (dev)

The Rust shell auto-detects the dev sidecar (`sidecar/.venv` + `main.py`) when no
frozen sidecar is bundled, so you can run straight from a debug build:

```powershell
# 1. sidecar deps (one time)
cd sidecar
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. run the app
cd ..\src-tauri
cargo run
```

A tray icon appears. With **no API keys** it transcribes locally (offline) and
types the raw transcript. Add keys in **tray → Settings** (or set the
`GROQ_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables) to
enable fast cloud STT + AI cleanup.

### Try dictating

1. Focus any text field (Notepad, your editor, a browser…).
2. **Hold `\`** — the recording pill appears at the bottom of the screen.
3. Speak, then **release** — the pill switches to *transcribing*, then the cleaned
   text is typed in.
4. A quick **tap** of `\` still just types a `\`.
5. Misspoke? Press **Esc** while recording to discard it — nothing is typed.

> If transcripts come back empty, check your Windows **default input device** is a
> real microphone.

## Build a release / installer

```powershell
.\build.ps1 -Release
```

This freezes the Python sidecar with PyInstaller (so end users need no Python),
builds the optimized Tauri app, and produces an MSI in
`src-tauri/target/release/bundle/msi`. The frozen sidecar is bundled next to the
shell so an installed copy is self-contained. (Omit `-Release` for a fast debug
build.)

## Layout

- `sidecar/` — Python pipeline: recorder, context detection, STT providers,
  corrections/grammar/intent, formatter, injector, orchestrator. Tests in
  `sidecar/tests`.
- `src-tauri/` — Rust/Tauri shell: global hotkey hook, sidecar supervisor, tray,
  overlay, config, commands, autostart.
- `ui/` — settings/dictionary web UI + the recording-indicator overlay.
- `docs/` — architecture notes and the original design + implementation plan.

See `CHANGELOG.md` for the feature history and `docs/ARCHITECTURE.md` for the
internals.

## Roadmap

- **Audio-reactive waveform** — drive the indicator's bars from live mic level.
- **Command mode** — select text and speak an instruction to rewrite it.
- **Streaming partials** — show text as you speak rather than on release.
- **Maybe later:** merge into a custom Windows taskbar (glassbar) as a managed
  voice module — the sidecar protocol is already compatible.
