import json

from murmur_sidecar.config import DEFAULTS, load_config, resolve_keys


def test_defaults_used_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "nope.json")
    assert cfg["stt"]["provider"] == "groq"
    assert cfg["formatter"]["provider"] == "anthropic"
    assert cfg["hotkey"]["hold_threshold_ms"] == 350
    assert cfg["inject_mode"] == "paste"


def test_file_overrides_and_backfills(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"stt": {"provider": "local"}}))
    cfg = load_config(p)
    assert cfg["stt"]["provider"] == "local"           # override kept
    assert cfg["stt"]["language"] == "en"              # sibling default backfilled
    assert cfg["formatter"]["provider"] == "anthropic"  # untouched default


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    cfg = load_config(p)
    assert cfg == DEFAULTS


def test_resolve_keys_prefers_config_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env")
    cfg = load_config(tmp_path / "nope.json")
    cfg["keys"]["groq"] = "from-config"
    keys = resolve_keys(cfg)
    assert keys["groq"] == "from-config"     # config wins
    assert keys["anthropic"] == "anthropic-env"  # env fills the gap
