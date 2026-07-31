"""Real end-to-end test of the local STT path.

Synthesizes a known sentence to a 16 kHz mono WAV with whatever TTS engine the
host offers (Windows SAPI, or espeak-ng/pico2wave/flite on Linux), then runs it
through the actual LocalTranscriber (faster-whisper) and asserts the key words
come back. No microphone, network, or API key required — this verifies the
offline pipeline genuinely works. Marked slow because it downloads the base model.

Skips rather than fails when the host has no TTS engine: the absence of a voice
synthesizer says nothing about whether murmur's offline pipeline works.
"""
import shutil
import subprocess
import wave

import numpy as np
import pytest

from murmur_sidecar.stt.local import LocalTranscriber

SENTENCE = "the quick brown fox jumps over the lazy dog"


def _sapi_command(path):
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
    return ["powershell", "-NoProfile", "-Command", ps]


#: (tool that must exist, argv builder) in preference order.
_TTS_ENGINES = [
    ("powershell", _sapi_command),
    ("espeak-ng", lambda p: ["espeak-ng", "-s", "140", "-w", str(p), SENTENCE]),
    ("espeak", lambda p: ["espeak", "-s", "140", "-w", str(p), SENTENCE]),
    ("pico2wave", lambda p: ["pico2wave", "-w", str(p), SENTENCE]),
    ("flite", lambda p: ["flite", "-t", SENTENCE, "-o", str(p)]),
]


def _synthesize(path):
    for tool, build in _TTS_ENGINES:
        if shutil.which(tool):
            subprocess.run(build(path), check=True)
            return tool
    pytest.skip("no TTS engine available to synthesize test audio "
                "(tried: %s)" % ", ".join(t for t, _ in _TTS_ENGINES))


def _load_wav_mono_f32(path):
    """Return (samples, sample_rate). Engines differ on rate — espeak-ng writes
    22.05 kHz, SAPI is told 16 kHz — so read it rather than assuming."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


@pytest.mark.slow
def test_local_transcribes_synthesized_speech(tmp_path):
    wav = tmp_path / "speech.wav"
    _synthesize(wav)
    assert wav.exists() and wav.stat().st_size > 1000

    audio, rate = _load_wav_mono_f32(wav)
    text = LocalTranscriber("base", "en").transcribe(audio, rate, "")
    low = text.lower()
    # The base model should nail this pangram's content words.
    assert "quick" in low
    assert "fox" in low
    assert "dog" in low
