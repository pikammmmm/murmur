from murmur_sidecar.config import load_config
from murmur_sidecar.formatter.base import format_text, make_formatter


class Echo:
    """A fake formatter that records what it was asked and returns a fixed out."""

    def __init__(self, out):
        self.out = out
        self.seen = None

    def complete(self, system, user):
        self.seen = (system, user)
        return self.out


def test_returns_model_output():
    assert format_text(Echo("Hello, world."), "hello world", "generic", []) == "Hello, world."


def test_system_carries_profile_and_dictionary():
    f = Echo("x")
    format_text(f, "raw", "email", ["Rojo"])
    system, _ = f.seen
    assert "email" in system.lower()
    assert "Rojo" in system


def test_empty_input_short_circuits_without_calling_model():
    f = Echo("should-not-be-used")
    assert format_text(f, "   ", "generic", []) == ""
    assert f.seen is None


def test_formatter_error_falls_through_to_raw():
    class Boom:
        def complete(self, system, user):
            raise RuntimeError("api down")

    assert format_text(Boom(), "my raw words", "generic", []) == "my raw words"


def test_runaway_output_falls_through_to_raw():
    # Output far longer than input => model ignored the contract; keep raw.
    assert format_text(Echo("x" * 1000), "hi", "generic", []) == "hi"


def test_off_provider_passes_through(tmp_path):
    cfg = load_config(tmp_path / "n.json")
    cfg["formatter"]["provider"] = "off"
    f = make_formatter(cfg, {})
    assert format_text(f, "raw words here", "generic", []) == "raw words here"


def test_anthropic_without_key_is_passthrough(tmp_path):
    cfg = load_config(tmp_path / "n.json")  # default provider anthropic
    f = make_formatter(cfg, {})             # no key
    assert type(f).__name__ == "_Passthrough"


def test_anthropic_with_key_builds_formatter(tmp_path):
    cfg = load_config(tmp_path / "n.json")
    f = make_formatter(cfg, {"anthropic": "k"})
    assert type(f).__name__ == "AnthropicFormatter"
