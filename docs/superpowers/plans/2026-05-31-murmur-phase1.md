# murmur Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working, tested Windows voice-dictation tool: hold Shift → speak → release → accurate, punctuated, context-formatted text typed into the focused field.

**Architecture:** Rust/Tauri shell (global hotkey, tray, settings UI, config owner, sidecar supervisor) drives a Python sidecar (audio → context → STT → faithful-cleanup formatter → inject) over a `stdin`-command / `stdout`-JSON-event protocol identical to glassbar's old `VoiceController`.

**Tech Stack:** Python 3.12 (sounddevice, numpy, faster-whisper, groq, openai, anthropic, pywin32, psutil, pynput, pytest); Rust 1.95 + Tauri 2 (windows crate 0.58, directories, serde, tray-icon).

**Verifiability note:** The Python sidecar is fully testable offline (local faster-whisper path needs no API key). The Rust hotkey/tray require interactive use to fully exercise; their *pure logic* (hold-alone state machine, config, backoff) is unit-tested, and a manual runbook covers the interactive parts. No API keys are present on this machine, so cloud providers are built behind mocks + config slots and the tool defaults to the local STT path.

---

## File structure

```
murmur/
  sidecar/
    murmur_sidecar/
      __init__.py
      app.py            # orchestrator + state machine + stdin loop
      events.py         # JSON stdout event emit (atomic)
      config.py         # read config.json (Rust owns writing it)
      context.py        # foreground exe+title -> formatting profile
      dictionary.py     # user terms -> STT prompt bias + formatter protect-list
      recorder.py       # sounddevice capture
      injector.py       # pynput type-into-focused-window
      stt/
        __init__.py
        base.py         # Transcriber protocol + selection/fallback
        local.py        # faster-whisper int8 CPU
        groq.py         # whisper-large-v3-turbo
        openai.py       # gpt-4o-transcribe (accuracy mode)
      formatter/
        __init__.py
        base.py         # Formatter protocol + prompt builder + selection
        prompts.py      # faithful-cleanup system prompt + per-profile guidance
        anthropic.py    # Claude Haiku 4.5
        groq.py         # fast-mode llama
    tests/              # pytest
    requirements.txt
    pyproject.toml      # pytest config
  src-tauri/
    Cargo.toml
    tauri.conf.json
    build.rs
    capabilities/default.json
    src/
      main.rs           # wire setup, tray, hotkey thread, supervisor, event pump
      logger.rs         # mlog! macro -> data-dir/debug.log (mirror glassbar)
      config.rs         # Config struct + load/save (directories)
      hotkey.rs         # WH_KEYBOARD_LL hook + hold-alone state machine
      ptt.rs            # pure hold-alone state machine (unit-testable, no Win32)
      sidecar.rs        # spawn + supervise Python sidecar, parse events
      tray.rs           # tray icon + menu + state
      commands.rs       # Tauri commands: get/set config, dictionary CRUD
  ui/
    index.html          # settings + dictionary UI
    main.js
    style.css
  docs/superpowers/...  # spec + this plan
```

---

## PART A — Python sidecar (fully testable offline)

### Task A1: Sidecar scaffold + config loader

**Files:**
- Create: `sidecar/murmur_sidecar/__init__.py` (empty), `sidecar/murmur_sidecar/config.py`
- Create: `sidecar/requirements.txt`, `sidecar/pyproject.toml`
- Test: `sidecar/tests/test_config.py`

- [ ] **Step 1: requirements.txt**

```
sounddevice>=0.4.6
numpy>=1.24
faster-whisper>=1.0.0
groq>=0.11
openai>=1.0.0
anthropic>=0.39
pynput>=1.7.6
pywin32>=306
psutil>=5.9
pytest>=8.0
```

- [ ] **Step 2: pyproject.toml** (pytest config)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: Write failing test** `tests/test_config.py`

```python
import json
from murmur_sidecar.config import load_config, DEFAULTS

def test_defaults_used_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "nope.json")
    assert cfg["stt"]["provider"] == "groq"
    assert cfg["formatter"]["provider"] == "anthropic"
    assert cfg["hotkey"]["hold_threshold_ms"] == 350

def test_file_overrides_and_backfills(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"stt": {"provider": "local"}}))
    cfg = load_config(p)
    assert cfg["stt"]["provider"] == "local"          # override kept
    assert cfg["formatter"]["provider"] == "anthropic" # default backfilled
```

