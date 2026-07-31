"""The host-OS interface the sidecar pipeline depends on.

Everything murmur needs from the operating system — text injection, clipboard,
foreground-window inspection, audio cues — lives behind this one interface. The
pipeline modules (``injector``, ``context``, ``cues``) call the active backend
instead of importing OS APIs directly, so supporting a new platform means adding
one file next to this one rather than sprinkling ``sys.platform`` branches
through the pipeline.

Every method is best-effort: a backend that cannot do something returns the
neutral value (``None`` / ``("", "")``) rather than raising, because none of
these are worth breaking a dictation over. The one exception is
``set_clipboard``, whose failure ``injector.paste_text`` needs to see.
"""


class Backend:
    """Default no-op implementation; subclasses override what they support."""

    #: Short platform id, e.g. "win32" / "linux" / "null".
    name = "null"

    # --- text injection -------------------------------------------------
    def make_controller(self):
        """Return an object with a ``.type(str)`` method, or None if typing is
        unsupported. Duck-typed so ``injector.type_text`` stays platform-free
        and unit-testable with a fake."""
        return None

    def default_char_delay(self):
        """Preferred seconds between characters when typing, or None to accept
        the caller's default. A backend whose typing helper paces itself (and
        costs a process spawn per call) returns 0 to ask for bulk typing."""
        return None

    def send_paste(self):
        """Send the paste chord (Ctrl+V) to the focused window."""

    # --- clipboard ------------------------------------------------------
    def get_clipboard(self):
        """Current clipboard text, or None if unreadable/empty."""
        return None

    def set_clipboard(self, text):
        """Put ``text`` on the clipboard. May raise; callers handle it."""

    # --- window context -------------------------------------------------
    def active_window(self):
        """``(exe, title)`` for the foreground window; ``("", "")`` if unknown."""
        return ("", "")

    # --- audio cues -----------------------------------------------------
    def beep(self, pairs):
        """Play ``[(freq_hz, duration_ms), ...]`` synchronously. Silent on failure."""

    # --- diagnostics ----------------------------------------------------
    def diagnostics(self):
        """Human-readable dict of what this backend resolved to. Used by the
        runbook / troubleshooting, never by the pipeline."""
        return {"backend": self.name}


#: Backwards-compatible alias — a Backend with nothing overridden *is* the null
#: backend, used on unsupported platforms so the pipeline still runs.
NullBackend = Backend
