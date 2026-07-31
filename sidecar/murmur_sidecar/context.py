"""Active-application context detection -> a formatting profile.

``classify(exe, title)`` is a pure function (unit-tested). ``detect()`` asks the
platform backend for the foreground window and is best-effort: any failure
degrades to the ``generic`` profile rather than raising. It is exercised by the
manual runbook.

Profiles: ``email | chat | code | notes | generic``.

The exe sets carry both Windows image names (``code.exe``) and Linux process /
WM_CLASS names (``code``), because the same app is identified differently on
each platform and one shared table keeps the classifier pure and testable.
"""
from .backends import get_backend

EMAIL_EXES = {
    # Windows
    "outlook.exe", "hxoutlook.exe", "thunderbird.exe", "mailspring.exe",
    # Linux
    "thunderbird", "betterbird", "evolution", "kmail", "geary", "mailspring",
}
CHAT_EXES = {
    # Windows
    "slack.exe", "teams.exe", "ms-teams.exe", "discord.exe",
    "telegram.exe", "whatsapp.exe", "signal.exe",
    # Linux
    "slack", "discord", "discordcanary", "telegram-desktop", "signal-desktop",
    "element-desktop", "teams-for-linux", "whatsapp-for-linux",
}
CODE_EXES = {
    # Windows
    "code.exe", "code - insiders.exe", "devenv.exe", "pycharm64.exe",
    "idea64.exe", "sublime_text.exe", "rider64.exe", "clion64.exe",
    "webstorm64.exe", "cursor.exe",
    # Linux
    "code", "code-insiders", "codium", "vscodium", "cursor", "zed",
    "pycharm", "idea", "clion", "webstorm", "rider", "sublime_text",
    "kdevelop", "qtcreator", "emacs",
}
NOTES_EXES = {
    # Windows
    "notepad.exe", "notepad++.exe", "obsidian.exe", "wordpad.exe",
    "winword.exe", "onenote.exe", "notion.exe",
    # Linux
    "obsidian", "kate", "kwrite", "gedit", "gnome-text-editor", "logseq",
    "notion-app", "standard-notes", "soffice", "libreoffice",
}
BROWSER_EXES = {
    # Windows
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "arc.exe",
    # Linux
    "firefox", "firefox-bin", "librewolf", "zen", "chromium",
    "google-chrome", "google-chrome-stable", "brave", "brave-browser",
    "vivaldi-bin", "vivaldi", "opera", "microsoft-edge",
}

# Title-substring -> profile, checked in order, only for browser windows.
_BROWSER_TITLE_RULES = [
    (("gmail", "outlook", "proton mail", "- mail", "yahoo mail"), "email"),
    (("slack", "discord", "teams", "whatsapp", "messenger", "telegram"), "chat"),
    (("github", "gitlab", "stack overflow", "visual studio code", "codepen", "replit", "codesandbox"), "code"),
]


def classify(exe, title):
    """Map a process exe name + window title to a formatting profile (pure)."""
    e = (exe or "").lower()
    t = (title or "").lower()
    if e in EMAIL_EXES:
        return "email"
    if e in CHAT_EXES:
        return "chat"
    if e in CODE_EXES:
        return "code"
    if e in NOTES_EXES:
        return "notes"
    if e in BROWSER_EXES:
        for needles, profile in _BROWSER_TITLE_RULES:
            if any(n in t for n in needles):
                return profile
        return "generic"
    return "generic"


def detect():
    """Return ``(profile, exe, title)`` for the current foreground window.

    Best-effort: any backend error, or a platform/compositor that will not
    report the foreground window (native Wayland clients, by design), yields
    ``("generic", "", "")`` so the pipeline never breaks on context detection.
    """
    try:
        exe, title = get_backend().active_window()
    except Exception:
        return ("generic", "", "")
    return (classify(exe, title), exe or "", title or "")
