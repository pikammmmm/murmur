"""The orchestrator + state machine + learning.

Drives one dictation: record -> (on stop) detect context -> transcribe (cloud,
local fallback, biased toward the user's vocabulary) -> apply learned/phonetic
corrections -> faithful-cleanup format -> type into the focused field. Also owns
the learning surface: `learn` (diff the last raw transcript vs the user's fix),
plus manual add/remove of correction entries. Collaborators are injected so the
whole pipeline is unit-testable without a mic, network, or models.
"""
import logging
import threading

from . import cues, events
from .corrections import (
    Corrector,
    build_bias_string,
    build_bias_terms,
    learn_from_correction,
    save_store,
    upsert,
)
from .formatter.base import format_text
from .grammar import fix_grammar
from .stt.base import transcribe_with_fallback
from .voicecommands import apply_voice_commands

log = logging.getLogger("murmur.app")


class App:
    def __init__(
        self, recorder, transcriber, fallback, formatter, type_text, detect,
        dict_terms=None, entries=None, corrections_path=None, format_mode="faithful",
        voice_commands=True, audio_cues=True, sample_rate=16000, max_seconds=60,
        emit_state=None, emit_transcript=None, emit_error=None,
        emit_last_raw=None, emit_corrections=None, use_threads=True,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.fallback = fallback
        self.formatter = formatter
        self.type_text = type_text
        self.detect = detect
        self.dict_terms = dict_terms or []
        self.entries = entries or []
        self.corrections_path = corrections_path
        self.format_mode = format_mode
        self.voice_commands = voice_commands
        self.audio_cues = audio_cues
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self._emit_state = emit_state or events.state
        self._emit_transcript = emit_transcript or events.transcript
        self._emit_error = emit_error or events.error
        self._emit_last_raw = emit_last_raw
        self._emit_corrections = emit_corrections
        self.use_threads = use_threads
        self._lock = threading.Lock()
        self._recording = False
        self._transcribing = False
        self._max_timer = None
        self.last_raw = ""
        self._rebuild()

    def _rebuild(self):
        """Rebuild the corrector + STT bias string from dictionary + entries."""
        self.corrector = Corrector(self.dict_terms, self.entries)
        self.bias_prompt = build_bias_string(build_bias_terms(self.dict_terms, self.entries))

    # --- commands ---------------------------------------------------------
    def start(self):
        with self._lock:
            if self._recording or self._transcribing:
                return
            self._recording = True
        try:
            self.recorder.start()
        except Exception as exc:
            with self._lock:
                self._recording = False
            if self.audio_cues:
                cues.error()
            self._emit_error(f"mic open failed: {exc}")
            self._emit_state("idle")
            return
        self._arm_max_timer()
        if self.audio_cues:
            cues.record_start()
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
            return
        if recording:
            self.stop()
        else:
            self.start()

    def apply_config(self, cfg, keys):
        from .formatter.base import make_formatter
        from .injector import make_injector
        from .stt.base import make_transcriber
        self.transcriber, self.fallback = make_transcriber(cfg, keys)
        self.formatter = make_formatter(cfg, keys)
        self.type_text = make_injector(cfg.get("inject_mode", "type"))
        self.dict_terms = cfg.get("dictionary", [])
        self.format_mode = cfg.get("formatter", {}).get("mode", "faithful")
        self.voice_commands = cfg.get("voice_commands", True)
        self.audio_cues = cfg.get("audio_cues", True)
        self.max_seconds = cfg.get("max_recording_seconds", 60)
        self._rebuild()

    # --- learning ---------------------------------------------------------
    def _persist_and_refresh(self):
        if self.corrections_path:
            try:
                save_store(self.corrections_path, self.entries)
            except OSError as exc:
                log.warning("save corrections failed: %s", exc)
        self._rebuild()
        if self._emit_corrections:
            self._emit_corrections(self.entries)

    def learn(self, corrected_text):
        """Teach from a correction of the last dictation: diff the raw STT
        against the user's fix and persist the (heard -> intended) pairs."""
        if not corrected_text or not corrected_text.strip() or not self.last_raw:
            return []
        self.entries, pairs = learn_from_correction(self.entries, self.last_raw, corrected_text)
        self._persist_and_refresh()
        log.info("learned %d correction(s)", len(pairs))
        return pairs

    def add_correction(self, wrong, right):
        if wrong and wrong.strip() and right and right.strip():
            self.entries = upsert(self.entries, wrong, right, source="manual")
            self._persist_and_refresh()

    def remove_correction(self, wrong):
        key = (wrong or "").strip().lower()
        self.entries = [e for e in self.entries if e["wrong"].lower() != key]
        self._persist_and_refresh()

    def snapshot(self):
        """Emit the current correction list (called once at startup)."""
        if self._emit_corrections:
            self._emit_corrections(self.entries)

    # --- internals --------------------------------------------------------
    def _arm_max_timer(self):
        if self.max_seconds and self.use_threads:
            self._max_timer = threading.Timer(self.max_seconds, self.stop)
            self._max_timer.daemon = True
            self._max_timer.start()

    def _process(self):
        try:
            self._emit_state("transcribing")
            if self.audio_cues:
                cues.record_stop()
            audio = self.recorder.stop()
            if audio is None or len(audio) == 0:
                return
            profile, _exe, _title = self.detect()
            raw = transcribe_with_fallback(
                self.transcriber, self.fallback, audio, self.sample_rate, self.bias_prompt
            )
            if not raw:
                return
            self.last_raw = raw
            if self._emit_last_raw:
                self._emit_last_raw(raw)
            corrected = self.corrector.correct(raw) if self.corrector else raw
            if self.format_mode == "grammar":
                corrected = fix_grammar(corrected)
            if self.voice_commands:
                corrected = apply_voice_commands(corrected)
            text = format_text(self.formatter, corrected, profile, self.dict_terms, self.format_mode)
            if text:
                try:
                    self.type_text(text)
                except Exception as exc:
                    self._emit_error(f"type failed: {exc}")
                self._emit_transcript(text)
        except Exception as exc:
            log.exception("processing failed")
            if self.audio_cues:
                cues.error()
            self._emit_error(f"processing failed: {exc}")
        finally:
            with self._lock:
                self._transcribing = False
            self._emit_state("idle")


def build_app(cfg, keys, corrections_path=None, **overrides):
    """Wire a real App from config + resolved keys (overrides for testing)."""
    from . import context
    from .corrections import load_store
    from .formatter.base import make_formatter
    from .injector import make_injector
    from .recorder import Recorder
    from .stt.base import make_transcriber

    transcriber, fallback = make_transcriber(cfg, keys)
    formatter = make_formatter(cfg, keys)
    entries = load_store(corrections_path) if corrections_path else []
    return App(
        recorder=overrides.get("recorder") or Recorder(),
        transcriber=transcriber,
        fallback=fallback,
        formatter=formatter,
        type_text=overrides.get("type_text") or make_injector(cfg.get("inject_mode", "type")),
        detect=overrides.get("detect") or context.detect,
        dict_terms=cfg.get("dictionary", []),
        entries=entries,
        corrections_path=corrections_path,
        format_mode=cfg.get("formatter", {}).get("mode", "faithful"),
        voice_commands=cfg.get("voice_commands", True),
        audio_cues=cfg.get("audio_cues", True),
        max_seconds=cfg.get("max_recording_seconds", 60),
        emit_last_raw=events.last_raw,
        emit_corrections=events.corrections,
    )


def stdin_command_loop(app, stream=None, on_reload=None):
    """Dispatch one command per stdin line. The verb is lower-cased; the
    argument keeps its original case (transcripts/corrections are case-sensitive).
    Returns on 'quit' or EOF."""
    import sys
    stream = stream if stream is not None else sys.stdin
    for line in stream:
        line = line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        head, _, arg = line.partition(" ")
        verb = head.strip().lower()
        if verb == "start":
            app.start()
        elif verb == "stop":
            app.stop()
        elif verb == "toggle":
            app.toggle()
        elif verb == "learn":
            app.learn(arg)
        elif verb == "correctadd":  # arg: "<wrong>\t<right>"
            wrong, tab, right = arg.partition("\t")
            if tab:
                app.add_correction(wrong, right)
        elif verb == "correctdel":
            app.remove_correction(arg)
        elif verb == "snapshot":
            app.snapshot()
        elif verb == "reload":
            if on_reload is not None:
                on_reload()
        elif verb == "quit":
            break
        else:
            log.warning("unknown command: %r", verb)
