"""murmur voice-dictation sidecar.

Spawned and driven by the Rust/Tauri shell. Reads one-line commands on stdin
(start, stop, toggle, reload, quit) and emits one JSON event per line on stdout
(state, transcript, error). The pipeline: audio capture -> context detection ->
speech-to-text (cloud primary, local fallback) -> faithful-cleanup formatting ->
type into the focused field.
"""

__version__ = "0.1.0"
