"""Audio feedback cues.

Ascending beep when recording starts, descending when it stops, a low buzz on
error — the same affordances the original PTT tool had, so you know it's
listening without looking at the tray. Played on a daemon thread so they never
block the pipeline; silent on any failure (no audio device, etc.).

The tones are described as (frequency_hz, duration_ms) pairs and rendered by the
platform backend: winsound.Beep on Windows, synthesized PCM piped to the sound
server on Linux.
"""
import threading

from .backends import get_backend

START = [(600, 70), (900, 70)]   # ascending
STOP = [(900, 70), (600, 70)]    # descending
ERR = [(300, 250)]               # low buzz
CANCEL = [(500, 60), (350, 60)]  # quick low descending — dictation discarded


def _default_player(pairs):
    try:
        get_backend().beep(pairs)
    except Exception:
        pass


def play(pairs, player=None, sync=False):
    target = player or _default_player
    if sync:
        target(pairs)
    else:
        threading.Thread(target=lambda: target(pairs), daemon=True).start()


def record_start(player=None):
    play(START, player)


def record_stop(player=None):
    play(STOP, player)


def error(player=None):
    play(ERR, player)


def cancel(player=None):
    play(CANCEL, player)
