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
- **More keybind options:** Right Alt and Caps Lock, alongside the default hold-Shift.
