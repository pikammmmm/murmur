import io
import json
import threading

from murmur_sidecar import events


def test_emit_one_json_line():
    buf = io.StringIO()
    events.emit({"type": "state", "state": "idle"}, buf)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"type": "state", "state": "idle"}


def test_helpers_emit_expected_shapes():
    buf = io.StringIO()
    events.state("recording", buf)
    events.transcript("héllo wörld", buf)  # unicode survives ensure_ascii=False
    events.error("boom", buf)
    objs = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert objs[0] == {"type": "state", "state": "recording"}
    assert objs[1] == {"type": "transcript", "text": "héllo wörld"}
    assert objs[2] == {"type": "error", "message": "boom"}


def test_emit_writes_utf8_bytes_through_buffer_for_non_ascii():
    # Mirrors a Windows pipe: the text layer is cp1252 and cannot encode č/š/ž,
    # so emit must go through the UTF-8 byte buffer the Rust shell decodes — not
    # the locale-bound text .write (which would raise and silently drop the event).
    class CP1252Stream:
        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, s):  # pragma: no cover - asserts the wrong path isn't taken
            s.encode("cp1252")  # would raise on Slovenian
            raise AssertionError("emit used the text path instead of the UTF-8 buffer")

        def flush(self):
            pass

    stream = CP1252Stream()
    events.transcript("želim računati čokolado", stream)
    decoded = stream.buffer.getvalue().decode("utf-8")
    assert json.loads(decoded) == {"type": "transcript", "text": "želim računati čokolado"}


def test_concurrent_emits_are_well_formed():
    buf = io.StringIO()

    def worker(i):
        for _ in range(10):
            events.emit({"type": "state", "state": f"s{i}"}, buf)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = buf.getvalue().splitlines()
    assert len(lines) == 50
    for line in lines:  # the lock guarantees no interleaved/garbled lines
        json.loads(line)
