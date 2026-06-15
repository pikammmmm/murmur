"""Startup warm-up planning: which targets block 'idle' vs warm in the background.

A local/GPU *primary* must be warmed synchronously — the first dictation needs
it and there's no fast cloud path, so blocking until idle is correct. A cloud
primary's local *fallback* is warmed in the background instead: the first
dictation goes to the cloud, so the user must not wait on a CPU model load
before they can dictate. The microphone is always warmed synchronously.
"""
import threading

from murmur_sidecar import warmup


class Warmable:
    def __init__(self, name, block=None):
        self.name = name
        self.warmed = False
        self._block = block

    def warm(self):
        if self._block is not None:
            self._block.wait(2)
        self.warmed = True


class NoWarm:
    """A transcriber with no warm() — e.g. a cloud provider."""


class FakeApp:
    def __init__(self, transcriber=None, fallback=None, recorder=None):
        self.transcriber = transcriber
        self.fallback = fallback
        self.recorder = recorder


def test_local_primary_warms_synchronously():
    primary = Warmable("local")
    rec = Warmable("mic")
    app = FakeApp(transcriber=primary, fallback=None, recorder=rec)
    sync, background = warmup.plan(app)
    assert sync == [primary, rec]
    assert background == []


def test_cloud_primary_warms_fallback_in_background():
    fallback = Warmable("local-fallback")
    rec = Warmable("mic")
    app = FakeApp(transcriber=NoWarm(), fallback=fallback, recorder=rec)
    sync, background = warmup.plan(app)
    # The cloud primary (no warm) must NOT drag a CPU model load into the
    # blocking path; only the mic is synchronous, the fallback is backgrounded.
    assert sync == [rec]
    assert background == [fallback]


def test_no_fallback_only_warms_the_mic():
    rec = Warmable("mic")
    app = FakeApp(transcriber=NoWarm(), fallback=None, recorder=rec)
    sync, background = warmup.plan(app)
    assert sync == [rec]
    assert background == []


def test_run_warms_sync_targets_inline():
    primary = Warmable("local")
    rec = Warmable("mic")
    app = FakeApp(transcriber=primary, fallback=None, recorder=rec)
    warmup.run(app)
    # Synchronous targets are guaranteed warm the instant run() returns.
    assert primary.warmed
    assert rec.warmed


def test_run_backgrounds_the_fallback_off_the_calling_thread():
    gate = threading.Event()
    fallback = Warmable("local-fallback", block=gate)
    rec = Warmable("mic")
    app = FakeApp(transcriber=NoWarm(), fallback=fallback, recorder=rec)
    th = warmup.run(app)
    # The mic warmed inline; the fallback is still blocked on the gate, proving
    # run() returned WITHOUT waiting on the model load.
    assert rec.warmed
    assert not fallback.warmed
    gate.set()
    th.join(2)
    assert fallback.warmed


def test_run_does_not_raise_when_a_warm_fails():
    class Boom:
        def warm(self):
            raise RuntimeError("model load failed")

    app = FakeApp(transcriber=Boom(), fallback=None, recorder=Warmable("mic"))
    warmup.run(app)  # must swallow the failure, never crash startup