- [ ] **Step 4: Implement** `config.py` with `DEFAULTS` (mirroring spec §7) and a `load_config(path)` that deep-merges file over defaults; if file missing, return defaults. Resolve API keys from config then env (`GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY`).
- [ ] **Step 5:** Run `python -m pytest tests/test_config.py -v` → PASS.
- [ ] **Step 6:** Commit `feat(sidecar): config loader with defaults + env key resolution`.

### Task A2: events.py (JSON stdout protocol)

**Files:** Create `murmur_sidecar/events.py`; Test `tests/test_events.py`

- [ ] Write failing test: capture stdout, call `emit({"type":"state","state":"idle"})`, assert one JSON line parses back equal. Thread-safety: 50 concurrent emits → 50 well-formed lines.
- [ ] Implement: lock-guarded `emit(obj)` writing `json.dumps(obj, ensure_ascii=False)+"\n"` to stdout + flush (lift from `voice_ptt.py:69-77`). Helpers `state(s)`, `transcript(t)`, `error(m)`.
- [ ] Run tests → PASS. Commit `feat(sidecar): atomic JSON event channel`.

### Task A3: context.py (active-app → profile)

**Files:** Create `murmur_sidecar/context.py`; Test `tests/test_context.py`

- [ ] **Step 1: Failing test** — pure classifier `classify(exe, title)`:

```python
from murmur_sidecar.context import classify
import pytest

@pytest.mark.parametrize("exe,title,expected", [
    ("OUTLOOK.EXE", "Inbox - me@x.com - Outlook", "email"),
    ("slack.exe", "Slack | general | Acme", "chat"),
    ("Teams.exe", "Chat | Microsoft Teams", "chat"),
    ("Code.exe", "file.py - murmur - Visual Studio Code", "code"),
    ("chrome.exe", "Inbox (3) - me@gmail.com - Gmail - Google Chrome", "email"),
    ("chrome.exe", "Slack | #dev - Google Chrome", "chat"),
    ("chrome.exe", "Some random article - Google Chrome", "generic"),
    ("notepad.exe", "Untitled - Notepad", "notes"),
    ("randomgame.exe", "whatever", "generic"),
])
def test_classify(exe, title, expected):
    assert classify(exe, title) == expected
```

- [ ] **Step 2:** Run → fails (no module).
- [ ] **Step 3: Implement** `classify(exe, title)`: lowercase exe; rules table — email exes {outlook.exe}, chat exes {slack.exe, teams.exe, discord.exe}, code exes {code.exe, devenv.exe, pycharm64.exe, idea64.exe, sublime_text.exe}, notes exes {notepad.exe, notepad++.exe, obsidian.exe, wordpad.exe, winword.exe}; browser exes {chrome.exe, msedge.exe, firefox.exe, brave.exe, opera.exe} → inspect title: contains "gmail"|"outlook"|"- mail" → email; "slack"|"discord"|"teams"|"whatsapp"|"messenger" → chat; "github"|"stack overflow"|"visual studio code" → code; else generic. Non-browser unknown → generic.
- [ ] **Step 4:** Add `detect()` that calls `win32gui.GetForegroundWindow()` → `win32process.GetWindowThreadProcessId` → `psutil.Process(pid).name()` + `GetWindowText`, handles `ApplicationFrameHost.exe` by walking child windows for a child with a different PID (guarded in try/except; on any failure return `("","")`). `detect()` returns `(profile, exe, title)`. Guard imports so non-Windows test runs still import the module (wrap pywin32 import; `detect()` may raise only at call time).
- [ ] **Step 5:** Run → PASS. Commit `feat(sidecar): active-app context classifier`.

### Task A4: dictionary.py

**Files:** Create `murmur_sidecar/dictionary.py`; Test `tests/test_dictionary.py`

