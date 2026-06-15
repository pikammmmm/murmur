import io

import numpy as np

from murmur_sidecar.app import App, stdin_command_loop


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio

    def start(self):
        pass

    def stop(self):
        return self.audio


class FixedTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, sr, prompt):
        return self.text


class FmtEcho:
    def __init__(self, out):
        self.out = out

    def complete(self, system, user):
        return self.out


class FailTranscriber:
    def transcribe(self, audio, sr, prompt):
        raise RuntimeError("cloud STT down")


class FailFormatter:
    def complete(self, system, user):
        raise RuntimeError("cloud key out of credits")


def make_app(audio, raw="hello world", formatted="Hello, world.", profile="generic",
             stt_cloud=False, formatter_cloud=False):
    rec = {"states": [], "transcripts": [], "errors": [], "typed": [], "engine": []}
    app = App(
        recorder=FakeRecorder(audio),
        transcriber=FixedTranscriber(raw),
        fallback=None,
        formatter=FmtEcho(formatted),
        type_text=lambda t: rec["typed"].append(t),
        detect=lambda: (profile, "x.exe", "title"),
        dict_terms=[],
        max_seconds=0,
        stt_cloud=stt_cloud,
        formatter_cloud=formatter_cloud,
        emit_state=lambda s: rec["states"].append(s),
        emit_transcript=lambda t: rec["transcripts"].append(t),
        emit_error=lambda m: rec["errors"].append(m),
        emit_engine=lambda v: rec["engine"].append(v),
        use_threads=False,
    )
    return app, rec


def test_record_then_stop_runs_full_pipeline():
    app, rec = make_app(np.ones(1600, dtype=np.float32))
    app.start()
    assert rec["states"] == ["recording"]
    app.stop()
    assert rec["states"] == ["recording", "transcribing", "idle"]
    assert rec["typed"] == ["Hello, world."]
    assert rec["transcripts"] == ["Hello, world."]
    assert rec["errors"] == []


def test_empty_audio_returns_to_idle_without_injecting():
    app, rec = make_app(np.zeros(0, dtype=np.float32))
    app.start()
    app.stop()
    assert rec["states"] == ["recording", "transcribing", "idle"]
    assert rec["typed"] == []
    assert rec["transcripts"] == []


def test_start_while_recording_is_ignored():
    app, rec = make_app(np.ones(10, dtype=np.float32))
    app.start()
    app.start()
    assert rec["states"] == ["recording"]


def test_toggle_flips_record_and_stop():
    app, rec = make_app(np.ones(10, dtype=np.float32))
    app.toggle()  # -> start
    assert rec["states"] == ["recording"]
    app.toggle()  # -> stop -> process
    assert rec["states"][-1] == "idle"
    assert rec["typed"] == ["Hello, world."]


def test_engine_orange_when_cloud_path_succeeds():
    app, rec = make_app(np.ones(1600, dtype=np.float32), stt_cloud=True, formatter_cloud=True)
    app.start()
    app.stop()
    assert rec["engine"][-1] == "cloud"


def test_engine_white_when_cloud_formatter_key_runs_out():
    # cloud STT works but the Claude/cloud cleanup call raises (key out of credits)
    # -> the overlay must drop to white, not keep lying orange.
    app, rec = make_app(np.ones(1600, dtype=np.float32), stt_cloud=True, formatter_cloud=True)
    app.formatter = FailFormatter()
    app.start()
    app.stop()
    assert rec["engine"][-1] == "local"
    assert rec["typed"] == ["hello world"]  # still types the raw transcript


def test_engine_white_when_cloud_stt_falls_back():
    app, rec = make_app(np.ones(1600, dtype=np.float32), stt_cloud=True, formatter_cloud=False)
    app.transcriber = FailTranscriber()
    app.fallback = FixedTranscriber("local words")
    app.start()
    app.stop()
    assert rec["engine"][-1] == "local"


def test_engine_white_for_on_device_stt():
    app, rec = make_app(np.ones(1600, dtype=np.float32))  # stt_cloud=False (local)
    app.start()
    app.stop()
    assert rec["engine"][-1] == "local"


def test_stdin_loop_dispatches_until_quit():
    class FakeApp:
        def __init__(self):
            self.calls = []

        def start(self):
            self.calls.append("start")

        def stop(self):
            self.calls.append("stop")

        def toggle(self):
            self.calls.append("toggle")

    app = FakeApp()
    stdin_command_loop(app, stream=io.StringIO("start\n\nstop\ntoggle\nquit\nstart\n"))
    assert app.calls == ["start", "stop", "toggle"]  # blank line skipped, post-quit ignored


def test_stdin_loop_survives_a_failing_command():
    class App2:
        def __init__(self):
            self.calls = []

        def stop(self):
            raise RuntimeError("boom")  # a command that raises

        def start(self):
            self.calls.append("start")

    app = App2()
    # "stop" raises but must not kill the loop; "start" after it still runs.
    stdin_command_loop(app, stream=io.StringIO("stop\nstart\nquit\n"))
    assert app.calls == ["start"]
