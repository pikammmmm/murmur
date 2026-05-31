# murmur sidecar — runbook

## Run standalone (dev)

```
cd sidecar
.\.venv\Scripts\python.exe main.py
```

Type one command per line on stdin: `start`, `stop`, `toggle`, `reload`, `quit`.
Watch JSON events on stdout; diagnostic logs go to stderr.

With **no API keys**, the sidecar uses local faster-whisper (fully offline) and
the formatter is a passthrough (you get the raw transcript). Add keys to the
config file — or set `GROQ_API_KEY` / `ANTHROPIC_API_KEY` in the environment — to
enable cloud STT and AI cleanup. The formatter always falls through to the raw
transcript on any error, so dictation never loses your words.

## Config path

`%MURMUR_CONFIG%` if set, otherwise `%APPDATA%\murmur\murmur\data\config.json`
(written by the Rust shell). See the spec §7 for the schema.

## Tests

```
.\.venv\Scripts\python.exe -m pytest -q              # 50 fast unit tests
.\.venv\Scripts\python.exe -m pytest -q -m slow      # real STT + process boot (downloads the base model)
```

The slow suite includes a genuine end-to-end check: Windows SAPI synthesizes a
known sentence, faster-whisper transcribes it, and we assert the words come back.

## Manual microphone smoke (needs a real mic)

1. Focus Notepad (or any text field).
2. Run the sidecar; wait for `{"type":"state","state":"idle"}`.
3. Type `start`, speak, type `stop`.
4. Expect `recording` → `transcribing` → `idle`, and the cleaned text typed in.

Note: this machine's default input is "Microphone (Voicemod)" — a virtual device
that needs Voicemod running to pass real input. Switch the Windows default input
if transcripts come back empty.
