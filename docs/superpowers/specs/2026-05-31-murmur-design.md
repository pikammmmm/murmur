# murmur — design spec

**Date:** 2026-05-31
**Status:** Approved design, pre-implementation
**Working name:** murmur (rename anytime)

## 1. What we're building

A self-hosted, Wispr-Flow-class voice dictation tool for Windows 11. Hold a key,
speak, release — and accurately-transcribed, correctly-punctuated,
context-appropriately-formatted text is typed into whatever field is focused
(email client, chat app, code editor, browser, anywhere). No subscription; the
running cost is a few cents a month.

It replaces a $15/mo ($144/yr) subscription with a tool whose recurring cost at
the user's volume is roughly **$1–2/month** (or near-zero if the formatter runs
on a free inference tier).

### Origin

This evolves the existing `%USERPROFILE%\voice-ptt\` tool, which already owns the
hard parts (audio capture, Whisper transcription, type-into-focused-window) and
already speaks a clean `stdin`-command / `stdout`-JSON-event protocol. murmur
wraps that Python core in a robust native Rust/Tauri shell and adds the two
things that make Wispr feel magical: **a frictionless trigger** and a
**context-aware AI formatting layer**.

## 2. Goals and non-goals

### Goals (must clear the Wispr bar)
- **Verbatim word-accuracy.** The transcript faithfully matches the words spoken.
  Custom dictionary biases the recognizer toward the user's names/jargon.
- **Context-aware formatting.** Detect the active app and format accordingly:
  an email gets greeting/structure, chat stays casual, code stays terse.
- **Correct grammar & punctuation.** Periods, question marks, commas,
  capitalization, paragraph breaks; filler/false-start removal; resolution of
  explicit self-corrections ("...50K, actually make that 75K" → "75K").
- **Low perceived latency.** Target sub-2s end-to-end for short utterances.
- **Frictionless trigger.** Hold a key, speak, release. Works system-wide.
- **Faithful, not creative.** The formatter must never paraphrase, reword, add
  content, or answer questions. It only cleans and structures the user's words.
- **Quality bar:** built test-first, with real verification at each step. We
  prefer to spend more time and ship something correct than ship fast and buggy.

### Non-goals (for now)
- Cross-platform (Windows-only v1; macOS/mobile out of scope).
- Real-time streaming partials (push-to-talk uploads the whole utterance; nice-to-have later).
- A "rewrite/polish" mode that changes wording (explicitly deferred; default is faithful cleanup).
- Diarization, translation-as-default, wake words (later, if at all).

## 3. Architecture

Two processes. Rust/Tauri is the **controller + UI**; Python is the **pipeline**.

```
                       ┌──────────────────────── Rust / Tauri core ───────────────────────┐
   hold Shift (PTT) ─► │  Win32 low-level keyboard hook (WH_KEYBOARD_LL)                    │
                       │  Tray icon (idle/recording/transcribing/error)                    │
                       │  Settings + dictionary UI (web frontend)                          │
                       │  Config owner → writes config.json                                │
                       │  Sidecar supervisor (spawn + backoff respawn)                     │
                       └───────────────┬───────────────────────────────▲───────────────────┘
                          stdin: toggle│start│stop│reload│quit          │ stdout: JSON events
                                       ▼                                │ (state / transcript / error)
                       ┌──────────────────────── Python sidecar ───────────────────────────┐
                       │  recorder  → context  → STT → formatter → injector                 │
                       │  (capture)   (app id)   (Groq    (Claude    (type into            │
                       │                          → local) Haiku)     focused field)        │
                       │  dictionary feeds both STT prompt and formatter                    │
                       └────────────────────────────────────────────────────────────────────┘
