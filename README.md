# 📚 PDF Tutor

> Study any PDF the way *you* learn best — locally, privately, with diagrams, audio, flashcards, and hands-on commands.

[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-8%20passing-brightgreen.svg)](tests/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#-quick-start)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

![PDF Tutor demo](assets/demo.gif)

PDF Tutor reads the table of contents of any PDF, lets you pick a chapter, and explains it through your preferred AI model — **fully offline** on your own machine, or via **free cloud APIs** for higher quality. It adapts to how *you* learn using the VARK model: Visual diagrams, Auditory TTS, Read/Write notes, or Kinesthetic hands-on commands.

---

## ⚡ Try it in 30 seconds

```bash
git clone https://github.com/Ashut90/pdf-tutor.git
cd pdf-tutor
pip install -r requirements.txt
python -m pdf_tutor
```

Drop any PDF onto the app, pick a chapter, pick a learning mode — that's it.

---

## ✨ Features

### 📖 Smart PDF handling
- **Auto-detects chapters** from the PDF's table of contents
- **Dual viewer** — read extracted text or view actual rendered pages
- **Page-range control** — load a full chapter or a single section

### 🤖 Four AI providers (use whichever fits)
| Provider | Cost | Runs | Best for |
|----------|------|------|----------|
| **Ollama** | Free | Locally (offline) | Privacy, no limits, no internet |
| **Google Gemini** | Free tier | Cloud | Whole chapters (1M token context) |
| **Groq** | Free tier | Cloud | Fast inference |
| **OpenRouter** | Free tier | Cloud | Model variety |

### 🧠 VARK learning system
Built around the **VARK model** (Visual, Auditory, Read/Write, Kinesthetic):
- **🎯 Style detector** — a short quiz that recommends your learning style
- **🎨 Visual mode** — mind maps, flowcharts, comparison tables
- **🎧 Auditory mode** — conversational explanations + text-to-speech playback
- **📝 Read/Write mode** — definitions, structured notes, writing prompts
- **🛠️ Kinesthetic mode** — hands-on terminal commands and code experiments
- **🌐 Omni mode** — all four styles in a single response

### 🎨 Rich visual output
- **Mermaid diagrams** rendered via online APIs with a **local graphviz fallback** (works offline)
- **Charts** generated locally with matplotlib
- **ASCII diagrams** for reliable inline visuals

### 💾 Export & study tools
- **Save notes** as Markdown, HTML, or plain text
- **Anki flashcard export** — auto-generates spaced-repetition cards
- **Mindmap export** — interactive HTML mindmaps
- **Text-to-speech** — listen to any explanation

---

## 🖼️ Screenshots

<!--
  Replace these with actual screenshots once captured.
  Suggested shots:
    1. assets/screenshot-main.png  — main 3-pane window with a PDF loaded
    2. assets/screenshot-vark.png  — VARK mode selector
    3. assets/screenshot-mindmap.png — exported mindmap in browser

  To record a GIF on Ubuntu (install once, drag-select region):
    sudo apt install peek && peek

  Then reference it at the very top of this README:
    ![PDF Tutor demo](assets/demo.gif)
-->

| Main window | VARK learning modes |
|:-----------:|:-------------------:|
| _coming soon_ | _coming soon_ |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- (Optional) [Ollama](https://ollama.com) for offline local models
- (Optional) `graphviz` and `espeak-ng` system packages for diagram fallback and TTS

### Installation

PDF Tutor runs on **Linux, Windows, and macOS** — anywhere Python 3.9+ works.

**1. Clone and enter the project:**
```bash
git clone https://github.com/Ashut90/pdf-tutor.git
cd pdf-tutor
```

**2. Create a virtual environment and install Python dependencies:**

<details open>
<summary><b>Linux / macOS</b></summary>

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
</details>

**3. (Optional) install system packages for full features:**

| Feature | Linux (Debian/Ubuntu) | macOS (Homebrew) | Windows |
|---------|----------------------|------------------|---------|
| Diagram fallback | `sudo apt install graphviz` | `brew install graphviz` | [graphviz.org/download](https://graphviz.org/download/) |
| Offline TTS | `sudo apt install espeak-ng` | built-in (uses `say`) | built-in (SAPI5) |

> These are optional. Without graphviz, diagrams still render via online services. Without espeak-ng, TTS falls back to the online voice (gTTS).

### Run

Same command on every OS:

```bash
python -m pdf_tutor
# or
python run.py
```

---

## 🔧 Setting up AI providers

### Option 1 — Ollama (free, local, offline)
```bash
# Install Ollama from https://ollama.com, then pull a model:
ollama pull qwen2.5-coder:7b     # great for technical content
ollama serve                     # starts the local server
```
In the app: select **Ollama**, no API key needed.

### Option 2 — Google Gemini (free, best for whole chapters)
1. Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. In the app: select **Google Gemini**, paste your key
3. Gemini's 1M-token context handles entire chapters without truncation

### Option 3 — Groq / OpenRouter
Get free keys from [console.groq.com](https://console.groq.com) or [openrouter.ai](https://openrouter.ai) and paste into the app.

---

## 📂 Project Structure

```
pdf-tutor/
├── pdf_tutor/
│   ├── __init__.py
│   ├── __main__.py          # entry point: python -m pdf_tutor
│   ├── config.py            # theme, fonts, providers, limits
│   ├── core/
│   │   └── pdf.py           # TOC extraction, text/page rendering
│   ├── ai/
│   │   └── client.py        # unified client for all 4 providers
│   ├── rendering/
│   │   └── visuals.py       # mermaid / chart / graphviz rendering
│   ├── learning/
│   │   └── modes.py         # teaching modes + VARK prompts
│   └── ui/
│       └── app.py           # Tkinter GUI (3-pane layout)
├── tests/                   # pytest suite
├── requirements.txt
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  PDF Library│     │  PDF Viewer  │     │  AI Chat    │
│  (chapters) │────▶│ text / image │────▶│  + VARK     │
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
   core/pdf.py      rendering/visuals.py    ai/client.py
   (PyMuPDF)        (mermaid/charts)        (4 providers)
```

The UI loads a chapter via `core/pdf.py`, sends its text plus a mode-specific
prompt (`learning/modes.py`) to the selected provider (`ai/client.py`), and
renders any diagrams the AI produces (`rendering/visuals.py`).

---

## 🧪 Running tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

---

## 🗺️ Roadmap

- [ ] Conversation history persistence across sessions
- [ ] Multi-PDF library with search
- [ ] Spaced-repetition scheduler built in (beyond Anki export)
- [ ] Support for EPUB and DjVu formats
- [ ] Configurable prompt templates per subject

---

## 🤝 Contributing

Contributions are welcome. Please open an issue to discuss major changes first.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Run the tests (`pytest`)
4. Commit and open a pull request

---

## 📝 License

MIT — see [LICENSE](LICENSE).

---

## 🙏 Acknowledgments

- [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF parsing
- [Ollama](https://ollama.com), [Groq](https://groq.com), [Google AI Studio](https://aistudio.google.com), [OpenRouter](https://openrouter.ai) for model access
- The [VARK model](https://vark-learn.com/) by Neil Fleming for the learning-styles framework