- [ ] Failing test: `build_stt_prompt(["glassbar","Rojo","Luau"])` returns a string containing all terms (used as Whisper `prompt=` bias); `build_stt_prompt([])` returns `""`. `protect_clause(terms)` returns a formatter instruction listing the terms, empty string for `[]`.
- [ ] Implement both. STT prompt: `"Vocabulary: " + ", ".join(terms) + "."`. Protect clause: `"Preserve these terms exactly: " + ", ".join(terms) + "."`.
- [ ] Run → PASS. Commit `feat(sidecar): custom dictionary biasing`.

### Task A5: STT layer (local + cloud + fallback)

**Files:** Create `murmur_sidecar/stt/{__init__.py,base.py,local.py,groq.py,openai.py}`; Test `tests/test_stt.py`

- [ ] **Step 1: Failing test** for selection/fallback with fakes:

```python
from murmur_sidecar.stt.base import transcribe_with_fallback

class FakeOK:
    def transcribe(self, audio, sr, prompt): return "hello world"
class FakeFail:
    def transcribe(self, audio, sr, prompt): raise RuntimeError("net down")

def test_primary_used():
    assert transcribe_with_fallback(FakeOK(), FakeOK(), b"", 16000, "") == "hello world"

def test_falls_back_on_primary_error():
    assert transcribe_with_fallback(FakeFail(), FakeOK(), b"", 16000, "") == "hello world"

def test_both_fail_returns_empty():
    assert transcribe_with_fallback(FakeFail(), FakeFail(), b"", 16000, "") == ""
```

- [ ] **Step 2:** Run → fail.
- [ ] **Step 3: Implement** `base.py`: `Transcriber` protocol (`transcribe(audio_f32_mono, sr, prompt) -> str`); `transcribe_with_fallback(primary, fallback, audio, sr, prompt)` tries primary, on Exception logs + tries fallback, on Exception returns `""`; `make_transcriber(cfg, keys)` factory returning `(primary, fallback)` — primary from `cfg["stt"]["provider"]` (groq/openai/local; if cloud chosen but no key → primary=local, fallback=None), fallback always `LocalTranscriber` unless primary already local.
- [ ] **Step 4: Implement** `local.py` (`faster-whisper`, lazy-load model from `cfg.local_model`, int8 cpu, `vad_filter`, `condition_on_previous_text=False`, accepts `prompt` as `initial_prompt`); `groq.py` (`groq` SDK `audio.transcriptions.create(model=..., file=wav_bytes, prompt=prompt, language=...)`, encode float32→wav in-memory like `voice_ptt.py:233-240`); `openai.py` (same but OpenAI SDK + `gpt-4o-transcribe`). Cloud modules import their SDK lazily inside `transcribe`.
- [ ] **Step 5:** Run selection/fallback tests → PASS (no network used).
- [ ] **Step 6 (real, offline):** Add `tests/test_local_stt.py` marked `@pytest.mark.slow`: synthesize 1s of silence + a known WAV if available, assert `LocalTranscriber().transcribe(...)` returns a `str` without raising (downloads the `base` model). Run `python -m pytest tests/test_local_stt.py -v -m slow`.
- [ ] **Step 7:** Commit `feat(sidecar): STT providers with local fallback`.

### Task A6: Formatter layer (faithful cleanup)

**Files:** Create `murmur_sidecar/formatter/{__init__.py,base.py,prompts.py,anthropic.py,groq.py}`; Test `tests/test_formatter.py`

- [ ] **Step 1: prompts.py** — `SYSTEM` (the faithful-cleanup contract from spec §5) + `PROFILE_GUIDANCE = {"email":..., "chat":..., "code":..., "notes":..., "generic":...}`; `build_messages(raw, profile, dict_terms)` returns `(system, user)` where system = SYSTEM + profile guidance + dictionary protect-clause, user = raw transcript.
- [ ] **Step 2: Failing test** with a fake LLM:

```python
from murmur_sidecar.formatter.base import format_text

class Echo:
    def __init__(self, out): self.out=out; self.seen=None
    def complete(self, system, user): self.seen=(system,user); return self.out

def test_returns_model_output():
    f = Echo("Hello, world.")
    assert format_text(f, "hello world", "generic", []) == "Hello, world."

def test_system_carries_profile_and_dict():
    f = Echo("x")
    format_text(f, "raw", "email", ["Rojo"])
    sys, _ = f.seen
    assert "email" in sys.lower() and "Rojo" in sys

def test_empty_input_short_circuits():
    f = Echo("should-not-be-called")
    assert format_text(f, "   ", "generic", []) == ""

def test_formatter_error_falls_through_to_raw():
    class Boom:
        def complete(self, s,u): raise RuntimeError("api down")
    assert format_text(Boom(), "my raw words", "generic", []) == "my raw words"
```

