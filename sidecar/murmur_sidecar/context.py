"""Active-application context detection -> a formatting profile.

``classify(exe, title)`` is a pure function (unit-tested). ``detect()`` reads the
live foreground window via Win32 and is best-effort: any failure degrades to the
``generic`` profile rather than raising. It is exercised by the manual runbook.

Profiles: ``email | chat | code | notes | generic``.
"""

EMAIL_EXES = {"outlook.exe", "hxoutlook.exe", "thunderbird.exe", "mailspring.exe"}
CHAT_EXES = {
    "slack.exe", "teams.exe", "ms-teams.exe", "discord.exe",
    "telegram.exe", "whatsapp.exe", "signal.exe",
}
CODE_EXES = {
    "code.exe", "code - insiders.exe", "devenv.exe", "pycharm64.exe",
    "idea64.exe", "sublime_text.exe", "rider64.exe", "clion64.exe",
    "webstorm64.exe", "cursor.exe",
}
NOTES_EXES = {
    "notepad.exe", "notepad++.exe", "obsidian.exe", "wordpad.exe",
    "winword.exe", "onenote.exe", "notion.exe",
}
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "arc.exe",
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


def _resolve_uwp_child(hwnd, host_pid, default):
    """UWP/packaged apps run under ApplicationFrameHost.exe; the real app is a
    child window owned by a different PID. Return that child's exe name."""
    import psutil
    import win32gui
    import win32process

    names = []

    def _cb(child, _ctx):
        try:
            _, cpid = win32process.GetWindowThreadProcessId(child)
            if cpid and cpid != host_pid:
                name = psutil.Process(cpid).name()
                if name.lower() != "applicationframehost.exe":
                    names.append(name)
        except Exception:
            pass
        return True  # keep enumerating

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return names[0] if names else default


def detect():
    """Return ``(profile, exe, title)`` for the current foreground window.

    Best-effort and Windows-only: missing pywin32 or any Win32 error yields
    ``("generic", "", "")`` so the pipeline never breaks on context detection.
    """
    try:
        import psutil
        import win32gui
        import win32process
    except Exception:
        return ("generic", "", "")
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = psutil.Process(pid).name()
        if exe.lower() == "applicationframehost.exe":
            exe = _resolve_uwp_child(hwnd, pid, exe)
        return (classify(exe, title), exe, title)
    except Exception:
        return ("generic", "", "")
