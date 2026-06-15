from murmur_sidecar.config import load_config
from murmur_sidecar.stt.base import make_transcriber, transcribe_with_fallback


class FakeOK:
    def transcribe(self, audio, sr, prompt):
        return "hello world"


class FakeFail:
    def transcribe(self, audio, sr, prompt):
        raise RuntimeError("net down")


def test_primary_used():
    assert transcribe_with_fallback(FakeOK(), FakeOK(), b"", 16000, "") == ("hello world", False)


def test_falls_back_on_primary_error():
    # primary raised -> fallback served it -> used_fallback True (cloud "ran out")
    assert transcribe_with_fallback(FakeFail(), FakeOK(), b"", 16000, "") == ("hello world", True)


def test_both_fail_returns_empty():
    assert transcribe_with_fallback(FakeFail(), FakeFail(), b"", 16000, "") == ("", True)


def _cfg(tmp_path):
    return load_config(tmp_path / "nope.json")  # defaults: provider=groq


def test_cloud_without_key_falls_back_to_local(tmp_path):
    primary, fallback = make_transcriber(_cfg(tmp_path), {})  # no keys
    assert type(primary).__name__ == "LocalTranscriber"
    assert fallback is None


def test_local_provider_has_no_fallback(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["stt"]["provider"] = "local"
    primary, fallback = make_transcriber(cfg, {})
    assert type(primary).__name__ == "LocalTranscriber"
    assert fallback is None


def test_groq_with_key_gets_local_fallback(tmp_path):
    primary, fallback = make_transcriber(_cfg(tmp_path), {"groq": "k"})
    assert type(primary).__name__ == "GroqTranscriber"
    assert type(fallback).__name__ == "LocalTranscriber"


def test_accuracy_mode_selects_openai(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["stt"]["accuracy_mode"] = True
    primary, fallback = make_transcriber(cfg, {"openai": "k"})
    assert type(primary).__name__ == "OpenAITranscriber"
    assert type(fallback).__name__ == "LocalTranscriber"
