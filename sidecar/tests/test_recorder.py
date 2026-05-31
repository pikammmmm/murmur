import numpy as np

from murmur_sidecar.recorder import Recorder


class FakeStream:
    """Synchronously feeds two chunks through the callback on start()."""

    def __init__(self, sr, cb):
        self.cb = cb

    def start(self):
        self.cb(np.ones((100, 1), dtype=np.float32), 100, None, None)
        self.cb(np.full((50, 1), 0.5, dtype=np.float32), 50, None, None)

    def stop(self):
        pass

    def close(self):
        pass


def test_records_and_concatenates():
    r = Recorder(stream_factory=lambda sr, cb: FakeStream(sr, cb))
    r.start()
    audio = r.stop()
    assert audio.shape[0] == 150
    assert audio.dtype == np.float32


def test_stop_without_start_returns_empty():
    r = Recorder(stream_factory=lambda sr, cb: None)
    audio = r.stop()
    assert audio.shape[0] == 0
    assert audio.dtype == np.float32


class FailingStream:
    def __init__(self, sr, cb):
        self.closed = False

    def start(self):
        raise RuntimeError("mic busy")

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_start_failure_closes_stream_and_leaves_none():
    import pytest

    streams = []

    def factory(sr, cb):
        s = FailingStream(sr, cb)
        streams.append(s)
        return s

    r = Recorder(stream_factory=factory)
    with pytest.raises(RuntimeError):
        r.start()
    assert streams[0].closed is True   # the unstarted stream was cleaned up
    assert r._stream is None           # not retained
    assert r.stop().shape[0] == 0      # subsequent stop is safe
