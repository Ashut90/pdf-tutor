"""
Central configuration: theme tokens, fonts, AI providers, teaching modes.
Edit this file to add providers, models, or change the look.
"""
import sys

# ── Theme colors (GitHub dark palette) ──────────────────────────────────────
BG        = "#0d1117"
PANEL     = "#161b22"
CARD      = "#1c2333"
BORDER    = "#30363d"
BLUE      = "#58a6ff"
GREEN     = "#3fb950"
ORANGE    = "#f78166"
TEXT      = "#e6edf3"
MUTED     = "#8b949e"
WARN      = "#d29922"
INPUT     = "#21262d"
ACCENT    = "#1f6feb"
CODE_BLUE = "#79c0ff"

# ── Fonts (platform-aware) ──────────────────────────────────────────────────
FN = ("Segoe UI" if sys.platform == "win32" else
      "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans")
FM = "Courier New" if sys.platform == "win32" else "DejaVu Sans Mono"

H1   = (FN, 15, "bold")
H2   = (FN, 12, "bold")
H3   = (FN, 11, "bold")
BODY = (FN, 11)
SM   = (FN, 9)
MONO = (FM, 10)

# ── Content limits ──────────────────────────────────────────────────────────
MAX_CTX = 40000   # chars stored from a chapter; per-provider caps applied at request time

# ── AI Providers ────────────────────────────────────────────────────────────
PROVIDERS = {
    "Ollama (Local, Free)": {
        "id": "ollama", "needs_key": False,
        "models": ["qwen2.5-coder:7b", "deepseek-coder-v2:lite", "llama3.1:8b",
                   "mistral", "llama3.2", "codellama", "phi3", "gemma2"],
        "note": "Local — needs 'sudo systemctl start ollama'. Click refresh to detect installed models.",
    },
    "Groq (Free API, Fast)": {
        "id": "groq", "needs_key": True,
        "models": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile",
                   "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
        "note": "Free key from console.groq.com. Note: free tier ~12K tokens/request.",
    },
    "OpenRouter (Free Models)": {
        "id": "openrouter", "needs_key": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-r1:free",
                   "google/gemini-2.0-flash-exp:free", "qwen/qwen-2.5-72b-instruct:free"],
        "note": "Free key from openrouter.ai",
    },
    "Google Gemini (Best Free, 1M context)": {
        "id": "gemini", "needs_key": True,
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite",
                   "gemini-2.5-pro", "gemini-2.0-flash"],
        "note": "Free key from aistudio.google.com/apikey — 1M context fits whole chapters.",
    },
}
