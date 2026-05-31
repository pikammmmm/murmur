# murmur architecture

Two processes. The **Rust/Tauri shell** is the controller + UI; the **Python
sidecar** is the dictation pipeline. They talk over a line protocol on the
sidecar's stdin/stdout (identical in spirit to glassbar's old VoiceController,
so murmur can later be folded into the taskbar).

```
                 ┌──────────────── Rust / Tauri shell ─────────────────┐
 hold Shift ───► │ hotkey.rs   WH_KEYBOARD_LL hook + hold-alone PTT     │
 (alone, 350ms)  │ ptt.rs      pure state machine (unit-tested)         │
                 │ tray.rs     tray icon + Pause/Resume + state tooltip │
                 │ commands.rs settings/dictionary/corrections/history  │
                 │ config.rs   owns config.json (schema mirrors Python) │
                 │ autostart.rs HKCU Run-key toggle                     │
                 │ sidecar.rs  spawn + supervise + backoff respawn      │
                 └───────┬───────────────────────────────▲──────────────┘
        stdin commands   │                               │  stdout JSON events
  start stop toggle      ▼                               │  state / transcript /
  reload quit learn                                      │  error / last_raw /
  correctadd correctdel              ┌───────────────────┴──┐  corrections / preview
  clearhistory preview snapshot      │   Python sidecar      │
                                     │  app.py orchestrator  │
                                     └───────────────────────┘
```

## Sidecar pipeline (one dictation)

`recorder` → `context` (active-app profile) → `stt` (cloud primary, local
fallback, biased by dictionary+corrections) → `corrections` (exact + phonetic +
fuzzy) → `grammar` (rule pass, grammar mode) → `voicecommands` ("new paragraph",
"scratch that", …) → `formatter` (Claude Haiku; grammar/faithful/off) →
`injector` (type or clipboard-paste) → emit transcript + record `history`.

Every stage degrades safely: cloud STT → local; formatter error/timeout → raw
transcript; any exception → error event + idle. The user's words are never lost.

## Modules

**Python** (`sidecar/murmur_sidecar/`)
- `app.py` — orchestrator, PTT state machine, stdin loop, learning + preview.
- `config.py` — defaults + deep-merge load + env-var key resolution.
- `events.py` — atomic JSON stdout channel.
- `context.py` — foreground exe + title → formatting profile.
- `stt/` — `base` (selection + fallback), `groq`, `openai`, `local`, `wavutil`.
- `formatter/` — `base` (+ make/format), `prompts` (mode-shaped), `anthropic`, `openai_compat`.
- `corrections.py` — learning store + phonetic/fuzzy corrector + bias terms.
- `grammar.py` — high-precision offline grammar rules.
- `voicecommands.py` — spoken structural commands.
- `dictionary.py` — vocabulary biasing.
- `recorder.py`, `injector.py`, `history.py`, `cues.py`.

**Rust** (`src-tauri/src/`) — `main`, `hotkey`, `ptt`, `sidecar`, `tray`,
`commands`, `config`, `logger`, `autostart`.

**UI** (`ui/`) — single settings window (`index.html`/`main.js`/`style.css`).

## Key invariants

- `config.json` schema is shared: every field in Rust `config.rs` must match
  Python `config.py` `DEFAULTS` (name, nesting, type). Both backfill missing keys.
- The sidecar is the single writer of `corrections.json` / `history.jsonl` /
  `stats.json`; the shell reads them (or caches via events) for display.
- API keys live only in `config.json` (gitignored); never logged.
- The hotkey hook never suppresses the trigger key (typing/capitalization intact).

## Tests

`sidecar/`: `pytest` — fast unit tests + slow integration (real local STT via
SAPI, sidecar process boot, manual-correction + preview round-trips).
`src-tauri/`: `cargo test` — config round-trip, PTT state machine, event parsing,
hotkey mapping, tray tooltips, autostart round-trip.
