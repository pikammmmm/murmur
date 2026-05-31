import numpy as np

from murmur_sidecar.app import App


class Rec:
    def __init__(self, audio):
        self.audio = audio

    def start(self):
        pass

    def stop(self):
        return self.audio


class FixedT:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, sr, prompt):
        return self.text


class Passthrough:
    """Formatter that returns the (already-corrected) text unchanged, so the
    test asserts on the correction layer alone."""

    def complete(self, system, user):
        return user


def build(entries, raw, tmp_path, format_mode="faithful"):
    typed = []
    app = App(
        recorder=Rec(np.ones(10, dtype=np.float32)),
        transcriber=FixedT(raw),
        fallback=None,
        formatter=Passthrough(),
        type_text=lambda x: typed.append(x),
        detect=lambda: ("generic", "", ""),
        entries=entries,
        corrections_path=str(tmp_path / "corrections.json"),
        format_mode=format_mode,
        max_seconds=0,
        emit_state=lambda s: None,
        emit_transcript=lambda x: None,
        emit_error=lambda m: None,
        use_threads=False,
    )
    return app, typed


def test_grammar_mode_applies_offline_rule_pass(tmp_path):
    # provider 'off' (Passthrough) => no LLM, but grammar mode still runs the
    # offline rule pass before formatting.
    app, typed = build([], "he don't know", tmp_path, format_mode="grammar")
    app.start()
    app.stop()
    assert typed[-1] == "he doesn't know"


def test_faithful_mode_leaves_grammar_alone(tmp_path):
    app, typed = build([], "he don't know", tmp_path, format_mode="faithful")
    app.start()
    app.stop()
    assert typed[-1] == "he don't know"  # verbatim — no grammar change offline


def test_dictation_recorded_to_history_and_stats(tmp_path):
    from murmur_sidecar import history as H

    typed = []
    app = App(
        recorder=Rec(np.ones(10, dtype=np.float32)),
        transcriber=FixedT("hello world"),
        fallback=None,
        formatter=Passthrough(),
        type_text=lambda x: typed.append(x),
        detect=lambda: ("generic", "", ""),
        entries=[],
        corrections_path=str(tmp_path / "c.json"),
        save_history=True,
        history_path=str(tmp_path / "h.jsonl"),
        stats_path=str(tmp_path / "s.json"),
        max_seconds=0,
        emit_state=lambda s: None,
        emit_transcript=lambda x: None,
        emit_error=lambda m: None,
        use_threads=False,
    )
    app.start()
    app.stop()
    items = H.load_history(tmp_path / "h.jsonl")
    assert len(items) == 1 and items[0]["text"] == "hello world" and items[0]["words"] == 2
    assert H.load_stats(tmp_path / "s.json")["words"] == 2

    app.clear_history()
    assert H.load_history(tmp_path / "h.jsonl") == []


def test_learned_exact_correction_applies_to_dictation(tmp_path):
    app, typed = build(
        [{"wrong": "glass bar", "right": "glassbar", "count": 3, "source": "learned"}],
        "open glass bar",
        tmp_path,
    )
    app.start()
    app.stop()
    assert typed == ["open glassbar"]


def test_teach_then_next_dictation_is_corrected(tmp_path):
    app, typed = build([], "open glass bar", tmp_path)
    app.start()
    app.stop()
    assert typed[-1] == "open glass bar"  # nothing learned yet

    pairs = app.learn("open glassbar")  # user fixes the last dictation
    assert ("glass bar", "glassbar") in pairs

    app.start()
    app.stop()
    assert typed[-1] == "open glassbar"  # the fix now applies automatically


def test_manual_add_and_remove_correction(tmp_path):
    app, typed = build([], "hey pika", tmp_path)
    app.add_correction("pika", "Pikammmmm")
    app.start()
    app.stop()
    assert typed[-1] == "hey Pikammmmm"

    app.remove_correction("pika")
    app.start()
    app.stop()
    assert typed[-1] == "hey pika"


def test_learn_persists_to_disk(tmp_path):
    app, _ = build([], "call me pika", tmp_path)
    app.start()
    app.stop()
    app.learn("call me Pikammmmm")
    # a fresh app loads what was persisted
    from murmur_sidecar.corrections import load_store
    entries = load_store(tmp_path / "corrections.json")
    assert any(e["wrong"] == "pika" and e["right"] == "Pikammmmm" for e in entries)


def test_learn_with_no_prior_dictation_is_noop(tmp_path):
    app, _ = build([], "whatever", tmp_path)
    assert app.learn("anything") == []  # last_raw empty -> nothing to diff


def test_stdin_loop_parses_correction_commands():
    import io

    from murmur_sidecar.app import stdin_command_loop

    class FakeApp:
        def __init__(self):
            self.calls = []

        def add_correction(self, w, r):
            self.calls.append(("add", w, r))

        def remove_correction(self, w):
            self.calls.append(("del", w))

        def learn(self, t):
            self.calls.append(("learn", t))

    app = FakeApp()
    # tab between wrong/right; arg case + spaces must be preserved
    stream = io.StringIO("correctadd glass bar\tglassbar\ncorrectdel pika\nlearn Open GlassBar now\nquit\n")
    stdin_command_loop(app, stream=stream)
    assert ("add", "glass bar", "glassbar") in app.calls
    assert ("del", "pika") in app.calls
    assert ("learn", "Open GlassBar now") in app.calls
