# murmur

Self-hosted, Wispr-Flow-class **voice dictation for Windows**. Hold a key, speak,
release — and accurate, correctly-punctuated, context-appropriately-formatted
text is typed into whatever field is focused (email, chat, code editor, browser,
anywhere). No subscription; running cost is a few cents a month (or **$0** fully
offline).

## How it works

A native **Rust/Tauri shell** drives a **Python sidecar** over a tiny
`stdin`-command / `stdout`-JSON-event protocol:

```
hold Shift (alone, ~350ms)         Rust/Tauri core
   │                                ├─ Win32 WH_KEYBOARD_LL global hotkey
   ▼                                ├─ tray icon + settings/dictionary UI
 record ──────────────────────────▶├─ owns config.json, supervises sidecar
   │ release                        │      │ start / stop / reload / quit (stdin)
   ▼                                │      ▼
 transcribe + format + type ◀───────┴─ Python sidecar:
                                       recorder → context → STT → formatter → inject
```

- **STT:** Groq `whisper-large-v3-turbo` (cloud, fast/cheap) by default, with an
  automatic **local `faster-whisper` fallback** when offline. OpenAI
  `gpt-4o-transcribe` is available as an accuracy toggle.
- **Formatting:** Claude Haiku 4.5 in **faithful-cleanup mode** — fixes
  punctuation/grammar, removes fillers and false starts, resolves spoken
  self-corrections, and formats per the active app (email vs chat vs code) — but
  **never paraphrases, adds, or changes your words**. Falls through to the raw
  transcript on any error, so dictation never loses your words.
- **Context:** the active app + window title pick the formatting profile.
- **Custom dictionary:** your names/jargon bias the recognizer *and* are kept
  verbatim by the formatter.

## Status (Phase 1)

Built test-first. **All tests green:** 50 Python unit tests + 2 real Python
integration tests (SAPI→faster-whisper transcription, sidecar process boot) and
18 Rust unit tests. The full app has been launched and verified to boot, install
the global hotkey, spawn + supervise the sidecar, and reach `idle`.

The one step that needs you (interactive, can't be automated): holding the
hotkey and speaking into a mic — see the runbook below.

## Run it (works now, no packaging needed)

The Rust shell auto-detects the dev sidecar (`sidecar/.venv` + `main.py`) when no
frozen sidecar is bundled, so you can run straight from a debug build:

```powershell
# 1. sidecar deps (one time)
cd sidecar
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. run the app
cd ..\src-tauri
cargo run            # or cargo build && run target\debug\murmur.exe
```

A tray icon appears. With **no API keys**, it transcribes locally (offline) and
types the raw transcript. Add keys in **tray → Settings** (or set
`GROQ_API_KEY` / `ANTHROPIC_API_KEY`) to enable fast cloud STT + AI cleanup.

### Manual dictation test

1. Focus any text field (Notepad, Gmail, VS Code…).
2. **Hold Shift alone** for ~0.35s (don't press other keys) — the tray shows
   *recording*.
3. Speak, then **release Shift** — tray shows *transcribing*, then the cleaned
   text is typed in.
4. Quick `Shift`+letter capitalization must **not** trigger recording.

> Default input on this machine is "Microphone (Voicemod)" — a virtual mic that
> needs Voicemod running. Switch the Windows default input if transcripts come
> back empty.

If the Shift hold-delay feels off, switch the trigger to **Right Ctrl** in
Settings (zero delay, since it's not a typing key).

## Build a release / installer

```powershell
# Freeze the sidecar so end users need no Python (output: sidecar/dist/murmur-sidecar.exe)
cd sidecar
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\pyinstaller --onefile --name murmur-sidecar `
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all tokenizers main.py
# Copy sidecar/dist/murmur-sidecar.exe next to murmur.exe (or into Tauri resources),
# then:
cd ..\src-tauri
cargo tauri build         # produces an MSI in target/release/bundle
```

The shell prefers a `murmur-sidecar.exe` sitting next to it; otherwise it falls
back to the dev venv.

## Layout

- `sidecar/` — Python pipeline (recorder, context, STT providers, formatter,
  dictionary, injector, orchestrator). Tests in `sidecar/tests`.
- `src-tauri/` — Rust/Tauri shell (hotkey hook, supervisor, tray, config, commands).
- `ui/` — settings + dictionary web UI.
- `docs/superpowers/specs` & `docs/superpowers/plans` — design + implementation plan.

See `sidecar/RUNBOOK.md` for sidecar-level details and `docs/` for the full design.

## Roadmap

- **Phase 2:** browser-URL context (UI Automation), dictionary auto-learn from
  corrections, voice formatting commands ("new paragraph", "bullet list").
- **Phase 3:** command mode (select text + speak an instruction), style profiles,
  streaming partials.
- **Maybe later:** merge into the custom Windows taskbar (glassbar) as a managed
  voice module — the sidecar protocol is already compatible.
