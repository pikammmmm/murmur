"""Startup warm-up: pay slow first-use costs up front, without making a cloud
user wait on a CPU model they may never touch.

``plan(app)`` splits the warm-able pieces into *synchronous* (warm before the
sidecar reports ``idle``) and *background* (warm off-thread, after idle):

  * A local/GPU **primary** transcriber → synchronous. The first dictation needs
    it loaded and there's no faster cloud path, so blocking until idle is right.
  * A cloud primary's local **fallback** → background. The first dictation goes
    to the cloud; loading a CPU Whisper model before the user can dictate would
    add seconds of dead time at every launch for a model that's only a rarely-hit
    safety net. Warm it in the background so it's ready *if* the cloud fails,
    without gating readiness on it.
  * The **microphone** → always synchronous. Its first cold open lags by seconds
    and would clip the leading audio of the first recording regardless of which
    STT provider is in use; it's cheap to warm and must be ready before idle.

``run(app)`` executes that plan: synchronous targets inline, background targets
on a daemon thread (returned so callers can join it in tests). Every individual
``warm()`` is guarded — a missing mic or a failed model load must never crash
startup.
"""
import logging
import threading

log = logging.getLogger("murmur.warmup")


def plan(app):
    """Return ``(sync, background)`` lists of warm-able targets for ``app``."""
    sync, background = [], []
    primary = getattr(app, "transcriber", None)
    fallback = getattr(app, "fallback", None)
    if primary is not None and hasattr(primary, "warm"):
        sync.append(primary)                 # local/gpu primary: block, it's needed now
    elif fallback is not None and hasattr(fallback, "warm"):
        background.append(fallback)          # cloud primary: warm the fallback off-thread
    recorder = getattr(app, "recorder", None)
    if recorder is not None and hasattr(recorder, "warm"):
        sync.append(recorder)
    return sync, background


def _warm_one(target):
    try:
        target.warm()
    except Exception as exc:
        log.warning("warm failed for %r: %s", target, exc)


def run(app):
    """Warm sync targets inline, background targets on a daemon thread.

    Returns the background thread (or ``None`` if there's nothing to background)
    so a caller can join it; nothing in the app relies on the return value."""
    sync, background = plan(app)
    for target in sync:
        _warm_one(target)
    if not background:
        return None

    def _bg():
        for target in background:
            _warm_one(target)

    thread = threading.Thread(target=_bg, daemon=True, name="murmur-warm")
    thread.start()
    return thread
