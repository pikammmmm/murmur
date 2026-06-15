# Changelog

## Phase 1 — MVP
- Rust/Tauri shell + Python sidecar over a stdin/stdout JSON protocol.
- Hold-Shift push-to-talk (hold-alone heuristic so capitalization doesn't trigger).
- STT: Groq `whisper-large-v3-turbo` primary, local faster-whisper fallback,
  OpenAI `gpt-4o-transcribe` accuracy mode.
- Context-aware formatting (Claude Haiku 4.5): email / chat / code / notes / generic.
- Custom dictionary; tray icon; settings UI; sidecar supervisor with backoff respawn.
- Frozen-sidecar packaging (no Python needed for end users).

## Phase 2a — pronunciation & accent adaptation
- STT biasing (faster-whisper `hotwords`, cloud `prompt`) from dictionary + learned terms.
- Correction engine: exact learned substitutions + Double-Metaphone phonetic match
  + fuzzy fallback, precision-gated to never touch correct text.
- Learns from corrections (difflib diff of raw vs. your fix); manual entries.
- "Pronunciations & corrections" + "Teach last dictation" UI.

## Phase 2b — grammar correction
- Formatter `mode`: **grammar** (default) fixes agreement, tense, did/didn't,
  don't/doesn't, double negatives, malformed phrases; **faithful** stays verbatim.
- Offline high-precision grammar rule pass (works with no API key).
- "Cleanup level" selector.

## Phase 3 — polish
- Voice commands: "new paragraph", "new line", "new bullet", "scratch that".
- Audio cues (record/stop/error beeps), toggleable.
- Clipboard-paste injection mode (robust for apps that reject synthetic typing).
- Dictation history + usage stats (words, sessions, time saved), local + clearable.
- Run-at-login toggle (HKCU Run key).
- Tray Pause/Resume (suspends the hotkey).
- "Try it" offline preview — see corrections + grammar + voice commands live.
- Language + max-recording settings; first-run "offline mode" hint.
- Build script (`build.ps1`) + architecture doc.
- Hardened via a multi-agent code review: thread-safe `last_raw`; resilient stdin
  loop (one bad command can't kill it); recorder stream-leak fix on mic error;
  clipboard always restored (finally); non-panicking tray icon; control-char
  input sanitization; durable `sidecar.log`; error handling on every UI call.

## Phase 4 — content awareness + recording indicator
- **Content-aware formatting:** detects email-shaped speech ("dear …", "regards,",
  "send an email", "subject:") and shopping/to-do lists ("shopping list",
  "I need to buy", enumerated items) and formats accordingly, overriding the
  per-app profile. Offline lists become bullets; the LLM refines when keys are set.
- **Recording-indicator overlay:** a transparent, always-on-top, click-through
  "blob" (Wispr-style) that pulses blue while you hold to speak and breathes while
  transcribing. Toggleable in settings.
- **Keybind:** hold **`\`** (backslash) to talk — a dual-function key (a quick tap
  still types a `\`; only a held press records). Shift / Right Ctrl / Right Alt /
  Caps Lock remain selectable in settings.

## Phase 5 — distribution build
- **Release build** (`build.ps1 -Release`): frozen PyInstaller sidecar
  (`murmur-sidecar.exe`, ~101 MB, no Python needed) + optimized Tauri shell
  (`murmur.exe`, 3.5 MB) + WiX MSI installer (`murmur_0.1.0_x64_en-US.msi`).
- **Installer fix:** the sidecar is now embedded as a bundle resource and
  installs next to the shell — previously the MSI shipped only the 3.5 MB shell,
  so an installed copy launched a dead UI with no sidecar. `resolve_launch` also
  checks a `resources/` subdir as a fallback. Verified by admin-extracting the
  MSI: `PFiles\murmur\{murmur.exe, murmur-sidecar.exe}` land side by side.
- **Verified:** 145 Python + 22 Rust tests pass; the frozen sidecar boots
  standalone (loads faster-whisper base int8, warms, emits state events, exits
  clean) — confirming the onefile's native deps (ctranslate2, onnxruntime)
  resolve at runtime.

## Phase 6 — working keybind + Wispr-style indicator
- **Fix: hold-`\` never started recording.** The `\` trigger is suppressed so it
  doesn't type while held, but a key swallowed by a low-level hook is invisible
  to `GetAsyncKeyState` — the 20 ms self-heal saw it as released ~20 ms in,
  cancelled the arm before the hold threshold, and recording never started (you
  got a stray `\` on release). Text keys now self-heal off our own `PENDING`
  flag; only modifier keys use the physical-state probe.
- **Recording indicator redesigned** to a Wispr "Flow Bar"-style pill: a dark
  frosted pill at bottom-center with an animated 14-bar **waveform + red
  live-dot** while listening, and a blue **thinking pulse** while transcribing.
- **Tray → "Preview indicator"** cycles the overlay through its states with no
  mic, so the look can be checked independently of dictation.

## Phase 7 — accuracy
- **Built-in technical vocabulary** (`vocab.py`): a curated set of common program/
  brand words (GitHub, Discord, autostart, Roblox, Rust, Luau, Tauri, …) always
  fed to the STT bias so they're recognized out of the box. User dictionary terms
  still come first; the broad list is bias-only and kept out of the phonetic
  corrector to avoid over-correcting common English.
- **Bigger offline model** — default faster-whisper `base` → `small`.
- **GPU transcription (`"gpu"` provider):** full Whisper on any DirectX 12 GPU
  via **DirectML** — including AMD/Intel, no CUDA. Big accuracy jump, fully
  offline; CPU `faster-whisper` stays as automatic fallback. The GPU stack
  (torch + DirectML + the model) is optional and lives in `requirements-gpu.txt`,
  so it runs via the dev venv and the frozen/distributed sidecar stays lean
  (CPU). Sparse `alignment_heads` is densified so the model moves to the DirectML
  device. Default model is **`large-v3-turbo`** (≈2× faster than large-v3 at
  equal accuracy) at **beam 3** (matched beam-5 accuracy for ~27% less time).
  Verified on a Radeon RX 7800 XT: ~1.9 s for a ~13 s clip.
- **Punctuation fix (GPU path):** stopped passing the vocabulary glossary as
  openai-whisper's `initial_prompt` — an unpunctuated list there made the model
  drop periods/commas/apostrophes (and it wasn't improving recognition). turbo's
  native accuracy handles the terms; the post-STT corrector still fixes
  user-taught words. (faster-whisper `hotwords` and cloud `prompt` are unaffected
  and keep biasing.)

## Phase 8 — startup latency + cancel
- **Cloud users dictate instantly at launch.** Warm-up used to load the local
  faster-whisper fallback *synchronously* before the sidecar reported `idle` —
  even when the primary is a cloud engine (Groq/OpenAI) with no `warm()`. So a
  cloud user paid a full CPU model load at every launch/login before they could
  dictate, for a model their first dictation never touches. Warm-up is now split
  (`warmup.plan`/`run`): a local/GPU primary + the mic stay synchronous (needed
  before idle), but a cloud primary's local fallback warms on a daemon thread
  *after* idle — still ready if the cloud later fails, without gating readiness.
- **Esc cancels an in-progress dictation.** Press Esc while recording to discard
  the capture and type nothing, instead of being forced to release and let
  garbage type (then undo). A distinct descending "cancel" cue confirms the
  discard. The `PttState` gains a `cancel()` that latches so a still-held trigger
  can't immediately restart, clearing on release (or via the timer self-heal if
  that key-up is missed behind an elevated window); the hook swallows the Esc so
  the focused app doesn't also receive it, and suppresses the stray `\` the
  text-key release would otherwise synthesize. New sidecar `cancel` command.
- **Tests:** 197 Python + 28 Rust green.
