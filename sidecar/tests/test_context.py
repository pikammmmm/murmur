import pytest

from murmur_sidecar.context import classify


@pytest.mark.parametrize(
    "exe,title,expected",
    [
        ("OUTLOOK.EXE", "Inbox - me@x.com - Outlook", "email"),
        ("slack.exe", "Slack | general | Acme", "chat"),
        ("Teams.exe", "Chat | Microsoft Teams", "chat"),
        ("discord.exe", "#general - Acme - Discord", "chat"),
        ("Code.exe", "file.py - murmur - Visual Studio Code", "code"),
        ("devenv.exe", "murmur - Microsoft Visual Studio", "code"),
        ("chrome.exe", "Inbox (3) - me@gmail.com - Gmail - Google Chrome", "email"),
        ("chrome.exe", "Slack | #dev - Google Chrome", "chat"),
        ("msedge.exe", "murmur - GitHub - Microsoft Edge", "code"),
        ("chrome.exe", "Some random article - Google Chrome", "generic"),
        ("notepad.exe", "Untitled - Notepad", "notes"),
        ("obsidian.exe", "Daily Note - Obsidian", "notes"),
        ("randomgame.exe", "whatever", "generic"),
        ("", "", "generic"),
    ],
)
def test_classify(exe, title, expected):
    assert classify(exe, title) == expected
