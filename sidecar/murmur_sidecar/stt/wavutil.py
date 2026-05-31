"""float32 mono numpy audio -> 16-bit PCM WAV bytes for cloud STT upload."""
import io
import wave

import numpy as np


def to_wav_bytes(audio, sr):
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(pcm)
    return buf.getvalue()
