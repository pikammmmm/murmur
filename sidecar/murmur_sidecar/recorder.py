"""Microphone capture -> float32 mono numpy array @ 16 kHz.

``stream_factory`` is injectable so tests can drive the callback with synthetic
audio without a real microphone. In production it opens a sounddevice InputStream
(imported lazily so the module loads without an audio backend present).
"""
import logging
import threading
import time

import numpy as np

log = logging.getLogger("murmur.recorder")

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self, sample_rate=SAMPLE_RATE, stream_factory=None):
        self.sample_rate = sample_rate
        self._stream_factory = stream_factory
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(np.asarray(indata).copy())

    def _default_factory(self, sr, callback):
        import sounddevice as sd
        return sd.InputStream(samplerate=sr, channels=1, dtype="float32", callback=callback)

    def start(self):
        with self._lock:
            self._chunks = []
        factory = self._stream_factory or self._default_factory
        stream = factory(self.sample_rate, self._callback)
        try:
            stream.start()
        except Exception:
            # Close the created-but-unstarted stream so it doesn't leak, then
            # re-raise for the caller to surface (mic error -> idle).
            try:
                stream.close()
            except Exception:
                pass
            raise
        self._stream = stream  # only retain the stream once it actually started

    def warm(self, settle=0.2):
        """Pre-open (and discard) a capture stream at startup so the FIRST real
        recording isn't truncated.

        PortAudio initializes lazily on the first stream in the process, and on a
        cold boot the audio endpoint's first open lags by seconds while the
        driver/device spins up. That latency would otherwise be paid on the
        user's first key-press — clipping the leading audio of their first
        dictation down to a word or two. Opening a throwaway stream here pays it
        up front. ``stop()`` clears the buffer, so no warm samples leak into the
        next real capture. Best-effort: a missing mic must not break startup."""
        try:
            self.start()
            if settle:
                time.sleep(settle)  # let the device actually begin streaming
            self.stop()
        except Exception as exc:
            log.warning("recorder warmup failed: %s", exc)

    def stop(self):
        """Stop capture and return the recorded audio (flattened float32)."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                log.warning("stream close error: %s", exc)
            finally:
                self._stream = None
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).flatten().astype(np.float32)