- [ ] **Step 3:** Run → fail.
- [ ] **Step 4: Implement** `base.py`: `Formatter` protocol (`complete(system,user)->str`); `format_text(formatter, raw, profile, terms)`: if `raw.strip()==""` return `""`; build messages; call `complete`; on Exception log + **return the raw transcript** (never lose words); guard output length (if len > 4× input chars, return raw). `make_formatter(cfg, keys)`: anthropic/groq/`off`(returns a passthrough formatter that returns raw).
- [ ] **Step 5: Implement** `anthropic.py` (Anthropic SDK, `messages.create(model, system=system, messages=[{"role":"user","content":user}], temperature=0, max_tokens=1024)`, lazy import) and `groq.py` (chat completions, temp 0).
- [ ] **Step 6:** Run → PASS. Commit `feat(sidecar): faithful-cleanup formatter`.
- [ ] **Step 7 (anti-paraphrase guard test):** `tests/test_faithful.py` with a fake that would paraphrase — assert our length-guard + passthrough behavior holds. (Live-model fidelity is checked in the manual runbook, opt-in with a key.)

### Task A7: recorder.py + injector.py

**Files:** Create `murmur_sidecar/recorder.py`, `murmur_sidecar/injector.py`; Tests `tests/test_recorder.py`, `tests/test_injector.py`

- [ ] recorder: `Recorder` class with `start()`/`stop()->np.float32 mono @16k` using `sounddevice.InputStream` (lift `voice_ptt.py:145-197`), 60s cap via caller. Test with a fake stream (monkeypatch `sounddevice`) that feeds 2 chunks → `stop()` concatenates them.
- [ ] injector: `type_text(text)` via `pynput` Controller (lift `voice_ptt.py:204`); test monkeypatches the controller and asserts `.type` called with the text; empty text is a no-op.
- [ ] Run → PASS. Commit `feat(sidecar): recorder + text injector`.

### Task A8: app.py orchestrator + stdin loop

**Files:** Create `murmur_sidecar/app.py`; Test `tests/test_app.py`

- [ ] **Step 1: Failing test** for the state machine with all deps faked (recorder, transcriber pair, formatter, injector, context.detect): drive `App.start()` then `App.stop()`; assert sequence of emitted states `recording → transcribing → idle`, that injector got the formatted text, and a `transcript` event was emitted. Test `stop` with empty audio → goes back to `idle`, no inject. Test that `start` while already recording is ignored; `toggle` flips.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3: Implement** `App` with injected collaborators (constructor takes them; a `build()` factory wires real ones from config). Methods: `start()`, `stop()` (runs context→stt→format→inject on a worker thread, emits states), `toggle()`, `reload(cfg)`. Reuse the locking/threading shape from `voice_ptt.py:128-211` but split record vs transcribe and add the context+format steps. `stdin_command_loop` dispatches `start|stop|toggle|reload|quit` (extends `voice_ptt.py:252-267`).
- [ ] **Step 4:** Run → PASS. Commit `feat(sidecar): orchestrator + start/stop/toggle/reload protocol`.

### Task A9: sidecar entrypoint + end-to-end smoke

**Files:** Create `sidecar/main.py` (`python -m` entry → `App.build(); loop`); `sidecar/RUNBOOK.md`

- [ ] Implement `main.py`: load config (path from `MURMUR_CONFIG` env or default data dir), build App, warm the local model (mirror `voice_ptt.py:119-124`), emit `loading→idle`, run stdin loop.
- [ ] **Manual smoke (documented + run):** `python main.py`, type `start`, speak/wait, type `stop`; observe JSON `state`/`transcript` events; verify text typed into a focused Notepad. Record results in RUNBOOK.md.
- [ ] Commit `feat(sidecar): entrypoint + runbook`.

