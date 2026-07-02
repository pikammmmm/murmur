"""One-shot smoke test for the frozen sidecar: boot it, wait for a ready state
event on stdout, send quit, report. Run: python smoke_frozen.py [exe]"""
import subprocess
import sys
import threading
import time

exe = sys.argv[1] if len(sys.argv) > 1 else r"dist\murmur-sidecar.exe"
proc = subprocess.Popen(
    [exe],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8",
)

lines = []
ready = threading.Event()

def pump():
    for line in proc.stdout:
        line = line.strip()
        if line:
            lines.append(line)
            print("EVENT:", line, flush=True)
        if '"idle"' in line or '"ready"' in line:
            ready.set()

t = threading.Thread(target=pump, daemon=True)
t.start()

ok = ready.wait(timeout=90)
try:
    proc.stdin.write("quit\n")
    proc.stdin.flush()
except OSError:
    pass
try:
    code = proc.wait(timeout=15)
except subprocess.TimeoutExpired:
    proc.kill()
    code = "KILLED"

print("READY:", ok)
print("EXIT:", code)
print("EVENTS_SEEN:", len(lines))
sys.exit(0 if ok and code == 0 else 1)
