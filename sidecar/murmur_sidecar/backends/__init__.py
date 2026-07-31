"""Platform backend selection.

``get_backend()`` returns the singleton backend for the running OS. Tests use
``set_backend()`` to swap in a fake and restore afterwards.
"""
import sys

from .base import Backend, NullBackend

_BACKEND = None


def _make_backend():
    if sys.platform.startswith("win"):
        from .win32 import Win32Backend
        return Win32Backend()
    if sys.platform.startswith("linux"):
        from .linux import LinuxBackend
        return LinuxBackend()
    return NullBackend()


def get_backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _make_backend()
    return _BACKEND


def set_backend(backend):
    """Install ``backend`` (or reset to auto-detect with None). Returns the
    previous backend so callers/tests can restore it."""
    global _BACKEND
    previous = _BACKEND
    _BACKEND = backend
    return previous


__all__ = ["Backend", "NullBackend", "get_backend", "set_backend"]
