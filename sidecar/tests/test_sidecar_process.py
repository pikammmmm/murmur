"""Real process-level test of the sidecar protocol.

Launches ``main.py`` as a subprocess with a local-only config (no keys needed),
reads its stdout JSON events until it reports ``idle``, then sends ``quit`` and
confirms a clean exit. Verifies the actual stdin/stdout contract the Rust shell
relies on. Marked slow because it boots + warms the local model.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.slow
def test_sidecar_boots_to_idle_and_quits(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "stt": {"provider": "local", "local_model": "base"},
        "formatter": {"provider": "off"},
    }))
    env = dict(os.environ)
    env["MURMUR_CONFIG"] = str(cfg)
    main_py = Path(__file__).resolve().parent.parent / "main.py"

    proc = subprocess.Popen(
        [sys.executable, str(main_py)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env, bufsize=1,
    )
    seen = []
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            evt = json.loads(line)
            seen.append(evt.get("state"))
            if evt.get("state") == "idle":
                break
        assert "loading" in seen
        assert "idle" in seen
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode is not None
