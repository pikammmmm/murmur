"""The mic must be warmed at startup.

On a cold boot the audio device's FIRST open (PortAudio init + endpoint spin-up)
lags by seconds — so without a warm-up the leading audio of the user's first
dictation is dropped and the transcript is truncated to a word or two. Warming a
throwaway stream at startup pays that cost before the first real key-press.
"""
import numpy as np

from murmur_sidecar.recorder import Recorder


class FakeStream:
    def __init__(self):
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_warm_opens_starts_and_closes_a_stream():
    created = []

    def factory(sr, callback):
        s = FakeStream()
        created.append(s)
        return s

    Recorder(stream_factory=factory).warm(settle=0)
    assert len(created) == 1
    assert created[0].started and created[0].stopped and created[0].closed


def test_warm_is_best_effort_when_mic_open_fails():
    def factory(sr, callback):
        raise RuntimeError("no mic")

    Recorder(stream_factory=factory).warm(settle=0)  # must not raise


def test_warm_leaves_recorder_ready_for_a_real_capture():
    """Warming must not leave residual audio that pollutes the next recording."""
    box = {}

    def factory(sr, callback):
        box["cb"] = callback
        return FakeStream()

    rec = Recorder(stream_factory=factory)
    rec.warm(settle=0)
    rec.start()
    box["cb"](np.ones((100, 1), dtype="float32"), 100, None, None)
    audio = rec.stop()
    assert len(audio) == 100  # exactly the post-warm capture, no leftover warm samples