---

## PART B — Rust/Tauri shell

### Task B1: Tauri scaffold

**Files:** Create `src-tauri/{Cargo.toml,tauri.conf.json,build.rs,capabilities/default.json}`, `src-tauri/src/main.rs` (skeleton), `ui/{index.html,main.js,style.css}` (minimal).

- [ ] Cargo.toml: package `murmur`, Tauri 2 with `features=["tray-icon"]`, windows crate 0.58 (features: Foundation, Win32_Foundation, Win32_UI_WindowsAndMessaging, Win32_UI_Input_KeyboardAndMouse, Win32_System_Threading), serde, serde_json, directories 5, anyhow. dev-dep: none yet. `[profile.release]` mirror glassbar.
- [ ] tauri.conf.json: productName `murmur`, identifier `com.murmur.app`, `frontendDist: ../ui`, `withGlobalTauri: true`, no auto windows (settings window opened on demand), bundle msi + `resources` to include the frozen sidecar later.
- [ ] build.rs: `fn main(){ tauri_build::build(); }`. capabilities/default.json: core defaults.
- [ ] main.rs skeleton: `tauri::Builder::default().run(...)` that just starts (no tray yet) — confirm it compiles.
- [ ] **Verify:** `cargo build` in `src-tauri` succeeds. Commit `chore(app): tauri 2 scaffold`.

### Task B2: logger.rs + config.rs

- [ ] logger.rs: `mlog!` macro writing timestamped lines to `<data_dir>/debug.log` (mirror glassbar `logger.rs`; data dir via `directories::ProjectDirs::from("com","murmur","murmur")`). Test: writing creates the file.
- [ ] config.rs: `Config` serde struct matching spec §7; `load()/save()` to `<data_dir>/config.json`; `default()` impl. Unit tests: round-trip serialize/deserialize; missing-file → defaults; partial JSON → backfills (serde `#[serde(default)]`).
- [ ] `cargo test` → PASS. Commit `feat(app): logger + config`.

### Task B3: ptt.rs — pure hold-alone state machine (unit-tested)

**Files:** Create `src-tauri/src/ptt.rs`; tests inline `#[cfg(test)]`.

- [ ] **Step 1: Failing tests** for a Win32-free state machine:

```rust
// Events fed by the hook; Actions consumed by the app.
// PttState::on_key_down(is_trigger, now_ms), on_key_up(is_trigger, now_ms),
// on_other_key(now_ms), on_tick(now_ms) -> Option<Action> {StartRecording, StopRecording}
#[test] fn hold_alone_past_threshold_starts() { /* down(trigger,0); tick(400)->StartRecording */ }
#[test] fn other_key_before_threshold_cancels() { /* down(trigger,0); other(100); tick(400)->None */ }
#[test] fn release_after_recording_stops() { /* down(0); tick(400)->Start; up(trigger,500)->Stop */ }
#[test] fn release_before_threshold_no_action() { /* down(0); up(trigger,100)->None */ }
#[test] fn key_repeat_ignored() { /* down(0); down(0 repeat) doesn't reset timer */ }
```

- [ ] **Step 2:** Implement `PttState { threshold_ms, armed_at: Option<u64>, recording: bool, canceled: bool }` with the transition rules. No Win32 — `now_ms` injected.
- [ ] **Step 3:** `cargo test ptt` → PASS. Commit `feat(app): hold-alone PTT state machine + tests`.

### Task B4: hotkey.rs — WH_KEYBOARD_LL hook

**Files:** Create `src-tauri/src/hotkey.rs` (adapt glassbar `keyhook.rs`).

- [ ] Implement: `spawn(trigger_vk, side, threshold_ms, tx)` installs `WH_KEYBOARD_LL` on a dedicated message-pump thread (pattern from `keyhook.rs:55-78`). Callback feeds a global `PttState` (behind a Mutex/atomics): on trigger down/up and on any other key, computes `Action`; a companion timer thread calls `on_tick` to fire the threshold. Cross-check real key state with `GetAsyncKeyState` to self-heal the elevated-window stale-state bug (per `keyhook.rs:128-147` rationale). **Never suppress** the trigger key (return `CallNextHookEx`). Emits `Start`/`Stop` over an `mpsc`/channel to the app. Distinguish left/right Shift via `VK_LSHIFT(0xA0)`/`VK_RSHIFT(0xA1)` + scancode.
- [ ] **Verify:** compiles; pure logic already covered by B3. Interactive verification deferred to runbook. Commit `feat(app): low-level keyboard hook driving PTT`.

