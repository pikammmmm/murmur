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


def build(entries, raw, tmp_path):
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
        max_seconds=0,
        emit_state=lambda s: None,
        emit_transcript=lambda x: None,
        emit_error=lambda m: None,
        use_threads=False,
    )
    return app, typed


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