```

### Why this split
- The hard, already-solved problems (audio, Whisper, text injection, context via
  `pywin32`/UI Automation) have far more mature Python libraries; we reuse them.
- Rust/Tauri gives a rock-solid low-level global hotkey (the one thing Tauri's
  built-in shortcut plugin *cannot* do — it only handles press-chords, not
  hold), a real tray presence, and a polished settings UI.
- The `stdin`/`stdout`-JSON protocol is identical to what glassbar's old
  `VoiceController` spoke, so **merging murmur into the custom Windows taskbar
  later is a small lift** — that's an explicit design constraint, not an accident.

## 4. Components

### 4.1 Rust/Tauri core

**`hotkey`** — Global hold-to-talk via a `WH_KEYBOARD_LL` hook (using the
`windows` crate; same Win32 territory glassbar already uses).

The Shift-collision problem and its solution:
- Shift is used constantly for capitalization, so "hold Shift = record" naively
  fires on every capital letter. Disambiguation = **hold-alone heuristic**:
  - On chosen-key **down**, start a ~350ms timer; do not record yet.
  - If **any other key** goes down before the timer fires, cancel — this was
    capitalization/a shortcut, not dictation.
  - If the timer fires with the key still held and no other key pressed, **begin
    recording** (signal sidecar `start`).
  - On chosen-key **up**, if recording, **stop + transcribe** (`stop`).
  - The hook **observes only — it never suppresses** the key, so normal typing
    and capitalization are unaffected.
- Defaults: key = **Shift** (per user), threshold = 350ms, both configurable.
  A non-text key (e.g. right-Ctrl) can be selected to eliminate the threshold
  delay entirely.
- Edge cases to handle: key-repeat events (ignore repeats, act on true
  down/up transitions); focus loss / session lock while held; left vs right
  Shift (configurable: either, left-only, or right-only).

**`tray`** — Tray icon reflecting sidecar state; menu: Settings, Dictionary,
Pause/Resume, Quit.

**`settings_ui`** — Tauri web window: API keys, provider selection (STT &
formatter), hotkey + threshold, language, max-recording cap, dictionary editor,
per-app profile overrides.

**`config`** — Single source of truth. Writes `config.json` (schema in §7) to the
data dir; signals sidecar `reload` on change. API keys stored here (local only).

**`supervisor`** — Spawns the Python sidecar (hidden console), reads its
stdout JSON events, forwards `stdin` commands, and respawns with exponential
backoff (1s, 2s, 4s, 8s, 10s cap) on crash — the proven `VoiceController` pattern.

### 4.2 Python sidecar (seeded from `voice_ptt.py`)

Small, single-purpose modules, each independently testable:

- **`recorder.py`** — `sounddevice` capture at 16kHz mono float32, 60s cap.
  (Extracted from current `App._start_recording`/`_audio_callback`.)
- **`context.py`** — `GetForegroundWindow → GetWindowThreadProcessId → psutil`
  process exe + `GetWindowText` title → a **formatting profile**. Rules table:
  - `outlook.exe`, title `"- Outlook"` → `email`
  - `slack.exe`, `Teams.exe`, `Discord.exe` → `chat`
  - `Code.exe`, `devenv.exe`, `pycharm*` → `code`
  - browsers (`chrome.exe`/`msedge.exe`/`firefox.exe`) → classify by **title**
    substrings: `"Gmail"`/`"Outlook"` → email, `"Slack"` → chat, else `generic`
  - fallback → `generic`
  - Handle the UWP `ApplicationFrameHost.exe` case (walk child windows for the
    real PID).
  - **P2:** browser-URL classification via UI Automation (`uiautomation` lib),
    behind a flag — known-brittle, always falls back to title heuristics.
- **`stt/`** — provider interface + implementations:
  - `groq.py` (default) — `whisper-large-v3-turbo`, OpenAI-compatible SDK;
    `prompt=` biased with the dictionary terms.
  - `openai.py` — `gpt-4o-transcribe` ("accuracy mode" toggle; best on jargon).
  - `local.py` — `faster-whisper` int8 on CPU (offline fallback; what exists today).
  - Selection: try the configured cloud provider; on network/error, fall back to local.
- **`formatter/`** — provider interface + implementations:
  - `anthropic.py` (default) — Claude Haiku 4.5, temperature 0.
  - `groq.py` / `cerebras.py` — "fast mode" alternatives.
  - Holds the **faithful-cleanup system prompt** + per-profile style guidance
    (see §5). Input: raw transcript + profile + dictionary. Output: final text only.
- **`dictionary.py`** — user terms (names, jargon: "glassbar", "Rojo", "Luau",
  proper nouns). Injected into the STT prompt *and* given to the formatter as
  protected spellings. **P2:** auto-learn from user corrections.
- **`injector.py`** — `pynput` `.type()` into the focused field (exists today).
- **`app.py`** — orchestrator + state machine + config load + JSON event emit
  (reuses the existing `emit()` / event schema).

## 5. The faithful-cleanup contract

This is the load-bearing requirement: clean output that is still *the user's words*.

**System prompt (sketch):**
> You are a transcription formatter. You receive a raw speech-to-text transcript
> and a context label. Return ONLY the cleaned transcript. You MAY: fix
> punctuation, capitalization, and obvious grammar; remove filler words ("um",
> "uh", "like", "you know") and false starts; resolve explicit self-corrections
> (if the speaker says "actually" / "I mean" / "no wait" and restates, keep only
> the final intent); apply formatting appropriate to the context label. You MUST
> NOT: paraphrase, reword, add information, summarize, answer questions, or change
> the speaker's meaning or vocabulary. Preserve technical terms exactly, including
> any in the provided dictionary. If unsure, keep the original wording.

**Per-profile style guidance** appended to the prompt:
- `email` → greeting/sign-off if clearly dictated; sentence case; paragraph breaks.
- `chat` → casual, minimal structure, no forced capitalization of every line.
- `code` → terse; preserve identifiers; no prose embellishment; respect camelCase/snake_case from the dictionary.
- `notes`/`generic` → clean sentences and paragraphs, no added structure.

Temperature 0. Output capped to a sane multiple of input length as a guard
against runaway generation. A regression test suite of `(raw, profile) → expected`
cases enforces "no paraphrasing" behavior (see §9).

## 6. Data flow (one dictation)

1. User holds Shift alone → 350ms → core signals sidecar `start`.
2. `recorder` opens the mic, buffers audio; tray shows **recording**.
3. User releases Shift → core signals `stop`.
4. `context` resolves the active-app profile (sub-ms).
5. `stt` transcribes (cloud → local fallback), biased by the dictionary; tray
   shows **transcribing**.
6. `formatter` cleans + formats the raw transcript given the profile + dictionary.
7. `injector` types the final text into the focused field.
8. Sidecar emits `{"type":"transcript","text":...}`; tray returns to **idle**.

## 7. Config schema (`config.json`, owned by Rust core)

```jsonc
{
  "hotkey": { "key": "shift", "side": "either", "hold_threshold_ms": 350 },
  "stt": {
    "provider": "groq",            // groq | openai | local
    "accuracy_mode": false,        // true → openai gpt-4o-transcribe
    "language": "en",
    "groq_model": "whisper-large-v3-turbo",
    "local_model": "small"
  },
  "formatter": {
    "provider": "anthropic",       // anthropic | groq | cerebras | off
    "model": "claude-haiku-4-5",   // friendly alias → API id claude-haiku-4-5-20251001
    "mode": "faithful"             // faithful (only mode in v1)
  },
  "keys": { "groq": null, "openai": null, "anthropic": null, "cerebras": null },
  "max_recording_seconds": 60,
  "dictionary": ["glassbar", "Rojo", "Luau"],
  "profiles": { /* optional per-exe profile overrides */ }
}
```

API keys live here, local only. Env-var fallback (`GROQ_API_KEY`, etc.) retained.

## 8. IPC protocol (unchanged, glassbar-compatible)

- **stdin → sidecar** (one command per line): `start`, `stop`, `toggle`,
  `reload`, `quit`. (`start`/`stop` added for hold-to-talk; `toggle` retained for
  click-driven use and for a future glassbar merge.)
- **stdout → core** (one JSON object per line):
  - `{"type":"state","state":"loading|idle|recording|transcribing|error"}`
  - `{"type":"transcript","text":"..."}`
  - `{"type":"error","message":"..."}`
- **stderr** → diagnostic logs (never stdout, which is the event channel).

## 9. Testing & verification strategy

Quality is the priority, so testing is first-class:

- **Python sidecar (pytest):**
  - `context.py`: table-driven tests mapping `(exe, title)` → profile, incl. the
    `ApplicationFrameHost` and browser-title cases.
  - `formatter/`: golden `(raw, profile) → expected` cases with a **mock LLM**,
    plus a small live-API smoke test (opt-in) and **anti-paraphrase assertions**
    (output token-overlap / no-new-content checks) against real model responses.
  - `dictionary.py`: term injection into STT prompt + formatter protection.
  - `stt/`: provider selection + offline-fallback logic with mocked clients.
  - State machine: ignore-toggle-while-transcribing, max-cap, empty-audio paths.
- **Rust core:**
  - Hotkey state machine: unit tests for the hold-alone heuristic (down →
    other-key-cancel; down → threshold → record; up → stop; key-repeat ignore).
  - Config (de)serialization round-trips.
  - Supervisor backoff schedule.
- **End-to-end manual verification** (documented runbook): real dictation into
  Gmail, Slack, VS Code, Notepad; offline fallback by killing the network;
  capitalization-doesn't-trigger check; accuracy spot-check with dictionary terms.
- No success is claimed without running the relevant check and showing output.

## 10. Error handling

- Mic open failure → error beep + `error` event + return to idle.
- Cloud STT error/timeout → automatic local fallback; surface a one-time notice
  if no local model is available.
- Formatter error/timeout → **fall through to the raw transcript** (never lose
  the user's words) + an `error` event; never block injection on the LLM.
- Sidecar crash → supervisor respawn with backoff; tray shows error state.
- Network offline → STT local, formatter `off` (raw transcript) with a tray hint.

## 11. Privacy & security

- API keys local only (`config.json` in the app data dir; never logged, never
  committed). `.gitignore` excludes config and logs.
- Audio is sent to the configured cloud STT provider when online; the local
  fallback keeps a fully-offline path. This is surfaced in settings.
- **Never read password fields** (mirror Wispr): if/when selected-text/live
  context is added (P2+), skip secure/password input controls.
- Public-repo-safe from commit one: pikammmmm + noreply identity, no personal
  info, scrub before any public push.

## 12. Cost estimate

At ~30 min dictation/day:
- STT (Groq `whisper-large-v3-turbo` @ $0.04/hr): ~$0.60/month.
- Formatter (Claude Haiku 4.5, <$0.001/dictation): ~$1–2/month — or **$0** on
  Cerebras' free tier (1M tokens/day) or Groq's free tier in "fast mode".

Versus Wispr Flow's $15/month. Break-even is immediate.

## 13. Phased scope

- **Phase 1 — MVP (clears most of Wispr's felt value):**
  Rust core (hold-Shift hotkey w/ hold-alone heuristic, tray, settings UI,
  config, sidecar supervisor) + Python sidecar (recorder, basic exe+title
  context, Groq STT + local fallback, Claude Haiku faithful-cleanup formatter,
  manual dictionary, injector). Full test suites. Standalone, no glassbar.
- **Phase 2 — the signature edge:**
  Browser-URL context via UI Automation; dictionary auto-learn from corrections;
  voice formatting commands ("new paragraph", "bullet list", "new line").
- **Phase 3 — nice-to-have:**
  Command mode (select text + speak an instruction); style profiles; streaming
  partials for lower perceived latency.
- **Possible later:** merge into the custom Windows taskbar (glassbar) as a
  managed voice module, reusing the identical sidecar protocol.

## 14. Open questions / risks

- **Shift feel.** The 350ms hold-alone delay may feel laggy; right-Ctrl (no
  delay) is the fallback. Validate during P1 and adjust the default if needed.
- **Tauri sidecar packaging.** Bundling the Python interpreter + deps cleanly
  (PyInstaller one-file sidecar vs. a pinned venv) — decide in the plan; favor a
  frozen sidecar so end users need no Python install.
- **Hook reliability under elevated windows.** A `WH_KEYBOARD_LL` hook won't see
  input directed at higher-integrity (admin) windows unless murmur is elevated;
  document the limitation.
- **Formatter latency vs. faithfulness.** Haiku ~0.85s TTFT is fine for P1; if
  it feels slow, "fast mode" (Cerebras/Groq) is one config flip.