### Task B5: sidecar.rs — supervisor

- [ ] Implement: `spawn_sidecar(python, script, config_path)` launches the Python sidecar (hidden window via `CREATE_NO_WINDOW`), pipes stdin, reads stdout lines, parses JSON events → forwards to a channel (`State`/`Transcript`/`Error`). On exit, respawn with backoff `[1,2,4,8,10]`s (mirror the `VoiceController` description in the voice-ptt memory). `send(cmd)` writes a line to stdin. Unit-test the backoff schedule + JSON line parsing (feed sample lines).
- [ ] `cargo test` → PASS. Commit `feat(app): python sidecar supervisor`.

### Task B6: tray.rs + commands.rs + settings UI

- [ ] tray.rs: build a `TrayIcon` with menu (Settings, Dictionary, Pause/Resume, Quit) and a `set_state(state)` that swaps the icon/tooltip per `idle|recording|transcribing|error|loading`.
- [ ] commands.rs: Tauri commands `get_config`, `set_config`, `add_dict_term`, `remove_dict_term`, `set_keys` — all read/write `config.rs` and signal the supervisor to `reload`.
- [ ] ui/: a single settings window (provider dropdowns, key fields, hotkey + threshold, dictionary list editor) using `withGlobalTauri` `invoke`. Keep it clean and minimal (frontend-design skill for polish).
- [ ] **Verify:** `cargo build`; open settings window manually; round-trip a config change. Commit `feat(app): tray + settings commands + UI`.

### Task B7: main.rs — wire everything

- [ ] In `setup`: init logger, load config, build tray, spawn sidecar supervisor, spawn hotkey hook; pump: hotkey `Start/Stop` → `supervisor.send("start"/"stop")`; sidecar events → `tray.set_state` + (optional) notifications. Single-instance guard (named mutex, mirror glassbar). Hide from taskbar (no main window).
- [ ] **Verify:** `cargo tauri build` (or `cargo build`) succeeds; launch; confirm tray appears, sidecar boots to `idle`. Commit `feat(app): wire tray + hotkey + sidecar`.

### Task B8: packaging + end-to-end runbook

- [ ] Freeze the sidecar with PyInstaller (`--onefile --noconsole main.py` → `murmur-sidecar.exe`), add to Tauri `resources`, point the supervisor at the bundled exe (fallback to system `python main.py` in dev). Document in RUNBOOK.
- [ ] **Full manual E2E:** launch murmur; hold Shift alone → speak → release; confirm formatted text typed into Gmail/Slack/VS Code/Notepad; verify capitalization (quick Shift+letter) does NOT trigger; kill network → offline local path still types raw transcript. Record in RUNBOOK.
- [ ] Commit `chore(app): freeze sidecar + e2e runbook`.

---

## Self-review

**Spec coverage:** §3 architecture → B5/B7; §4.1 hotkey → B3/B4; §4.2 modules → A2–A8; §5 faithful cleanup → A6; §6 data flow → A8; §7 config → A1/B2; §8 IPC → A2/A8/B5; §9 testing → tests in every task; §10 error handling → fallback in A5/A6, backoff in B5; §11 privacy → keys in config (gitignored), no password-field reads (P2 only); §12 cost → n/a; §13 P1 scope → all tasks; §14 risks → noted. No gaps.

**Placeholder scan:** No TBD/TODO; test code + commands provided per task. (Some "lift from voice_ptt.py:NN" references point at concrete existing code being reused — acceptable, not placeholders.)

**Type consistency:** `transcribe(audio, sr, prompt)`, `complete(system, user)`, `classify(exe, title)`, `format_text(formatter, raw, profile, terms)`, PTT `Action::{StartRecording,StopRecording}` used consistently across tasks.

**Execution:** Inline (user away). Front-load Part A (fully verifiable offline), then Part B (compile + logic tests + runbook).
