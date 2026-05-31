"""Type text into the currently-focused window via pynput.

``controller`` is injectable for testing; in production a pynput keyboard
Controller is created lazily (so importing this module needs no display/input
backend).
"""
import logging

log = logging.getLogger("murmur.injector")


def type_text(text, controller=None):
    if not text:
        return
    if controller is None:
        from pynput.keyboard import Controller
        controller = Controller()
    controller.type(text)
