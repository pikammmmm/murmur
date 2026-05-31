"""The orchestrator + state machine.

Drives one dictation: record -> (on stop) detect context -> transcribe (cloud,
local fallback) -> faithful-cleanup format -> type into the focused field, while
emitting state/transcript/error events. Collaborators are injected so the whole
pipeline is unit-testable without a mic, network, or models. ``build_app`` wires
the real ones from config; ``stdin_command_loop`` is the command channel.
"""
import logging
import threading

from . import events
from .dictionary import build_stt_prompt
from .formatter.base import format_text
from .stt.base import transcribe_with_fallback

log = logging.getLogger("murmur.app")


class App:
    def __init__(
        self, recorder, transcriber, fallback, formatter, type_text, detect,
        dict_terms=None, sample_rate=16000, max_seconds=60,
        emit_state=None, emit_transcript=None, emit_error=None, use_threads=True,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.fallback = fallback
        self.formatter = formatter
        self.type_text = type_text
        self.detect = detect
        self.dict_terms = dict_terms or []
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self._emit_state = emit_state or events.state
        self._emit_transcript = emit_transcript or events.transcript
        self._emit_error = emit_error or events.error
        self.use_threads = use_threads
        self._lock = threading.Lock()
        self._recording = False
        self._transcribing = False
        self._max_timer = None

    # --- commands ---------------------------------------------------------
    def start(self):
        with self._lock:
            if self._recording or self._transcribing:
                return  # already recording, or a previous clip is still processing
            self._recording = True
        try:
            self.recorder.start()
        except Exception as exc:
            with self._lock:
                self._recording = False
            self._emit_error(f"mic open failed: {exc}")
            self._emit_state("idle")
            return
        self._arm_max_timer()
        self._emit_state("recording")

    def stop(self):
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            self._transcribing = True
            if self._max_timer is not None:
                self._max_timer.cancel()
                self._max_timer = None
        if self.use_threads:
            threading.Thread(target=self._process, daemon=True).start()
        else:
            self._process()

    def toggle(self):
        with self._lock:
            recording, transcribing = self._recording, self._transcribing
        if transcribing:
            return  # ignore toggles while a clip is still being processed
        if recording:
            self.stop()
        else:
            self.start()

    def apply_config(self, cfg, keys):
        """Rebuild providers + settings when the Rust shell signals 'reload'."""
        from .formatter.base import make_formatter
        from .stt.base import make_transcriber
        self.transcriber, self.fallback = make_transcriber(cfg, keys)
        self.formatter = make_formatter(cfg, keys)
        self.dict_terms = cfg.get("dictionary", [])
        self.max_seconds = cfg.get("max_recording_seconds", 60)

    # --- internals --------------------------------------------------------
    def _arm_max_timer(self):
        if self.max_seconds and self.use_threads:
            self._max_timer = threading.Timer(self.max_seconds, self.stop)
            self._max_timer.daemon = True
            self._max_timer.start()

    def _process(self):
        try:
            self._emit_state("transcribing")
            audio = self.recorder.stop()
            if audio is None or len(audio) == 0:
                return
            profile, _exe, _title = self.detect()
            prompt = build_stt_prompt(self.dict_terms)
            raw = transcribe_with_fallback(self.transcriber, self.fallback, audio, self.sample_rate, prompt)
            if not raw:
                return
            text = format_text(self.formatter, raw, profile, self.dict_terms)
            if text:
                try:
                    self.type_text(text)
                except Exception as exc:
                    self._emit_error(f"type failed: {exc}")
                self._emit_transcript(text)
        except Exception as exc:
            log.exception("processing failed")
            self._emit_error(f"processing failed: {exc}")
        finally:
            with self._lock:
                self._transcribing = False
            self._emit_state("idle")


def build_app(cfg, keys, **overrides):
    """Wire a real App from config + resolved keys (overrides for testing)."""
    from . import context
    from .formatter.base import make_formatter
    from .injector import type_text
    from .recorder import Recorder
    from .stt.base import make_transcriber

    transcriber, fallback = make_transcriber(cfg, keys)
    formatter = make_formatter(cfg, keys)
    return App(
        recorder=overrides.get("recorder") or Recorder(),
        transcriber=transcriber,
        fallback=fallback,
        formatter=formatter,
        type_text=overrides.get("type_text") or type_text,
        detect=overrides.get("detect") or context.detect,
        dict_terms=cfg.get("dictionary", []),
        max_seconds=cfg.get("max_recording_seconds", 60),
    )


def stdin_command_loop(app, stream=None, on_reload=None):
    """Dispatch one command per stdin line. Returns on 'quit' or EOF."""
    import sys
    stream = stream if stream is not None else sys.stdin
    for line in stream:
        cmd = line.strip().lower()
        if not cmd:
            continue
        if cmd == "start":
            app.start()
        elif cmd == "stop":
            app.stop()
        elif cmd == "toggle":
            app.toggle()
        elif cmd == "reload":
            if on_reload is not None:
                on_reload()
        elif cmd == "quit":
            break
        else:
            log.warning("unknown command: %r", cmd)
