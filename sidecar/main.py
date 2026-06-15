"""murmur sidecar entrypoint.

    python main.py            # uses %MURMUR_CONFIG% or the default data-dir path
    python main.py <config>   # explicit config path

Loads config, builds the pipeline, warms the local model, then runs the stdin
command loop. JSON events go to stdout; logs go to stderr (stdout is sacred).
"""
import logging
import os
import sys
from pathlib import Path

from murmur_sidecar import events, warmup
from murmur_sidecar.app import build_app, stdin_command_loop
from murmur_sidecar.config import load_config, resolve_keys

log = logging.getLogger("murmur.main")


def default_config_path():
    override = os.environ.get("MURMUR_CONFIG")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "murmur" / "murmur" / "data" / "config.json"
    return Path.home() / ".murmur" / "config.json"


def force_utf8_stdio():
    """The Rust shell speaks UTF-8 on both pipes. Windows would otherwise default
    our stdin to the locale code page (cp1252), so inbound 'learn'/'correctadd'
    text in Slovenian (č/š/ž) or any non-Latin-1 script would mis-decode or raise.
    (Stdout output goes through events.emit, which writes UTF-8 bytes directly.)"""
    for stream in (sys.stdin, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # never stdout — that's the event channel
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def attach_file_log(cfg_path):
    """Also log to <data-dir>/sidecar.log. The shell runs us with stderr
    discarded, so without this our diagnostics would vanish."""
    try:
        from logging.handlers import RotatingFileHandler
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            cfg_path.parent / "sidecar.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(handler)
    except Exception:
        pass


def main(argv=None):
    force_utf8_stdio()
    setup_logging()
    argv = argv if argv is not None else sys.argv[1:]
    cfg_path = Path(argv[0]) if argv else default_config_path()
    attach_file_log(cfg_path)

    cfg = load_config(cfg_path)
    keys = resolve_keys(cfg)
    corrections_path = cfg_path.parent / "corrections.json"
    events.state("loading")
    log.info("config %s; stt=%s formatter=%s", cfg_path, cfg["stt"]["provider"], cfg["formatter"]["provider"])
    app = build_app(cfg, keys, corrections_path=corrections_path)
    # Warm synchronous targets (a local/GPU primary + the mic) before going idle;
    # a cloud primary's local fallback warms in the background so cloud users can
    # dictate the instant the app is ready instead of waiting on a CPU model load.
    try:
        warmup.run(app)
    except Exception as exc:
        log.warning("warmup failed: %s", exc)
    events.state("idle")
    app.snapshot()  # publish current correction entries to the shell
    log.info("ready; waiting for commands on stdin")

    def on_reload():
        new_cfg = load_config(cfg_path)
        app.apply_config(new_cfg, resolve_keys(new_cfg))
        log.info("config reloaded")
        events.state("idle")

    stdin_command_loop(app, on_reload=on_reload)
    log.info("stdin closed; exiting")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # last-resort: surface fatal errors as an event
        logging.getLogger("murmur.main").exception("fatal")
        events.error(f"fatal: {exc}")
