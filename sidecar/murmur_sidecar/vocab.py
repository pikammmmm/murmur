"""Built-in technical vocabulary.

Common program / brand / dev words (GitHub, Discord, autostart, Roblox, Rust,
Luau, ...) that general speech models tend to mis-hear or split. These are fed
to the STT *bias* (faster-whisper `hotwords` / cloud `prompt`) so the recognizer
is nudged toward the right spelling for everyone, before the user teaches a
single word.

The broad BASE_VOCAB is bias-only by design: biasing merely nudges recognition
and never rewrites correct text, so a broad list is safe there. We do NOT feed
that list to the *fuzzy* corrector — common words in it would cause
over-correction (e.g. fuzzy "trust" -> "Rust"). Separately, FIXES are exact,
deterministic post-STT substitutions for unambiguous brand mis-splits/mis-cases;
those DO feed the corrector (as entries) because exact matching is safe.
"""

# Curated, user-domain-weighted. Keep this focused (the bias list is capped and
# the cloud prompt has a token budget) — high-value proper nouns and the terms
# models most often fumble.
BASE_VOCAB = [
    # the user's stack / projects
    "Roblox", "Robux", "Luau", "Lua", "Rojo", "Blender", "Tauri", "Rust",
    "Python", "PyInstaller", "Electron", "glassbar",
    # brands / platforms
    "GitHub", "GitLab", "Discord", "Spotify", "YouTube", "Steam", "Reddit",
    "Twitch", "NVIDIA", "AMD", "Radeon", "Ryzen", "Intel", "Windows", "Linux",
    "macOS", "Android",
    # AI / models
    "Anthropic", "Claude", "OpenAI", "ChatGPT", "Groq", "Cerebras", "Whisper",
    "LLM",
    # dev langs / tools
    "JavaScript", "TypeScript", "Node.js", "npm", "React", "Vite", "Docker",
    "Cargo", "Git", "PowerShell", "VS Code",
    # git / GitHub workflow
    "GitHub", "commit", "repository", "repo", "branch", "merge", "rebase",
    "pull request", "push", "clone", "fork", "stash", "diff", "gist",
    "pipeline", "Kubernetes", "Bitbucket",
    # terms commonly mis-split / mis-cased
    "autostart", "async", "await", "OAuth", "API", "SDK", "CLI", "JSON",
    "regex", "repo", "README", "localhost", "frontend", "backend", "runtime",
    "plugin", "webhook", "endpoint", "GPU", "CPU",
]

# Deterministic canonicalizations: exact, case-insensitive, whole-word fixes for
# brand/tool names that speech models commonly mis-split or mis-case. Unlike the
# bias list these run POST-STT (so they help every path, incl. the GPU one where
# the bias prompt is intentionally off), and they're applied as correction
# *entries* — exact substitutions, never fuzzy — so they can't touch ordinary
# words. SAFETY RULE: only add a `wrong` form that is never a normal English
# word/phrase (so "git hub"/"github" are fine; a common word like "discord" is
# NOT, since it could be meant literally).
FIXES = [
    ("git hub", "GitHub"), ("github", "GitHub"),
    ("git lab", "GitLab"), ("gitlab", "GitLab"),
    ("bit bucket", "Bitbucket"),
    ("java script", "JavaScript"),
    ("py installer", "PyInstaller"), ("pi installer", "PyInstaller"),
    ("pie installer", "PyInstaller"), ("pyinstaller", "PyInstaller"),
    ("ro blox", "Roblox"), ("roblox", "Roblox"), ("robux", "Robux"),
    ("lua u", "Luau"), ("ro jo", "Rojo"),
]


def fix_entries():
    """The built-in FIXES as correction entries (deterministic substitutions)."""
    return [{"wrong": w, "right": r, "count": 0, "source": "builtin"} for w, r in FIXES]


def for_bias(user_terms):
    """Bias term list: the user's own terms FIRST (so they win the cap), then the
    built-in vocab. Deduped case-insensitively."""
    out, seen = [], set()
    for t in list(user_terms or []) + BASE_VOCAB:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out
