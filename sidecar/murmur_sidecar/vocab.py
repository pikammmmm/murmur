"""Built-in technical vocabulary.

Common program / brand / dev words (GitHub, Discord, autostart, Roblox, Rust,
Luau, ...) that general speech models tend to mis-hear or split. These are fed
to the STT *bias* (faster-whisper `hotwords` / cloud `prompt`) so the recognizer
is nudged toward the right spelling for everyone, before the user teaches a
single word.

Bias-only by design: biasing merely nudges recognition and never rewrites text
that came out correct, so a broad list is safe here. We do NOT feed this list to
the phonetic auto-corrector — that stage rewrites tokens, and common words in it
would cause over-correction (e.g. fuzzy "trust" -> "Rust"). The corrector stays
driven by the user's own dictionary + learned corrections.
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
    # terms commonly mis-split / mis-cased
    "autostart", "async", "await", "OAuth", "API", "SDK", "CLI", "JSON",
    "regex", "repo", "README", "localhost", "frontend", "backend", "runtime",
    "plugin", "webhook", "GPU", "CPU",
]


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
