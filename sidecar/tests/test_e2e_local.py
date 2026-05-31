"""Real end-to-end test of the local STT path.

Synthesizes a known sentence with Windows SAPI to a 16 kHz mono WAV, then runs
it through the actual LocalTranscriber (faster-whisper) and asserts the key
words come back. No microphone, network, or API key required — this verifies the
offline pipeline genuinely works. Marked slow because it downloads the base model.
"""
import subprocess
import wave

import numpy as np
import pytest

from murmur_sidecar.stt.local import LocalTranscriber

SENTENCE = "the quick brown fox jumps over the lazy dog"


def _synthesize(path):
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$fmt=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        "[System.Speech.AudioFormat.AudioChannel]::Mono);"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}',$fmt);"
        f"$s.Speak('{SENTENCE}');"
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def _load_wav_mono_f32(path):
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


@pytest.mark.slow
def test_local_transcribes_synthesized_speech(tmp_path):
    wav = tmp_path / "speech.wav"
    _synthesize(wav)
    assert wav.exists() and wav.stat().st_size > 1000

    audio = _load_wav_mono_f32(wav)
    text = LocalTranscriber("base", "en").transcribe(audio, 16000, "")
    low = text.lower()
    # The base model should nail this pangram's content words.
    assert "quick" in low
    assert "fox" in low
    assert "dog" in low
