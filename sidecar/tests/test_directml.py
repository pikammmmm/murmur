"""The GPU (DirectML) transcriber wiring.

Construction is lazy (no torch import), so these run anywhere. The real GPU
inference test is guarded by importorskip so it only runs where torch-directml
is installed (e.g. the dev machine)."""
import sys

import numpy as np
import pytest

from murmur_sidecar.stt.base import make_transcriber
from murmur_sidecar.stt.directml import DirectMLTranscriber, _resample


def _cfg(provider="gpu", **stt):
    base = {"provider": provider, "language": "en", "beam_size": 5,
            "local_model": "small", "gpu_model": "large-v3"}
    base.update(stt)
    return {"stt": base}


def test_gpu_provider_builds_the_platform_gpu_backend_with_local_fallback():
    """The "gpu" provider is one config value with two implementations.

    DirectML on Windows, ROCm on Linux — faster-whisper's CTranslate2 backend is
    CUDA-only, so an AMD card gets nothing from it on either OS. Asserting
    DirectML unconditionally passed only because this suite used to run on
    Windows alone; on Linux it built ROCm and failed.
    """
    primary, fallback = make_transcriber(_cfg("gpu"), keys={})
    if sys.platform == "win32":
        assert isinstance(primary, DirectMLTranscriber)
    else:
        from murmur_sidecar.stt.rocm import RocmTranscriber
        assert isinstance(primary, RocmTranscriber)
    assert primary.model_size == "large-v3"
    assert primary.language == "en" and primary.beam_size == 5
    # CPU faster-whisper stays as automatic fallback so a GPU failure never
    # loses a dictation.
    assert type(fallback).__name__ == "LocalTranscriber"


def test_directml_construction_is_lazy():
    # No torch/model load on construct — _model is built on first transcribe.
    t = DirectMLTranscriber("large-v3", "en", 5)
    assert t._model is None


def test_resample_changes_length_and_is_noop_at_target():
    sig = np.linspace(-1, 1, num=8000, dtype="float32")
    assert _resample(sig, 16000) is sig                      # already 16k -> untouched
    out = _resample(sig, 8000)                               # 8k -> 16k doubles length
    assert abs(len(out) - 16000) <= 1
    assert out.dtype == np.float32


@pytest.mark.slow
def test_gpu_transcribes_synthesized_speech(tmp_path):
    pytest.importorskip("torch_directml")
    pytest.importorskip("whisper")
    import subprocess
    import wave

    sentence = "the quick brown fox jumps over the lazy dog"
    wav = tmp_path / "speech.wav"
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$fmt=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        "[System.Speech.AudioFormat.AudioChannel]::Mono);"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{wav}',$fmt);$s.Speak('{sentence}');$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    with wave.open(str(wav), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype("float32") / 32768.0

    # A small model keeps the test quick while still exercising the GPU path.
    text = DirectMLTranscriber("base", "en").transcribe(audio, 16000, "").lower()
    assert "quick" in text and "fox" in text and "dog" in text
