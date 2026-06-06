"""
Main application window (Tkinter GUI).
Three-pane layout: PDF library | PDF viewer + settings | AI chat.
Wires together PDF handling, AI client, visual rendering, and VARK learning tools.
"""
import os
import re
import io
import json
import threading
import webbrowser
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

from pdf_tutor.config import (
    BG, PANEL, CARD, BORDER, BLUE, GREEN, ORANGE, TEXT, MUTED, WARN,
    INPUT, ACCENT, CODE_BLUE, FN, FM, H1, H2, H3, BODY, SM, MONO,
    MAX_CTX, PROVIDERS,
)
from pdf_tutor.learning.modes import MODES, VISUAL_INSTRUCTIONS, LOCAL_GUIDANCE
from pdf_tutor.core.pdf import (
    PDF_OK, PIL_OK, get_toc_and_total, extract_text,
    render_page_image, build_chapter_list,
)
from pdf_tutor.rendering.visuals import (
    MPL_OK, MERMAID_OK, MMDC_INSTALLED, GRAPHVIZ_OK,
    render_mermaid_to_image, render_chart_to_image,
)
from pdf_tutor.ai.client import AIClient, ensure_ollama

# TTS availability (used by Auditory mode)
try:
    import pyttsx3
    TTS_OFFLINE_OK = True
except ImportError:
    TTS_OFFLINE_OK = False
try:
    from gtts import gTTS
    TTS_ONLINE_OK = True
except ImportError:
    TTS_ONLINE_OK = False

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Teaching Assistant v5.0")
        self.geometry("1500x900")
        self.minsize(1100, 700)
        self.configure(bg=BG)

        self.client = AIClient()
        self.history = []
        self.is_busy = False
        self.active_pdf = None
        self._tts_engine = None   # persistent engine avoids espeak GC callback crash
        self.pdf_text = ""
        self.pdf_total = 0
        self.chapters = []
        self.selected_chapter = None
        self.viewer_mode = "text"
        self.current_view_page = 0
        self.page_images = []
        self._first_chunk = True
        self._followup_frame = None
        # buffer for accumulating AI response (for post-render visual extraction)
        self._response_buffer = ""
        self._chat_image_refs = []  # keep PhotoImage refs so they don't GC
        self._chat_pil_refs   = []  # keep PIL originals for zoom viewer
        self._custom_instructions = ""

        self._build_ui()
        self._apply_provider()

    # ── UI BUILD ──────────────────────────────────────────────────────────────
    # ── TEXT-TO-SPEECH ────────────────────────────────────────────────────
    def _speak_response(self):
        """Read the last AI response aloud using TTS."""
        text = self._get_last_ai_response()
        if not text:
            messagebox.showinfo("Nothing to Speak",
                                 "No AI response found yet. Generate one first.")
            return
        # Strip code blocks and markdown for cleaner speech
        clean = re.sub(r'```[\s\S]*?```', ' code example ', text)
        clean = re.sub(r'`[^`]+`', '', clean)
        clean = re.sub(r'[#*_>|]', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        if not TTS_OFFLINE_OK and not TTS_ONLINE_OK:
            messagebox.showerror("TTS Not Available",
                                  "Install one:\n  pip install pyttsx3\n  pip install gtts")
            return

        self._status("Speaking...", BLUE)

        def speak():
            try:
                if TTS_OFFLINE_OK:
                    if self._tts_engine is None:
                        self._tts_engine = pyttsx3.init()
                        self._tts_engine.setProperty("rate", 175)
                    engine = self._tts_engine
                    engine.say(clean[:5000])
                    engine.runAndWait()
                elif TTS_ONLINE_OK:
                    import tempfile, subprocess, sys as _sys
                    tts = gTTS(text=clean[:5000], lang="en", slow=False)
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                        tts.save(f.name)
                        # Cross-platform: open the audio with the OS default player
                        if _sys.platform == "win32":
                            os.startfile(f.name)                       # Windows
                        elif _sys.platform == "darwin":
                            subprocess.run(["open", f.name], capture_output=True)   # macOS
                        else:
                            subprocess.run(["xdg-open", f.name], capture_output=True)  # Linux
                self.after(0, lambda: self._status("Ready", GREEN))
            except Exception as e:
                self.after(0, lambda err=str(e):
                            messagebox.showerror("TTS Error", err))
                self.after(0, lambda: self._status("Error", ORANGE))

        threading.Thread(target=speak, daemon=True).start()

    def _get_last_ai_response(self):
        """Return text of the last AI response in chat."""
        text = self.chat_view.get("1.0", "end")
        # Find last "🤖 AI Teacher" label
        marker = "🤖 AI Teacher"
        last_pos = text.rfind(marker)
        if last_pos < 0:
            return ""
        # Get everything after that label
        after_label = text[last_pos + len(marker):]
        # Stop at next "👤" (next user message) if any
        next_user = after_label.find("👤")
        if next_user > 0:
            return after_label[:next_user].strip()
        return after_label.strip()

    # ── ANKI FLASHCARD EXPORT ─────────────────────────────────────────────
    def _export_anki(self):
        """Generate Anki-compatible flashcards from the last AI response.
        Format: tab-separated Front / Back, importable into Anki."""
        text = self._get_last_ai_response()
        if not text:
            messagebox.showinfo("Nothing to Export",
                                 "Generate an AI response first, then export.")
            return
        if not self.selected_chapter:
            messagebox.showwarning("No Chapter", "Load a chapter first.")
            return

        # Use AI to extract Q/A pairs from the response
        self._status("Generating flashcards...", WARN)
        title = self.selected_chapter[0]
        prompt = (f"From the following content, generate 10 high-quality Anki flashcards. "
                  f"Output ONLY the cards in this exact format (one per line, tab-separated):\n"
                  f"FRONT_QUESTION\tBACK_ANSWER\n\n"
                  f"Rules:\n"
                  f"- Front = a focused question or fill-in-the-blank\n"
                  f"- Back = concise complete answer (max 3 sentences)\n"
                  f"- NO bullet points, NO extra text, NO headers\n"
                  f"- Just 10 lines of: question[TAB]answer\n\n"
                  f"Content:\n{text[:6000]}")

        pname = self.pv.get()
        prov = PROVIDERS[pname]
        model = self.mv.get()
        key = self._get_clean_key() if prov["needs_key"] else ""

        def go():
            try:
                msgs = [{"role": "user", "content": prompt}]
                response = self.client.chat(prov["id"], model, key, msgs)
                # Parse Q/A pairs
                cards = []
                for line in response.split("\n"):
                    line = line.strip()
                    if "\t" in line and not line.startswith("#"):
                        parts = line.split("\t", 1)
                        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                            cards.append((parts[0].strip(), parts[1].strip()))
                if not cards:
                    self.after(0, lambda: messagebox.showwarning(
                        "No Cards", "Could not parse flashcards from response."))
                    self.after(0, lambda: self._status("Ready", GREEN))
                    return
                self.after(0, lambda c=cards: self._save_anki_file(c, title))
            except Exception as e:
                self.after(0, lambda err=str(e):
                            messagebox.showerror("Flashcard Gen Error", err))
                self.after(0, lambda: self._status("Error", ORANGE))

        threading.Thread(target=go, daemon=True).start()

    def _save_anki_file(self, cards, title):
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe = re.sub(r'[^\w\s-]', '', title).strip()[:40]
        safe = re.sub(r'[\s]+', '_', safe)
        # Deck name from the loaded book's filename, not a hardcoded subject.
        deck = "Study Notes"
        if self.active_pdf:
            deck = os.path.splitext(os.path.basename(self.active_pdf))[0][:50]
        default = f"anki_{safe}_{ts}.txt"
        path = filedialog.asksaveasfilename(
            title="Save Anki Flashcards",
            initialdir=os.path.expanduser("~/Documents"),
            initialfile=default,
            defaultextension=".txt",
            filetypes=[("Anki tab-separated", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                # Anki import header (optional, helps Anki recognize fields)
                f.write("#separator:tab\n")
                f.write("#html:false\n")
                f.write(f"#deck:{deck}\n")
                f.write(f"#tags:{safe}\n")
                for front, back in cards:
                    f.write(f"{front}\t{back}\n")
            self._status(f"Saved {len(cards)} flashcards", GREEN)
            messagebox.showinfo(
                "Anki Export",
                f"Saved {len(cards)} flashcards to:\n{path}\n\n"
                f"To import in Anki:\n"
                f"1. Open Anki\n"
                f"2. File > Import > select this file\n"
                f"3. Choose 'Tab' as separator\n"
                f"4. Deck: {deck} (auto-created)")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # ── MINDMAP EXPORT ────────────────────────────────────────────────────
    def _export_mindmap(self):
        """Generate a Markmap mindmap (horizontal tree) and save as HTML."""
        text = self._get_last_ai_response()
        if not text:
            messagebox.showinfo("Nothing to Export", "Generate AI content first.")
            return
        if not self.selected_chapter:
            messagebox.showwarning("No Chapter", "Load a chapter first.")
            return

        self._status("Generating mindmap...", WARN)
        title = self.selected_chapter[0]
        prompt = (f"Create a mindmap outline for: {title}\n\n"
                  f"STRICT RULES:\n"
                  f"- Output ONLY a Markdown outline. No prose, no explanation, no backticks.\n"
                  f"- First line must be: # {title}\n"
                  f"- Use ## for main branches (max 6).\n"
                  f"- Use ### for sub-topics (max 4 per branch).\n"
                  f"- Use #### for details (optional, max 2 per sub-topic).\n"
                  f"- Each line must be SHORT: 2-5 words maximum. No sentences.\n\n"
                  f"EXAMPLE (structure only — use the ACTUAL topics from the content below):\n"
                  f"# Main Topic\n"
                  f"## First Major Branch\n"
                  f"### Sub-topic\n"
                  f"### Sub-topic\n"
                  f"## Second Major Branch\n"
                  f"### Sub-topic\n"
                  f"### Sub-topic\n\n"
                  f"Content:\n{text[:4000]}")

        pname = self.pv.get()
        prov = PROVIDERS[pname]
        model = self.mv.get()
        key = self._get_clean_key() if prov["needs_key"] else ""

        def go():
            try:
                msgs = [{"role": "user", "content": prompt}]
                response = self.client.chat(prov["id"], model, key, msgs)
                md = response.strip()
                # strip code fences if AI wrapped it
                md = re.sub(r'^```[a-z]*\n?', '', md, flags=re.MULTILINE)
                md = re.sub(r'```$', '', md, flags=re.MULTILINE).strip()
                if not md.startswith('#'):
                    md = f"# {title}\n" + md
                self.after(0, lambda m=md: self._save_mindmap_file(m, title))
            except Exception as e:
                self.after(0, lambda err=str(e):
                            messagebox.showerror("Mindmap Gen Error", err))
                self.after(0, lambda: self._status("Error", ORANGE))

        threading.Thread(target=go, daemon=True).start()

    def _save_mindmap_file(self, md_content, title):
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe = re.sub(r'[^\w\s-]', '', title).strip()[:40]
        safe = re.sub(r'[\s]+', '_', safe)
        default = f"mindmap_{safe}_{ts}.html"
        path = filedialog.asksaveasfilename(
            title="Save Mindmap",
            initialdir=os.path.expanduser("~/Documents"),
            initialfile=default,
            defaultextension=".html",
            filetypes=[("HTML (renders in browser)", "*.html"),
                       ("Markdown source", "*.md"), ("All", "*.*")])
        if not path:
            return
        try:
            if path.endswith(".md"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(md_content)
            else:
                html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mindmap: {title}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#f6f8fa;font-family:'Inter',-apple-system,sans-serif;min-height:100vh;display:flex;flex-direction:column}}
  header{{background:#0d1117;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
  header h1{{font-size:17px;font-weight:700;color:#58a6ff}}
  header .sub{{font-size:11px;color:#8b949e;margin-top:2px}}
  .toolbar{{display:flex;gap:8px}}
  .btn{{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:5px 14px;font-size:12px;font-family:inherit;cursor:pointer}}
  .btn:hover{{background:#30363d}}
  .btn-blue{{background:#1f6feb;border-color:#1f6feb;color:#fff}}
  .btn-blue:hover{{background:#388bfd}}
  #mindmap{{width:100%;height:calc(100vh - 90px)}}
  footer{{background:#0d1117;border-top:1px solid #30363d;padding:8px 24px;font-size:11px;color:#8b949e;text-align:center}}
  footer a{{color:#58a6ff;text-decoration:none}}
</style>
</head>
<body>
<header>
  <div>
    <h1>🧠 {title}</h1>
    <div class="sub">Mind Map · Generated by PDF Tutor</div>
  </div>
  <div class="toolbar">
    <button class="btn btn-blue" id="btnPng">⬇ Save PNG</button>
  </div>
</header>
<svg id="mindmap"></svg>
<footer>
  Generated by <a href="https://github.com/Ashut90/pdf-tutor">PDF Tutor</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/Ashut90/pdf-tutor">⭐ Star on GitHub</a>
</footer>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-view@0.15"></script>
<script src="https://cdn.jsdelivr.net/npm/markmap-lib@0.15"></script>
<script>
const md = {repr(md_content)};
const {{ Transformer, builtInPlugins }} = window.markmap;
const transformer = new Transformer(builtInPlugins);
const {{ root, features }} = transformer.transform(md);
const {{ Markmap, loadCSS, loadJS }} = window.markmap;
const {{ styles, scripts }} = transformer.getUsedAssets(features);
if (styles) loadCSS(styles);
if (scripts) loadJS(scripts, {{ getMarkmap: () => window.markmap }});
const mm = Markmap.create('#mindmap', {{
  color: (node) => ['#1f6feb','#1a7f37','#6e40c9','#b45309','#0e7490','#be185d'][node.depth % 6],
  duration: 400,
  maxWidth: 220,
  zoom: true,
  pan: true,
}}, root);

document.getElementById('btnPng').onclick = () => {{
  const svg = document.getElementById('mindmap');
  const xml = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([xml], {{type:'image/svg+xml'}});
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => {{
    const c = document.createElement('canvas');
    c.width = img.width * 2; c.height = img.height * 2;
    const ctx = c.getContext('2d');
    ctx.scale(2,2);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0,0,img.width,img.height);
    ctx.drawImage(img,0,0);
    const a = document.createElement('a');
    a.download = '{safe}_mindmap.png';
    a.href = c.toDataURL('image/png');
    a.click();
    URL.revokeObjectURL(url);
  }};
  img.src = url;
}};
</script>
</body></html>"""
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
            self._status("Mindmap saved", GREEN)
            messagebox.showinfo("Mindmap Saved",
                                 f"Saved to:\n{path}\n\n"
                                 f"Open the HTML file in any browser to view the rendered mindmap.")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # ── VARK STYLE DETECTOR ───────────────────────────────────────────────
    def _show_vark_quiz(self):
        """Show a short VARK questionnaire and recommend a learning mode."""
        dlg = tk.Toplevel(self)
        dlg.title("VARK Style Detector")
        dlg.geometry("600x550")
        dlg.configure(bg=PANEL)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="🎯 Discover Your Learning Style",
                 bg=PANEL, fg=BLUE, font=H1).pack(pady=(14, 4))
        tk.Label(dlg, text="Answer 5 quick questions. Pick what feels most natural.",
                 bg=PANEL, fg=MUTED, font=SM).pack(pady=(0, 10))

        questions = [
            ("When learning a new concept, I prefer to:",
             [("See a diagram or visual", "V"), ("Hear someone explain it", "A"),
              ("Read about it", "R"), ("Try it hands-on", "K")]),
            ("When directions to a new place, I prefer:",
             [("A map", "V"), ("Verbal directions", "A"),
              ("Written list of streets", "R"), ("Just drive and figure it out", "K")]),
            ("When studying, I most often:",
             [("Highlight, color-code, draw diagrams", "V"),
              ("Read aloud, discuss with others", "A"),
              ("Take detailed written notes", "R"),
              ("Practice on examples, experiment", "K")]),
            ("To explain something complex to a friend, I'd:",
             [("Draw it out", "V"), ("Tell them a story", "A"),
              ("Write them an email", "R"), ("Show them by doing it", "K")]),
            ("Best way to remember something new:",
             [("Picture it in my head", "V"), ("Repeat it out loud", "A"),
              ("Write it down repeatedly", "R"), ("Use it in practice", "K")]),
        ]

        scores = {"V": 0, "A": 0, "R": 0, "K": 0}
        responses = []

        scroll_frame = tk.Frame(dlg, bg=PANEL)
        scroll_frame.pack(fill="both", expand=True, padx=20)

        for i, (q, options) in enumerate(questions):
            tk.Label(scroll_frame, text=f"{i+1}. {q}",
                     bg=PANEL, fg=TEXT, font=H3,
                     wraplength=540, justify="left", anchor="w").pack(anchor="w", pady=(8, 2))
            var = tk.StringVar()
            responses.append(var)
            for opt_text, opt_code in options:
                tk.Radiobutton(scroll_frame, text=opt_text, variable=var, value=opt_code,
                                bg=PANEL, fg=TEXT, selectcolor=CARD,
                                activebackground=PANEL, activeforeground=BLUE,
                                font=SM, anchor="w").pack(anchor="w", padx=20)

        def submit():
            for var in responses:
                code = var.get()
                if code:
                    scores[code] += 1
            if sum(scores.values()) < 3:
                messagebox.showwarning("Incomplete",
                                        "Please answer at least 3 questions.")
                return
            top_style = max(scores, key=scores.get)
            style_map = {
                "V": ("🎨 Visual Learning",
                      "You learn best with diagrams, charts, and mind maps.\n"
                      "Use the '🎨 Visual Learning' mode for richest output."),
                "A": ("🎧 Auditory Learning",
                      "You learn best by listening and verbal explanation.\n"
                      "Use '🎧 Auditory Learning' mode + the 🔊 Speak button."),
                "R": ("📝 Read/Write Learning",
                      "You learn best with detailed notes and lists.\n"
                      "Use '📝 Read/Write Learning' mode for thorough text."),
                "K": ("🛠️ Kinesthetic Learning",
                      "You learn best by doing hands-on tasks.\n"
                      "Use '🛠️ Kinesthetic Learning' mode for command/code-driven learning."),
            }
            mode, desc = style_map[top_style]
            messagebox.showinfo("Your Learning Style",
                                 f"Top style: {mode}\n\n{desc}\n\n"
                                 f"Full scores:\n"
                                 f"  Visual: {scores['V']}\n"
                                 f"  Auditory: {scores['A']}\n"
                                 f"  Read/Write: {scores['R']}\n"
                                 f"  Kinesthetic: {scores['K']}\n\n"
                                 f"Tip: Use 🌐 Omni Learning to combine all four styles.")
            dlg.destroy()

        tk.Button(dlg, text="Get My Style Recommendation",
                  bg=ACCENT, fg="white", relief="flat", font=H2,
                  cursor="hand2", pady=8, command=submit).pack(fill="x", padx=20, pady=10)

    def _build_ui(self):
        top = tk.Frame(self, bg=PANEL, height=46)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="AI Teaching Assistant", bg=PANEL, fg=BLUE, font=H1).pack(side="left", padx=16, pady=8)

        # Big visible badge showing which AI is active
        self.active_ai_badge = tk.Label(top, text="  🤖 Loading...  ", bg=CARD, fg=BLUE,
                                         font=H2, padx=10, pady=4)
        self.active_ai_badge.pack(side="left", padx=8)

        self.status_lbl = tk.Label(top, text="● Ready", bg=PANEL, fg=GREEN, font=SM)
        self.status_lbl.pack(side="right", padx=16)

        tk.Label(top, text="github.com/Ashut90/pdf-tutor", bg=PANEL, fg=MUTED, font=SM,
                 cursor="hand2").pack(side="right", padx=(0, 4))

        pw = tk.PanedWindow(self, orient="horizontal", bg=BG, bd=0, sashwidth=5)
        pw.pack(fill="both", expand=True)

        left = tk.Frame(pw, bg=PANEL, width=280)
        pw.add(left, minsize=230)
        self._build_library(left)

        mid = tk.Frame(pw, bg=PANEL)
        pw.add(mid, minsize=340)
        self._build_viewer(mid)

        right = tk.Frame(pw, bg=BG)
        pw.add(right, minsize=520)
        self._build_chat(right)

        footer = tk.Frame(self, bg=PANEL, height=24)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, text="v1.0.0", bg=PANEL, fg=MUTED, font=SM).pack(side="left", padx=12)
        link = tk.Label(footer, text="⭐ github.com/Ashut90/pdf-tutor",
                        bg=PANEL, fg=MUTED, font=SM, cursor="hand2")
        link.pack(side="right", padx=12)
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/Ashut90/pdf-tutor"))
        link.bind("<Enter>", lambda e: link.config(fg=BLUE))
        link.bind("<Leave>", lambda e: link.config(fg=MUTED))

    def _build_library(self, p):
        tk.Button(p, text="📂   Open PDF Book", bg=ACCENT, fg="white", relief="flat",
                  font=H2, cursor="hand2", pady=10, command=self._open_pdf
        ).pack(fill="x", padx=10, pady=(10, 4))

        self.pdf_info = tk.Label(p, text="No PDF loaded", bg=CARD, fg=MUTED,
                                  font=SM, anchor="w", wraplength=250, justify="left")
        self.pdf_info.pack(fill="x", padx=10, pady=(0, 6), ipady=6, ipadx=8)

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=10, pady=2)

        self.ch_header = tk.Label(p, text="Chapters / Sections", bg=PANEL, fg=TEXT, font=H2)
        self.ch_header.pack(anchor="w", padx=10, pady=(6, 2))

        tk.Label(p, text="Click a chapter — auto-loads",
                 bg=PANEL, fg=WARN, font=SM).pack(anchor="w", padx=10, pady=(0, 4))

        lf = tk.Frame(p, bg=PANEL)
        lf.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        sb = tk.Scrollbar(lf, bg=PANEL, troughcolor=PANEL, relief="flat", width=8)
        sb.pack(side="right", fill="y")
        self.ch_list = tk.Listbox(lf, bg=PANEL, fg=TEXT, font=SM,
                                   selectbackground=ACCENT, selectforeground="white",
                                   relief="flat", bd=0, activestyle="none",
                                   yscrollcommand=sb.set, cursor="hand2", highlightthickness=0)
        self.ch_list.pack(fill="both", expand=True)
        sb.config(command=self.ch_list.yview)
        self.ch_list.bind("<<ListboxSelect>>", self._on_chapter_click)

        self.sel_info = tk.Label(p, text="No chapter selected", bg=CARD, fg=MUTED,
                                  font=SM, anchor="w", wraplength=250, justify="left")
        self.sel_info.pack(fill="x", padx=10, pady=(0, 6), ipady=6, ipadx=8)

        pr = tk.Frame(p, bg=PANEL)
        pr.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(pr, text="Pages:", bg=PANEL, fg=MUTED, font=SM).pack(side="left")
        self.pg_from = tk.Entry(pr, bg=INPUT, fg=TEXT, width=4, relief="flat",
                                 font=SM, insertbackground=TEXT)
        self.pg_from.pack(side="left", padx=4, ipady=3)
        tk.Label(pr, text="–", bg=PANEL, fg=MUTED, font=SM).pack(side="left")
        self.pg_to = tk.Entry(pr, bg=INPUT, fg=TEXT, width=4, relief="flat",
                               font=SM, insertbackground=TEXT)
        self.pg_to.pack(side="left", padx=4, ipady=3)
        tk.Button(pr, text="Load", bg=CARD, fg=TEXT, relief="flat",
                  font=SM, cursor="hand2", padx=8, command=self._manual_load
                  ).pack(side="left", padx=4)

    def _build_viewer(self, p):
        tb = tk.Frame(p, bg=CARD, height=36)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        tk.Label(tb, text="📖 PDF Viewer", bg=CARD, fg=TEXT, font=H3).pack(side="left", padx=10)

        self.view_text_btn = tk.Button(tb, text="Text", bg=ACCENT, fg="white", relief="flat",
                                        font=SM, cursor="hand2", padx=12, pady=2,
                                        command=lambda: self._set_viewer_mode("text"))
        self.view_text_btn.pack(side="left", padx=(8, 2))

        self.view_img_btn = tk.Button(tb, text="Page Image", bg=CARD, fg=MUTED, relief="flat",
                                       font=SM, cursor="hand2", padx=12, pady=2,
                                       command=lambda: self._set_viewer_mode("image"))
        self.view_img_btn.pack(side="left", padx=2)

        nav = tk.Frame(tb, bg=CARD)
        nav.pack(side="right", padx=8)
        tk.Button(nav, text="◀", bg=CARD, fg=TEXT, relief="flat", font=H3,
                  cursor="hand2", padx=6, command=self._prev_page).pack(side="left")
        self.page_lbl = tk.Label(nav, text="—", bg=CARD, fg=TEXT, font=SM, width=10)
        self.page_lbl.pack(side="left", padx=4)
        tk.Button(nav, text="▶", bg=CARD, fg=TEXT, relief="flat", font=H3,
                  cursor="hand2", padx=6, command=self._next_page).pack(side="left")

        self.viewer_container = tk.Frame(p, bg=PANEL)
        self.viewer_container.pack(fill="both", expand=True)

        self.text_view = scrolledtext.ScrolledText(
            self.viewer_container, wrap="word", bg=PANEL, fg=TEXT, font=BODY,
            relief="flat", bd=0, state="disabled", padx=12, pady=10,
            insertbackground=TEXT, selectbackground=ACCENT)
        self.text_view.tag_configure("hint", foreground=WARN, font=SM)

        self.image_view = tk.Canvas(self.viewer_container, bg=PANEL, bd=0, highlightthickness=0)
        self.img_scroll_y = tk.Scrollbar(self.viewer_container, orient="vertical",
                                          command=self.image_view.yview, bg=PANEL, width=10)
        self.image_view.configure(yscrollcommand=self.img_scroll_y.set)
        self.image_view.bind("<MouseWheel>", lambda e: self.image_view.yview_scroll(int(-e.delta/120), "units"))
        self.image_view.bind("<Button-4>",   lambda e: self.image_view.yview_scroll(-3, "units"))
        self.image_view.bind("<Button-5>",   lambda e: self.image_view.yview_scroll(3, "units"))
        self.image_view.bind("<Configure>",  lambda e: self._on_canvas_resize())

        self._set_viewer_mode("text")
        self._show_viewer_welcome()

        # Settings frame at bottom — let it auto-size to its contents
        settings = tk.Frame(p, bg=PANEL)
        settings.pack(fill="x", side="bottom")

        tk.Frame(settings, bg=BORDER, height=1).pack(fill="x")

        # ── AI Provider config (always visible, grid layout) ────────────────
        ai_box = tk.Frame(settings, bg=PANEL)
        ai_box.pack(fill="x", padx=10, pady=(8, 4))

        tk.Label(ai_box, text="AI Provider:", bg=PANEL, fg=MUTED, font=SM, width=10, anchor="w").grid(row=0, column=0, sticky="w", pady=2)
        self.pv = tk.StringVar(value=list(PROVIDERS.keys())[0])
        self.pcb = ttk.Combobox(ai_box, textvariable=self.pv, values=list(PROVIDERS.keys()),
                                 state="readonly", font=SM)
        self.pcb.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        # Single reliable binding — fires whenever the user picks a different value
        self.pcb.bind("<<ComboboxSelected>>", self._apply_provider)

        tk.Label(ai_box, text="Model:", bg=PANEL, fg=MUTED, font=SM, width=10, anchor="w").grid(row=1, column=0, sticky="w", pady=2)
        model_row = tk.Frame(ai_box, bg=PANEL)
        model_row.grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        model_row.columnconfigure(0, weight=1)
        self.mv = tk.StringVar()
        # Editable combobox — user can type their own model name
        self.mcb = ttk.Combobox(model_row, textvariable=self.mv, state="normal", font=SM)
        self.mcb.grid(row=0, column=0, sticky="ew")
        self.refresh_btn = tk.Button(model_row, text="↻ Refresh from API",
                                      bg=ACCENT, fg="white", relief="flat",
                                      font=SM, cursor="hand2", padx=10, pady=2,
                                      command=self._refresh_models)
        self.refresh_btn.grid(row=0, column=1, padx=(6, 0))

        # API Key row — always rendered, but enabled/disabled by provider
        tk.Label(ai_box, text="API Key:", bg=PANEL, fg=MUTED, font=SM, width=10, anchor="w").grid(row=2, column=0, sticky="w", pady=2)
        self.ke = tk.Entry(ai_box, show="*", bg=INPUT, fg=TEXT, relief="flat",
                            font=SM, insertbackground=TEXT)
        self.ke.grid(row=2, column=1, sticky="ew", padx=4, pady=2, ipady=4)
        self.ke.bind("<KeyRelease>", lambda e: self._update_active_badge())
        self.ke.bind("<<Paste>>",     lambda e: self.after(50, self._update_active_badge))
        self.kf = self.ke  # alias for backwards compat with _apply_provider

        ai_box.columnconfigure(1, weight=1)

        self.pnote = tk.Label(settings, text="", bg=PANEL, fg=WARN, font=SM,
                               wraplength=320, justify="left")
        self.pnote.pack(anchor="w", padx=10, pady=(2, 4))

        tk.Frame(settings, bg=BORDER, height=1).pack(fill="x", pady=4)

        tk.Label(settings, text="🎯 Click a teaching mode — auto-runs on loaded chapter",
                 bg=PANEL, fg=TEXT, font=H3).pack(anchor="w", padx=10, pady=(2, 2))

        mode_grid = tk.Frame(settings, bg=PANEL)
        mode_grid.pack(fill="x", padx=10, pady=2)
        for i, (name, cfg) in enumerate(MODES.items()):
            r, c = divmod(i, 4)
            btn = tk.Button(mode_grid, text=f"{cfg['icon']} {name}",
                             bg=CARD, fg=TEXT, relief="flat", font=SM, cursor="hand2",
                             padx=6, pady=6, command=lambda n=name: self._run_mode(n),
                             anchor="w", justify="left")
            btn.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            mode_grid.columnconfigure(c, weight=1)

    def _build_chat(self, p):
        self.ctx_bar = tk.Frame(p, bg=CARD, height=32)
        self.ctx_bar.pack(fill="x")
        self.ctx_bar.pack_propagate(False)
        self.ctx_lbl = tk.Label(self.ctx_bar,
                                 text="📚 No chapter loaded — pick one from the left",
                                 bg=CARD, fg=MUTED, font=SM, anchor="w")
        self.ctx_lbl.pack(side="left", padx=10, pady=6)

        self.chat_view = scrolledtext.ScrolledText(
            p, wrap="word", bg=PANEL, fg=TEXT, font=BODY,
            relief="flat", bd=0, state="disabled",
            padx=16, pady=12, spacing1=2, spacing3=4,
            selectbackground=ACCENT, selectforeground="white")
        self.chat_view.pack(fill="both", expand=True)

        t = self.chat_view
        t.tag_configure("u_lbl",  foreground=BLUE,   font=H3)
        t.tag_configure("a_lbl",  foreground=GREEN,  font=H3)
        t.tag_configure("u_txt",  foreground=TEXT,   font=BODY, lmargin1=14, lmargin2=14)
        t.tag_configure("a_txt",  foreground=TEXT,   font=BODY, lmargin1=14, lmargin2=14)
        t.tag_configure("h2",     foreground=BLUE,   font=H2,   lmargin1=14, lmargin2=14, spacing1=8, spacing3=4)
        t.tag_configure("h3",     foreground=TEXT,   font=H3,   lmargin1=14, lmargin2=14, spacing1=6, spacing3=2)
        t.tag_configure("code",   foreground=CODE_BLUE, font=MONO, background=CARD,
                                  lmargin1=18, lmargin2=18, spacing1=2)
        t.tag_configure("ascii",  foreground=GREEN, font=MONO, background=CARD,
                                  lmargin1=18, lmargin2=18, spacing1=4, spacing3=4)
        t.tag_configure("bold",   font=H3, foreground=TEXT)
        t.tag_configure("sep",    foreground=BORDER)
        t.tag_configure("hint",   foreground=WARN, font=SM)
        t.tag_configure("err",    foreground=ORANGE, font=BODY)
        t.tag_configure("ctx",    foreground=GREEN, font=SM, background=CARD,
                                  lmargin1=14, lmargin2=14)
        t.tag_configure("viz_lbl", foreground=BLUE, font=SM, lmargin1=14, lmargin2=14)

        self._show_chat_welcome()

        self.followup_row = tk.Frame(p, bg=BG, height=40)
        self.followup_row.pack(fill="x")
        self.followup_row.pack_propagate(False)

        # ── VARK Action Toolbar (above input)
        vark_bar = tk.Frame(p, bg=BG)
        vark_bar.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(vark_bar, text="🧠 VARK Tools:", bg=BG, fg=MUTED, font=SM).pack(side="left", padx=(0, 6))
        tk.Button(vark_bar, text="🎯 Discover My Style", bg=CARD, fg=ORANGE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=self._show_vark_quiz).pack(side="left", padx=2)
        tk.Button(vark_bar, text="🔊 Speak Response", bg=CARD, fg=GREEN, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=self._speak_response).pack(side="left", padx=2)
        tk.Button(vark_bar, text="📇 Export Anki", bg=CARD, fg=BLUE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=self._export_anki).pack(side="left", padx=2)
        tk.Button(vark_bar, text="🧠 Save Mindmap", bg=CARD, fg=BLUE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=self._export_mindmap).pack(side="left", padx=2)

        # Save toolbar — above input
        save_bar = tk.Frame(p, bg=BG)
        save_bar.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(save_bar, text="💾 Save notes:", bg=BG, fg=MUTED, font=SM).pack(side="left", padx=(0, 6))
        tk.Button(save_bar, text="Markdown (.md)", bg=CARD, fg=BLUE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=lambda: self._save_notes("md")).pack(side="left", padx=2)
        tk.Button(save_bar, text="Plain text (.txt)", bg=CARD, fg=BLUE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=lambda: self._save_notes("txt")).pack(side="left", padx=2)
        tk.Button(save_bar, text="HTML (.html)", bg=CARD, fg=BLUE, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=lambda: self._save_notes("html")).pack(side="left", padx=2)
        tk.Button(save_bar, text="📋 Copy All", bg=CARD, fg=GREEN, relief="flat",
                  font=SM, cursor="hand2", padx=10, pady=3,
                  command=self._copy_all).pack(side="left", padx=2)

        # ── Custom Instructions (collapsible) ────────────────────────────────
        ci_toggle_row = tk.Frame(p, bg=BG)
        ci_toggle_row.pack(fill="x", padx=10, pady=(2, 0))
        self._ci_btn = tk.Button(ci_toggle_row, text="📝 Custom instructions (off)",
                                  bg=CARD, fg=MUTED, font=SM, relief="flat", cursor="hand2",
                                  padx=10, pady=2, command=self._toggle_custom_instructions)
        self._ci_btn.pack(side="left")

        self._ci_frame = tk.Frame(p, bg=BG)
        tk.Label(self._ci_frame, text="Extra instructions added to every AI request:",
                 bg=BG, fg=MUTED, font=SM).pack(anchor="w", padx=10, pady=(4, 0))
        self._ci_box = scrolledtext.ScrolledText(
            self._ci_frame, wrap="word", bg=INPUT, fg=TEXT, font=SM,
            relief="flat", bd=0, height=2, insertbackground=TEXT, padx=8, pady=6)
        self._ci_box.pack(fill="x", padx=10, pady=(2, 4))
        self._ci_box.bind("<KeyRelease>", lambda e: self._update_custom_instructions())
        # hidden by default

        inp = tk.Frame(p, bg=BG)
        inp.pack(fill="x", padx=10, pady=4)

        self.input_box = scrolledtext.ScrolledText(inp, wrap="word", bg=INPUT, fg=TEXT,
                                                    font=BODY, relief="flat", bd=0, height=3,
                                                    insertbackground=TEXT, padx=10, pady=8)
        self.input_box.pack(side="left", fill="x", expand=True)
        self.input_box.bind("<Return>", self._on_enter)
        self.input_box.bind("<Shift-Return>", lambda e: None)

        bc = tk.Frame(inp, bg=BG)
        bc.pack(side="right", padx=(8, 0), fill="y")
        self.send_btn = tk.Button(bc, text="Send", bg=BLUE, fg=BG, font=H2,
                                   relief="flat", cursor="hand2", padx=14, pady=8,
                                   command=self._send_manual)
        self.send_btn.pack(fill="x")
        tk.Button(bc, text="Clear", bg=CARD, fg=MUTED, font=SM,
                  relief="flat", cursor="hand2", command=self._clear).pack(fill="x", pady=(4, 0))

    # ═══════════════════════════════════════════════════════════════════════════
    # PDF LOGIC
    # ═══════════════════════════════════════════════════════════════════════════
    def _open_pdf(self):
        # Start in HOME so user can navigate to wherever books are
        path = filedialog.askopenfilename(
            title="Select PDF Book",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialdir=os.path.expanduser("~"))
        if not path:
            return
        if not PDF_OK:
            messagebox.showerror("Missing Library", "Run: pip install pymupdf")
            return
        self._status("Parsing PDF...", WARN)
        self.active_pdf = path

        def go():
            try:
                toc, total = get_toc_and_total(path)
                chapters = build_chapter_list(toc, total)
                self.pdf_total = total
                self.chapters = chapters
                size_kb = os.path.getsize(path) // 1024
                name = os.path.basename(path)
                self.after(0, lambda: self._show_pdf(name, total, size_kb, chapters))
            except Exception as ex:
                self.after(0, lambda e=str(ex): messagebox.showerror("PDF Error", e))
                self.after(0, lambda: self._status("Error", ORANGE))
        threading.Thread(target=go, daemon=True).start()

    def _show_pdf(self, name, total, size_kb, chapters):
        self.pdf_info.config(
            text=f"📕 {name[:35]}{'...' if len(name)>35 else ''}\n{total} pages · {size_kb} KB",
            fg=TEXT)
        self.ch_header.config(text=f"Chapters / Sections  ({len(chapters)} found)")
        self.ch_list.delete(0, "end")
        for level, title, ps, pe in chapters:
            indent = "  " * (level - 1)
            self.ch_list.insert("end", f"{indent}{title[:55]}  (p.{ps}–{pe})")
        self._status(f"PDF loaded — click any chapter", GREEN)

        self._set_viewer_mode("text")
        self.text_view.config(state="normal")
        self.text_view.delete("1.0", "end")
        self.text_view.insert("end",
            f"Opened: {name}\n{total} pages · {len(chapters)} chapters detected\n\n"
            "👈 Click a chapter on the left to load it here.\n", "hint")
        self.text_view.config(state="disabled")

    def _on_chapter_click(self, event):
        sel = self.ch_list.curselection()
        if not sel or not self.chapters:
            return
        idx = sel[0]
        if idx >= len(self.chapters):
            return
        level, title, ps, pe = self.chapters[idx]
        self.selected_chapter = (title, ps, pe)
        self.pg_from.delete(0, "end"); self.pg_from.insert(0, str(ps))
        self.pg_to.delete(0, "end");   self.pg_to.insert(0, str(pe))
        self.sel_info.config(text=f"✓ {title}\nPages {ps}–{pe} ({pe-ps+1} pages)", fg=GREEN)
        self._load_chapter()

    def _manual_load(self):
        if not self.active_pdf:
            messagebox.showinfo("No PDF", "Open a PDF first.")
            return
        try:
            ps = max(1, int(self.pg_from.get()))
            pe = min(self.pdf_total, int(self.pg_to.get()))
        except ValueError:
            messagebox.showwarning("Bad Pages", "Enter valid page numbers.")
            return
        self.selected_chapter = (f"Pages {ps}–{pe}", ps, pe)
        self.sel_info.config(text=f"✓ Pages {ps}–{pe} ({pe-ps+1} pages)", fg=GREEN)
        self._load_chapter()

    def _load_chapter(self):
        if not self.selected_chapter or not self.active_pdf:
            return
        title, ps, pe = self.selected_chapter
        self._status(f"Loading {title[:30]}...", WARN)

        def go():
            try:
                text = extract_text(self.active_pdf, ps-1, pe)
                if not text:
                    self.after(0, lambda: messagebox.showwarning("Empty",
                        "No text extracted — PDF may be image-only."))
                    return
                self.pdf_text = text[:MAX_CTX]
                trunc = len(text) > MAX_CTX
                self.history = []
                self.current_view_page = ps - 1
                self.after(0, lambda: self._chapter_loaded(title, ps, pe, text, trunc))
            except Exception as ex:
                self.after(0, lambda e=str(ex): messagebox.showerror("Error", e))
                self.after(0, lambda: self._status("Error", ORANGE))
        threading.Thread(target=go, daemon=True).start()

    def _chapter_loaded(self, title, ps, pe, full_text, trunc):
        size_msg = f"{len(self.pdf_text):,} chars" + ("  [truncated to fit AI]" if trunc else "")
        self.ctx_lbl.config(
            text=f"📖 {title}  |  Pages {ps}–{pe}  |  {size_msg}",
            fg=GREEN if not trunc else WARN)

        self.text_view.config(state="normal")
        self.text_view.delete("1.0", "end")
        self.text_view.insert("end", full_text)
        self.text_view.config(state="disabled")
        self.text_view.yview_moveto(0)

        self._update_page_label()

        self.chat_view.config(state="normal")
        self.chat_view.delete("1.0", "end")
        self.chat_view.insert("end",
            f"✓ Loaded: {title}  (pages {ps}–{pe})\n"
            f"→ Click any teaching mode below the viewer — it runs automatically.\n"
            f"→ Or type your own question and press Send.\n\n", "ctx")
        self.chat_view.config(state="disabled")
        self._status("Ready — pick a teaching mode", GREEN)

    # ── PDF VIEWER MODES ──────────────────────────────────────────────────────
    def _set_viewer_mode(self, mode):
        self.viewer_mode = mode
        if mode == "text":
            self.image_view.pack_forget()
            self.img_scroll_y.pack_forget()
            self.text_view.pack(fill="both", expand=True)
            self.view_text_btn.config(bg=ACCENT, fg="white")
            self.view_img_btn.config(bg=CARD, fg=MUTED)
        else:
            self.text_view.pack_forget()
            self.image_view.pack(side="left", fill="both", expand=True)
            self.img_scroll_y.pack(side="right", fill="y")
            self.view_img_btn.config(bg=ACCENT, fg="white")
            self.view_text_btn.config(bg=CARD, fg=MUTED)
            self.after(100, self._render_current_page)

    def _on_canvas_resize(self):
        # Skip if window isn't ready yet
        if not hasattr(self, "image_view") or not self.winfo_exists():
            return
        if hasattr(self, "_resize_timer"):
            try:
                self.after_cancel(self._resize_timer)
            except Exception:
                pass
        if self.viewer_mode == "image" and self.active_pdf:
            self._resize_timer = self.after(250, self._render_current_page)

    def _render_current_page(self):
        if not self.active_pdf:
            self.image_view.delete("all")
            self.image_view.create_text(250, 100,
                text="No PDF loaded.\nClick 'Open PDF Book' on the left.",
                fill=WARN, font=BODY, anchor="n")
            return
        if not PIL_OK:
            self.image_view.delete("all")
            self.image_view.create_text(250, 100,
                text="Pillow not installed.\n\nRun: pip install pillow\nthen restart.",
                fill=ORANGE, font=BODY, anchor="n")
            return
        self.image_view.update_idletasks()
        try:
            img = render_page_image(self.active_pdf, self.current_view_page, zoom=2.0)
            orig_w, orig_h = img.width, img.height
            cw = self.image_view.winfo_width()
            if cw < 50:
                cw = max(self.winfo_width() // 3, 500)
            if orig_w > cw:
                scale = cw / orig_w
                img = img.resize((cw, int(orig_h * scale)), Image.LANCZOS)
            # Pass master=self.image_view to anchor to a specific Tk widget
            photo = ImageTk.PhotoImage(img, master=self.image_view)
            self.page_images = [photo]
            self.image_view.delete("all")
            self.image_view.create_image(0, 0, anchor="nw", image=photo)
            self.image_view.config(scrollregion=(0, 0, img.width, img.height))
            self._update_page_label()
        except Exception as e:
            import traceback
            self.image_view.delete("all")
            self.image_view.create_text(250, 100,
                text=f"Render error:\n{e}\n\n{traceback.format_exc()[:300]}",
                fill=ORANGE, font=SM, anchor="n", width=500)

    def _update_page_label(self):
        if self.active_pdf and self.pdf_total > 0:
            self.page_lbl.config(text=f"p.{self.current_view_page+1}/{self.pdf_total}")
        else:
            self.page_lbl.config(text="—")

    def _prev_page(self):
        if self.active_pdf and self.current_view_page > 0:
            self.current_view_page -= 1
            if self.viewer_mode == "image":
                self._render_current_page()
            self._update_page_label()

    def _next_page(self):
        if self.active_pdf and self.current_view_page < self.pdf_total - 1:
            self.current_view_page += 1
            if self.viewer_mode == "image":
                self._render_current_page()
            self._update_page_label()

    def _show_viewer_welcome(self):
        self.text_view.config(state="normal")
        self.text_view.delete("1.0", "end")
        self.text_view.insert("1.0",
            "📖 PDF Viewer\n\n"
            "1. Click 'Open PDF Book' on the left\n"
            "2. Click a chapter → its text appears here\n"
            "3. Toggle 'Page Image' to see actual PDF pages\n"
            "4. Click a teaching mode below → AI explains with diagrams\n", "hint")
        self.text_view.config(state="disabled")

    def _show_chat_welcome(self):
        self.chat_view.config(state="normal")
        msg = ("💬 AI Chat — v5.0 with Visual Output\n\n"
               "Workflow:\n"
               "  1. Open a PDF (left panel)\n"
               "  2. Click any chapter — auto-loads as AI context\n"
               "  3. Click a teaching mode — runs instantly with diagrams/charts\n"
               "  4. Click follow-up chips or type your own question\n\n"
               "Visuals supported:\n"
               f"  📐 ASCII diagrams (always)\n"
               f"  🔗 Mermaid diagrams ({'✓ ready' if MERMAID_OK else 'install: npm i -g @mermaid-js/mermaid-cli'})\n"
               f"  📊 Matplotlib charts ({'✓ ready' if MPL_OK else 'install: pip install matplotlib'})\n")
        self.chat_view.insert("1.0", msg, "hint")
        self.chat_view.config(state="disabled")

    # ═══════════════════════════════════════════════════════════════════════════
    # TEACHING MODE EXECUTION
    # ═══════════════════════════════════════════════════════════════════════════
    def _run_mode(self, mode_name):
        if not self.pdf_text:
            messagebox.showinfo("No Chapter Loaded",
                                 "Open a PDF and click a chapter first.")
            return
        cfg = MODES[mode_name]
        self._do_chat(cfg["user"], cfg["sys"], cfg["followups"],
                       mode_label=f"{cfg['icon']} {mode_name}")

    def _send_manual(self):
        txt = self.input_box.get("1.0", "end").strip()
        if not txt or self.is_busy:
            return
        self.input_box.delete("1.0", "end")
        sys_p = (MODES["Explain in Depth"]["sys"] if self.pdf_text else
                 "You are a knowledgeable tutor. Answer the user's question clearly and accurately in whatever "
                 "field it concerns, with concrete examples and a diagram where it helps." + VISUAL_INSTRUCTIONS)
        self._do_chat(txt, sys_p, [])

    def _on_enter(self, event):
        if not (event.state & 0x1):
            self._send_manual()
            return "break"

    def _do_chat(self, user_text, sys_prompt, followups, mode_label=None):
        if self.is_busy:
            return
        pname = self.pv.get()
        prov  = PROVIDERS[pname]
        model = self.mv.get()
        key   = self._get_clean_key() if prov["needs_key"] else ""
        if prov["needs_key"] and not key:
            messagebox.showwarning("API Key", f"Enter your API key for {pname}.")
            return

        # Cap content based on provider limits
        # Gemini: 1M tokens — no need to truncate even big chapters
        # Groq/OpenRouter: ~12K total per request — must cap PDF chunk
        pdf_chunk = self.pdf_text
        if prov["id"] in ("groq", "openrouter"):
            if len(pdf_chunk) > 5000:
                pdf_chunk = pdf_chunk[:5000] + "\n[Content truncated. Load smaller pages for full coverage.]"
        # Gemini and Ollama get full content (Gemini: 1M ctx, Ollama: local no limits)
        content = (f"[PDF CONTENT]\n{pdf_chunk}\n\n[QUESTION]\n{user_text}"
                   if self.pdf_text else user_text)

        self._clear_followups()

        display_label = mode_label or "You"
        prov_short = pname.split("(")[0].strip()
        self._append("u_lbl", f"\n👤 {display_label}\n")
        self._append("u_txt", user_text + "\n")
        self._append("a_lbl", f"\n🤖 AI Teacher  ({prov_short} · {model})\n")
        self._append("hint", "   Generating with diagrams...\n")

        self.history.append({"role": "user", "content": content})
        self.is_busy = True
        self._first_chunk = True
        self._response_buffer = ""
        self.send_btn.config(state="disabled", text="...")
        self._status(f"Generating with {model}...", WARN)

        def run():
            try:
                effective_sys = sys_prompt
                # Local 7-8B models need strict structure; cloud models do better
                # with the open-ended prompts, so only Ollama gets the extra rules.
                if prov["id"] == "ollama":
                    effective_sys += LOCAL_GUIDANCE
                if self._custom_instructions:
                    effective_sys += f"\n\nADDITIONAL USER INSTRUCTIONS:\n{self._custom_instructions}"
                history_size = 2 if prov["id"] in ("groq", "openrouter") else 6
                msgs = [{"role": "system", "content": effective_sys}] + self.history[-history_size:]
                full = self.client.chat(prov["id"], model, key, msgs,
                                         lambda c: self.after(0, lambda x=c: self._chunk(x)))
                self.history.append({"role": "assistant", "content": full})
                self.after(0, lambda: self._done(full, followups))
            except Exception as ex:
                self.after(0, lambda e=str(ex): self._err(e))

        threading.Thread(target=run, daemon=True).start()

    def _chunk(self, chunk):
        self.chat_view.config(state="normal")
        if self._first_chunk:
            pos = self.chat_view.search("Generating with diagrams...", "1.0", "end")
            if pos:
                self.chat_view.delete(pos, f"{pos}+30c")
            self._first_chunk = False
        self._response_buffer += chunk
        self._format_chunk(chunk)
        self.chat_view.config(state="disabled")
        self.chat_view.see("end")

    def _format_chunk(self, text):
        """Streaming formatter — just text. Visuals rendered AFTER stream completes."""
        # During streaming we keep it simple: just append as plain text with markdown bold/headings
        lines = text.split("\n")
        for i, line in enumerate(lines):
            nl = "\n" if i < len(lines) - 1 else ""
            if line.startswith("## "):
                self.chat_view.insert("end", line[3:] + nl, "h2")
            elif line.startswith("### "):
                self.chat_view.insert("end", line[4:] + nl, "h3")
            else:
                parts = re.split(r'(\*\*[^*]+\*\*)', line)
                for p in parts:
                    if p.startswith("**") and p.endswith("**"):
                        self.chat_view.insert("end", p[2:-2], "bold")
                    else:
                        self.chat_view.insert("end", p, "a_txt")
                self.chat_view.insert("end", nl, "a_txt")

    def _done(self, full_response, followups):
        """After streaming finishes — find and replace fenced blocks with proper renderings."""
        self._first_chunk = True
        # Clear and re-render the full message with proper visual blocks
        self._rerender_last_response(full_response)

        self.chat_view.config(state="normal")
        self.chat_view.insert("end", "\n" + "─" * 70 + "\n", "sep")
        self.chat_view.config(state="disabled")
        self.chat_view.see("end")
        self.is_busy = False
        self.send_btn.config(state="normal", text="Send")
        self._status("Ready", GREEN)
        if followups:
            self._show_followups(followups)

    def _rerender_last_response(self, full):
        """Find the last AI response in the chat and replace with formatted version including visuals."""
        # Find where the last "🤖 AI Teacher" label is
        try:
            self.chat_view.config(state="normal")
            label_pos = self.chat_view.search("🤖 AI Teacher", "1.0", "end", backwards=False)
            # Find the LAST occurrence
            last_pos = "1.0"
            while True:
                p = self.chat_view.search("🤖 AI Teacher", last_pos, "end")
                if not p:
                    break
                label_pos = p
                last_pos = f"{p}+1c"
            if not label_pos:
                self.chat_view.config(state="disabled")
                return
            # Go to end of line after label
            content_start = self.chat_view.index(f"{label_pos} lineend +1c")
            # Delete everything from content_start to end
            self.chat_view.delete(content_start, "end")
            # Re-render with visual blocks
            self._render_formatted_response(full)
            self.chat_view.config(state="disabled")
            self.chat_view.see("end")
        except Exception:
            self.chat_view.config(state="disabled")

    def _render_formatted_response(self, text):
        """Robust parser: detects fenced blocks AND naked mermaid/chart blocks AI may produce without fences."""
        blocks = self._extract_all_blocks(text)
        # Render each block in order
        for kind, content in blocks:
            if kind == "text":
                if content.strip():
                    self._render_plain_markdown(content)
            elif kind == "mermaid":
                self._render_mermaid_block(content)
            elif kind == "chart":
                self._render_chart_block(content)
            elif kind == "ascii":
                self.chat_view.insert("end", "\n" + content + "\n\n", "ascii")
            elif kind == "code":
                self.chat_view.insert("end", "\n" + content + "\n\n", "code")

    def _extract_all_blocks(self, text):
        """
        Extract sequence of blocks: [(kind, content), ...]
        kind in {text, mermaid, chart, ascii, code}
        Handles: fenced ```mermaid```, naked 'mermaid\nflowchart...', tables, ASCII art.
        """
        blocks = []
        # First pass: split by fenced code blocks (```lang ... ```)
        fenced_pattern = re.compile(r'```([a-zA-Z]+)?\n?([\s\S]*?)```', re.MULTILINE)
        last_end = 0
        for m in fenced_pattern.finditer(text):
            before = text[last_end:m.start()]
            if before:
                # Process the "before" segment for naked blocks
                blocks.extend(self._extract_naked_blocks(before))
            lang = (m.group(1) or "").lower().strip()
            body = m.group(2).rstrip()
            if lang == "mermaid" or self._looks_like_mermaid(body):
                blocks.append(("mermaid", body))
            elif lang == "chart":
                blocks.append(("chart", body))
            elif lang == "ascii" or self._looks_like_ascii_diagram(body):
                blocks.append(("ascii", body))
            else:
                blocks.append(("code", body))
            last_end = m.end()
        # Tail
        tail = text[last_end:]
        if tail:
            blocks.extend(self._extract_naked_blocks(tail))
        return blocks

    def _extract_naked_blocks(self, text):
        """Find mermaid/chart blocks that the AI forgot to fence. Also finds ASCII boxes."""
        blocks = []
        lines = text.split("\n")
        i = 0
        buf = []   # accumulating plain text lines

        def flush_text():
            nonlocal buf
            if buf:
                blocks.append(("text", "\n".join(buf)))
                buf = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip().lower()

            # Detect naked mermaid block: line is "mermaid" or starts with mermaid keyword
            if stripped in ("mermaid",) or re.match(r'^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie)\s', line.strip()):
                # Found start of naked mermaid block
                # Collect lines until we hit a blank line followed by non-indented text, or until end
                mmd_lines = []
                if stripped == "mermaid":
                    i += 1  # skip the "mermaid" header line
                    if i < len(lines) and re.match(r'^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie)', lines[i].strip()):
                        mmd_lines.append(lines[i])
                        i += 1
                    else:
                        # False alarm - treat as text
                        buf.append(line)
                        continue
                else:
                    mmd_lines.append(line)
                    i += 1
                # Collect mermaid content lines (typically indented or arrows)
                while i < len(lines):
                    nxt = lines[i]
                    nxt_strip = nxt.strip()
                    # Mermaid lines are typically: indented, contain -->, --, :, [, (, end keyword
                    if (not nxt_strip or
                        nxt.startswith((" ", "\t")) or
                        any(t in nxt_strip for t in ["-->", "---", "->", "--", "==>", "|>"]) or
                        nxt_strip in ("end",) or
                        re.match(r'^[A-Za-z0-9_]+\[.*\]', nxt_strip) or
                        re.match(r'^[A-Za-z0-9_]+\(.*\)', nxt_strip) or
                        re.match(r'^subgraph\s', nxt_strip) or
                        re.match(r'^\w+\s*:\s*', nxt_strip)):
                        mmd_lines.append(nxt)
                        i += 1
                    else:
                        break
                # Trim trailing blank lines
                while mmd_lines and not mmd_lines[-1].strip():
                    mmd_lines.pop()
                if len(mmd_lines) >= 2:
                    flush_text()
                    blocks.append(("mermaid", "\n".join(mmd_lines)))
                    continue
                else:
                    # Not enough content, put back as text
                    buf.extend(mmd_lines)
                    continue

            # Detect ASCII box diagrams (consecutive lines with box chars)
            if self._looks_like_ascii_diagram(line) and i + 1 < len(lines):
                ascii_lines = [line]
                j = i + 1
                while j < len(lines) and (self._looks_like_ascii_diagram(lines[j]) or
                                          (lines[j].strip() and any(c in lines[j] for c in "│|"))):
                    ascii_lines.append(lines[j])
                    j += 1
                if len(ascii_lines) >= 2:
                    flush_text()
                    blocks.append(("ascii", "\n".join(ascii_lines)))
                    i = j
                    continue

            buf.append(line)
            i += 1
        flush_text()
        return blocks

    def _looks_like_mermaid(self, code):
        return bool(re.match(r'^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie)\s',
                              code, re.MULTILINE))

    def _looks_like_ascii_diagram(self, code):
        return any(ch in code for ch in "┌┐└┘├┤┬┴┼─│║═╔╗╚╝▶◀▲▼")

    def _render_plain_markdown(self, text):
        for seg in re.split(r'(`[^`]+`)', text):
            if seg.startswith("`") and seg.endswith("`") and len(seg) > 2:
                self.chat_view.insert("end", seg[1:-1], "code")
            else:
                lines = seg.split("\n")
                for i, line in enumerate(lines):
                    nl = "\n" if i < len(lines) - 1 else ""
                    if line.startswith("## "):
                        self.chat_view.insert("end", line[3:] + nl, "h2")
                    elif line.startswith("### "):
                        self.chat_view.insert("end", line[4:] + nl, "h3")
                    else:
                        parts = re.split(r'(\*\*[^*]+\*\*)', line)
                        for p in parts:
                            if p.startswith("**") and p.endswith("**"):
                                self.chat_view.insert("end", p[2:-2], "bold")
                            else:
                                self.chat_view.insert("end", p, "a_txt")
                        self.chat_view.insert("end", nl, "a_txt")

    def _render_mermaid_block(self, code):
        """Render mermaid block. Always show code as fallback on failure."""
        is_mm = code.strip().lower().startswith("mindmap")
        label = "\n🧠 Mind Map:\n" if is_mm else "\n🔗 Mermaid Diagram:\n"
        self.chat_view.insert("end", label, "viz_lbl")
        if not PIL_OK:
            self.chat_view.insert("end",
                "(Pillow not installed — paste at mermaid.live)\n\n", "hint")
            self.chat_view.insert("end", code + "\n\n", "code")
            return
        placeholder_idx = self.chat_view.index("end")
        self.chat_view.insert("end", "  ⏳ rendering...\n", "hint")

        def do_render():
            # Bulletproof: any failure must still clear the placeholder,
            # otherwise the UI hangs forever on "⏳ rendering...".
            try:
                img, err = render_mermaid_to_image(code)
            except Exception as e:
                img, err = None, str(e)
            if img:
                self.after(0, lambda: self._insert_image_at(placeholder_idx, img))
            else:
                msg = (f"  (Diagram couldn't render — showing source. "
                       f"Install graphviz for offline rendering.)\n\n{code}\n\n")
                self.after(0, lambda: self._insert_text_at(placeholder_idx, msg, "code"))
        threading.Thread(target=do_render, daemon=True).start()

    def _render_chart_block(self, spec):
        self.chat_view.insert("end", "\n📊 Chart:\n", "viz_lbl")
        if not MPL_OK:
            self.chat_view.insert("end", spec + "\n[install matplotlib to render]\n\n", "code")
            return
        placeholder_idx = self.chat_view.index("end")
        self.chat_view.insert("end", "  ⏳ rendering chart...\n\n", "hint")
        def do_render():
            img = render_chart_to_image(spec)
            if img:
                self.after(0, lambda: self._insert_image_at(placeholder_idx, img))
            else:
                self.after(0, lambda: self._insert_text_at(placeholder_idx,
                    "  [Chart render failed — showing spec]\n" + spec + "\n\n", "code"))
        threading.Thread(target=do_render, daemon=True).start()

    def _insert_image_at(self, idx, pil_img):
        try:
            self.chat_view.config(state="normal")
            try:
                placeholder = self.chat_view.search("⏳ rendering", idx, "end")
                if placeholder:
                    line_end = self.chat_view.index(f"{placeholder} lineend +1c")
                    self.chat_view.delete(placeholder, line_end)
            except Exception:
                pass

            max_w = max(self.chat_view.winfo_width() - 80, 400)
            display = pil_img
            if pil_img.width > max_w:
                scale = max_w / pil_img.width
                display = pil_img.resize((max_w, int(pil_img.height * scale)), Image.LANCZOS)

            photo = ImageTk.PhotoImage(display, master=self.chat_view)
            self._chat_image_refs.append(photo)
            self._chat_pil_refs.append(pil_img)

            # Embed as a clickable frame so we can attach zoom/copy buttons
            frame = tk.Frame(self.chat_view, bg=PANEL)
            img_lbl = tk.Label(frame, image=photo, bg=PANEL, cursor="hand2")
            img_lbl.pack(padx=14, pady=(8, 2))
            img_lbl.bind("<Button-1>", lambda e, img=pil_img: self._open_diagram_viewer(img))

            btn_row = tk.Frame(frame, bg=PANEL)
            btn_row.pack(fill="x", padx=14, pady=(0, 6))
            tk.Button(btn_row, text="🔍 Zoom / Copy", bg=CARD, fg=BLUE, relief="flat",
                      font=SM, cursor="hand2", padx=8, pady=2,
                      command=lambda img=pil_img: self._open_diagram_viewer(img)).pack(side="left", padx=2)
            tk.Button(btn_row, text="💾 Save PNG", bg=CARD, fg=GREEN, relief="flat",
                      font=SM, cursor="hand2", padx=8, pady=2,
                      command=lambda img=pil_img: self._save_diagram_png(img)).pack(side="left", padx=2)
            tk.Label(btn_row, text="← click image or button to zoom",
                     bg=PANEL, fg=MUTED, font=SM).pack(side="left", padx=8)

            self.chat_view.window_create(idx, window=frame)
            self.chat_view.insert(f"{idx} +1c", "\n", "a_txt")
            self.chat_view.config(state="disabled")
            self.chat_view.see("end")
        except Exception as e:
            self._insert_text_at(idx, f"[Image insert error: {e}]\n\n", "err")

    def _insert_text_at(self, idx, text, tag):
        self.chat_view.config(state="normal")
        try:
            placeholder = self.chat_view.search("⏳ rendering", idx, "end")
            if placeholder:
                line_end = self.chat_view.index(f"{placeholder} lineend +1c")
                self.chat_view.delete(placeholder, line_end)
        except Exception:
            pass
        self.chat_view.insert(idx, text, tag)
        self.chat_view.config(state="disabled")

    def _save_diagram_png(self, pil_img):
        path = filedialog.asksaveasfilename(
            title="Save Diagram as PNG", defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if path:
            pil_img.save(path)
            self._status("Diagram saved", GREEN)

    def _open_diagram_viewer(self, pil_img):
        """Zoomable popup viewer — click image or Zoom/Copy button to open."""
        if Image is None:
            return
        win = tk.Toplevel(self)
        win.title("Diagram Viewer")
        win.configure(bg=BG)
        win.geometry("920x680")
        win.resizable(True, True)

        zoom = [1.0]
        photo_ref = [None]

        toolbar = tk.Frame(win, bg=BG, pady=6)
        toolbar.pack(fill="x", padx=10, side="top")
        zoom_lbl = tk.Label(toolbar, text="100%", bg=BG, fg=MUTED, font=SM, width=6)

        cf = tk.Frame(win, bg=BG)
        cf.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        canvas = tk.Canvas(cf, bg="#1a1a2e", highlightthickness=0)
        hbar = tk.Scrollbar(cf, orient="horizontal", command=canvas.xview)
        vbar = tk.Scrollbar(cf, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        canvas.pack(fill="both", expand=True)

        def redraw():
            z = zoom[0]
            w = max(1, int(pil_img.width * z))
            h = max(1, int(pil_img.height * z))
            resized = pil_img.resize((w, h), Image.LANCZOS)
            photo_ref[0] = ImageTk.PhotoImage(resized, master=win)
            canvas.delete("all")
            canvas.create_image(4, 4, anchor="nw", image=photo_ref[0])
            canvas.configure(scrollregion=(0, 0, w + 8, h + 8))
            zoom_lbl.config(text=f"{int(z * 100)}%")

        def zoom_in(*_):
            zoom[0] = min(zoom[0] * 1.2, 6.0); redraw()

        def zoom_out(*_):
            zoom[0] = max(zoom[0] / 1.2, 0.1); redraw()

        def zoom_fit():
            win.update_idletasks()
            cw = canvas.winfo_width() - 16
            ch = canvas.winfo_height() - 16
            if cw > 10 and ch > 10:
                zoom[0] = min(cw / pil_img.width, ch / pil_img.height, 1.0)
                redraw()

        def save_png():
            path = filedialog.asksaveasfilename(
                parent=win, title="Save Diagram as PNG",
                defaultextension=".png",
                filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
            if path:
                pil_img.save(path)
                self._status("Diagram saved", GREEN)

        def copy_image():
            import subprocess, tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            pil_img.save(tmp.name)
            try:
                subprocess.run(["xclip", "-selection", "clipboard",
                                "-t", "image/png", "-i", tmp.name],
                               check=True, capture_output=True)
                self._status("Diagram copied to clipboard", GREEN)
            except (FileNotFoundError, subprocess.CalledProcessError):
                try:
                    subprocess.run(["xdg-open", tmp.name], capture_output=True)
                    self._status(f"Opened in viewer (install xclip for clipboard)", WARN)
                except Exception:
                    messagebox.showinfo("Save location", f"PNG saved to:\n{tmp.name}", parent=win)

        for text, cmd, fg in [
            ("🔍 Zoom In",  zoom_in,  GREEN),
            ("🔎 Zoom Out", zoom_out, GREEN),
            ("⊡ Fit",       zoom_fit, BLUE),
            ("💾 Save PNG", save_png, BLUE),
            ("📋 Copy",     copy_image, ORANGE),
        ]:
            tk.Button(toolbar, text=text, command=cmd, bg=CARD, fg=fg,
                      font=SM, relief="flat", padx=10, pady=3,
                      cursor="hand2").pack(side="left", padx=2)
        zoom_lbl.pack(side="left", padx=12)

        canvas.bind("<MouseWheel>", lambda e: zoom_in() if e.delta > 0 else zoom_out())
        canvas.bind("<Button-4>", zoom_in)
        canvas.bind("<Button-5>", zoom_out)

        redraw()
        win.after(150, zoom_fit)

    def _toggle_custom_instructions(self):
        if self._ci_frame.winfo_ismapped():
            self._ci_frame.pack_forget()
            self._ci_btn.config(text="📝 Custom instructions (off)", fg=MUTED)
        else:
            self._ci_frame.pack(fill="x", padx=0, pady=(0, 2))
            self._ci_btn.config(text="📝 Custom instructions (on)", fg=ORANGE)

    def _update_custom_instructions(self):
        self._custom_instructions = self._ci_box.get("1.0", "end").strip()

    def _show_followups(self, followups):
        self._clear_followups()
        self._followup_frame = tk.Frame(self.followup_row, bg=BG)
        self._followup_frame.pack(fill="x", padx=8, pady=4)
        tk.Label(self._followup_frame, text="Ask:", bg=BG, fg=MUTED, font=SM).pack(side="left", padx=(2, 6))
        for fu in followups[:4]:
            short = fu if len(fu) <= 40 else fu[:37] + "..."
            tk.Button(self._followup_frame, text=short, bg=CARD, fg=BLUE,
                      relief="flat", font=SM, cursor="hand2", padx=8, pady=3,
                      command=lambda q=fu: self._do_chat(
                          q, MODES["Explain in Depth"]["sys"], MODES["Explain in Depth"]["followups"])
            ).pack(side="left", padx=2)

    def _clear_followups(self):
        if self._followup_frame:
            self._followup_frame.destroy()
            self._followup_frame = None

    def _err(self, err):
        self._first_chunk = True
        self.chat_view.config(state="normal")
        pos = self.chat_view.search("Generating with diagrams...", "1.0", "end")
        if pos:
            self.chat_view.delete(pos, f"{pos}+30c")
        self.chat_view.insert("end", f"\n❌ Error: {err}\n\n", "err")
        self.chat_view.config(state="disabled")
        self.is_busy = False
        self.send_btn.config(state="normal", text="Send")
        self._status("Error", ORANGE)

    def _append(self, tag, text):
        self.chat_view.config(state="normal")
        self.chat_view.insert("end", text, tag)
        self.chat_view.config(state="disabled")
        self.chat_view.see("end")

    def _clear(self):
        self.history = []
        self._chat_image_refs = []
        self.chat_view.config(state="normal")
        self.chat_view.delete("1.0", "end")
        self.chat_view.config(state="disabled")
        self._clear_followups()
        if self.pdf_text and self.selected_chapter:
            title, ps, pe = self.selected_chapter
            self.chat_view.config(state="normal")
            self.chat_view.insert("end",
                f"Chat cleared. Chapter '{title}' is still loaded.\n\n", "ctx")
            self.chat_view.config(state="disabled")
        else:
            self._show_chat_welcome()

    def _get_clean_key(self):
        """Returns the API key with ALL whitespace and invisible characters removed."""
        raw = self.ke.get()
        # Keep ONLY ASCII alphanumeric, underscore, and hyphen — Groq keys use only these
        clean = ''.join(c for c in raw if c.isascii() and (c.isalnum() or c in '_-'))
        # Debug print — shows length, first 4, last 4, and any chars removed
        return clean

    def _refresh_models(self):
        """Query the live API for current models. Works for Groq, OpenRouter, AND Ollama."""
        name = self.pv.get()
        prov = PROVIDERS.get(name, {})
        # Special: Ollama queries local API directly, no key needed
        if prov.get("id") == "ollama":
            self._status("Querying Ollama...", WARN)
            def go_ollama():
                try:
                    req = urllib.request.Request("http://localhost:11434/api/tags")
                    with urllib.request.urlopen(req, timeout=5) as r:
                        data = json.loads(r.read().decode())
                    models = sorted([m["name"] for m in data.get("models", [])])
                    if models:
                        self.after(0, lambda: self._apply_live_models(models))
                    else:
                        self.after(0, lambda: messagebox.showinfo(
                            "No Models",
                            "Ollama running but no models installed.\nRun: ollama pull qwen2.5-coder:7b"))
                        self.after(0, lambda: self._status("Ready", GREEN))
                except Exception as ex:
                    self.after(0, lambda e=str(ex): messagebox.showerror(
                        "Ollama Not Reachable",
                        f"Could not query Ollama:\n{e}\n\nFix: start Ollama ('ollama serve', or on Linux: sudo systemctl start ollama)."))
                    self.after(0, lambda: self._status("Error", ORANGE))
            threading.Thread(target=go_ollama, daemon=True).start()
            return
        # Cloud providers need a key
        key = self._get_clean_key()
        if not key:
            messagebox.showwarning("API Key Needed",
                                    "Paste your API key first, then click ↻ to fetch the live model list.")
            return
        self._status("Fetching models...", WARN)

        def go():
            try:
                if prov["id"] == "groq":
                    url = "https://api.groq.com/openai/v1/models"
                    headers = {"Authorization": f"Bearer {key}",
                               "User-Agent": "curl/8.0", "Accept": "*/*"}
                elif prov["id"] == "openrouter":
                    url = "https://openrouter.ai/api/v1/models"
                    headers = {"Authorization": f"Bearer {key}",
                               "User-Agent": "curl/8.0", "Accept": "*/*"}
                elif prov["id"] == "gemini":
                    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                    headers = {"User-Agent": "curl/8.0"}
                else:
                    return
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode())
                if prov["id"] == "gemini":
                    # Gemini returns different shape: {"models": [{"name": "models/gemini-..."}, ...]}
                    models = sorted([m["name"].replace("models/", "")
                                      for m in data.get("models", [])
                                      if "generateContent" in m.get("supportedGenerationMethods", [])])
                else:
                    models = sorted([m["id"] for m in data.get("data", [])])
                if not models:
                    self.after(0, lambda: messagebox.showinfo("No Models", "API returned no models."))
                    self.after(0, lambda: self._status("Ready", GREEN))
                    return
                self.after(0, lambda: self._apply_live_models(models))
            except urllib.error.HTTPError as he:
                try:
                    body = he.read().decode()[:300]
                except Exception:
                    body = ""
                err_msg = f"HTTP {he.code}: {he.reason}\n\nServer response:\n{body}\n\nKey length: {len(key)} chars"
                self.after(0, lambda m=err_msg: messagebox.showerror("Fetch Failed", m))
                self.after(0, lambda: self._status("Error", ORANGE))
            except Exception as ex:
                self.after(0, lambda e=str(ex): messagebox.showerror(
                    "Fetch Failed", f"Could not fetch models:\n{e}\n\nKey length: {len(key)}"))
                self.after(0, lambda: self._status("Error", ORANGE))

        threading.Thread(target=go, daemon=True).start()

    def _apply_live_models(self, models):
        self.mcb["values"] = models
        # Keep current selection if it exists in new list, else pick first
        cur = self.mv.get()
        if cur not in models:
            self.mv.set(models[0])
            self.mcb.set(models[0])
        self._status(f"Got {len(models)} live models", GREEN)
        self._update_active_badge()

    def _apply_provider(self, *args):
        """Called on dropdown change AND on startup. Force-refresh model + key + badge."""
        # Read directly from the widget, not the StringVar (more reliable on Linux)
        name_from_widget = self.pcb.get()
        name_from_var = self.pv.get()
        # Prefer the widget value since user just clicked it
        name = name_from_widget or name_from_var
        if name not in PROVIDERS:
            return
        # Make sure var matches widget
        if name_from_var != name:
            self.pv.set(name)
        prov = PROVIDERS[name]

        # Force-reset model combobox (use both methods to be sure)
        self.mcb["values"] = prov["models"]
        self.mcb.set(prov["models"][0])
        self.mv.set(prov["models"][0])
        self.mcb.update_idletasks()

        # Key entry always editable
        self.ke.config(state="normal", bg=INPUT, fg=TEXT)
        if prov["needs_key"]:
            self.pnote.config(
                text=f"⚠  {prov['note']}\nPaste your API key in the box above.",
                fg=WARN)
        else:
            self.pnote.config(
                text=f"✓  {prov['note']}\n(No API key needed for local Ollama)",
                fg=GREEN)

        # Hook model-change events once (fires on click, typing, focus-out)
        if not hasattr(self, "_mv_traced"):
            self.mcb.bind("<<ComboboxSelected>>", self._update_active_badge)
            self.mcb.bind("<KeyRelease>",         self._update_active_badge)
            self.mcb.bind("<FocusOut>",            self._update_active_badge)
            self._mv_traced = True

        # Force badge refresh on next tick to ensure all widgets are settled
        self.after(50, self._update_active_badge)

    def _update_active_badge(self, *args):
        if not hasattr(self, "active_ai_badge"):
            return
        name = self.pv.get()
        # Always read from widget first (catches typed values), fall back to var
        widget_model = self.mcb.get() if hasattr(self, "mcb") else ""
        var_model = self.mv.get() if hasattr(self, "mv") else ""
        model = widget_model or var_model or "—"
        # Sync var if widget value is fresher
        if widget_model and widget_model != var_model:
            self.mv.set(widget_model)
        prov = PROVIDERS.get(name, {})
        short = name.split("(")[0].strip() if name else "?"
        if prov.get("needs_key"):
            key_set = bool(self._get_clean_key())
            color = GREEN if key_set else ORANGE
            status = "✓ key set" if key_set else "⚠ no key"
            new_text = f"  🤖 {short}  ·  {model}  ·  {status}  "
        else:
            color = GREEN
            new_text = f"  🤖 {short}  ·  {model}  "
        self.active_ai_badge.config(text=new_text, fg=color)
        self.active_ai_badge.update_idletasks()

    def _status(self, text, color):
        self.status_lbl.config(text=f"● {text}", fg=color)

    # ── SAVE / EXPORT METHODS ─────────────────────────────────────────────
    def _get_chat_text(self):
        """Return only AI response content — strips all UI chrome."""
        raw = self.chat_view.get("1.0", "end")

        # Split into segments on every AI Teacher marker
        ai_marker  = "🤖 AI Teacher"
        usr_marker = "👤"
        segments = raw.split(ai_marker)

        collected = []
        for seg in segments[1:]:  # skip everything before first AI response
            # Cut off at the next user message if present
            cut = seg.find(usr_marker)
            if cut > 0:
                seg = seg[:cut]
            # Strip the inline provider badge "(Google Gemini · model)" on first line
            lines = seg.splitlines()
            if lines and re.match(r'^\s*\(.*\)\s*$', lines[0]):
                lines = lines[1:]
            seg = "\n".join(lines).strip()
            if seg:
                collected.append(seg)

        if not collected:
            # Fallback: return raw minus obvious UI-only lines
            lines = raw.splitlines()
            clean = [l for l in lines if not re.match(
                r'^\s*[✓▶ℹ⚠✗]\s|^(Ask:|VARK Tools:|Save notes:)', l)]
            return "\n".join(clean).strip()

        return "\n\n---\n\n".join(collected)

    def _copy_all(self):
        """Copy entire chat to system clipboard."""
        text = self._get_chat_text()
        if not text:
            messagebox.showinfo("Nothing to copy", "Chat is empty.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # finalize clipboard
        self._status(f"Copied {len(text):,} chars to clipboard", GREEN)

    def _save_notes(self, fmt):
        """Save chat as md/txt/html. Default filename uses chapter + timestamp."""
        text = self._get_chat_text()
        if not text:
            messagebox.showinfo("Nothing to save", "Chat is empty. Generate some content first.")
            return

        # Build default filename
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        if self.selected_chapter:
            title = self.selected_chapter[0]
            safe = re.sub(r'[^\w\s-]', '', title).strip()[:40]
            safe = re.sub(r'[\s]+', '_', safe)
            default_name = f"notes_{safe}_{ts}.{fmt}"
        else:
            default_name = f"ai_teacher_notes_{ts}.{fmt}"

        type_map = {
            "md":   [("Markdown", "*.md"), ("All files", "*.*")],
            "txt":  [("Text", "*.txt"), ("All files", "*.*")],
            "html": [("HTML", "*.html"), ("All files", "*.*")],
        }
        path = filedialog.asksaveasfilename(
            title="Save Notes",
            initialdir=os.path.expanduser("~/Documents"),
            initialfile=default_name,
            defaultextension=f".{fmt}",
            filetypes=type_map.get(fmt, [("All files", "*.*")]))
        if not path:
            return

        try:
            if fmt == "html":
                output = self._build_html_notes(text)
            elif fmt == "md":
                output = self._build_md_notes(text)
            else:
                output = self._build_txt_notes(text)

            with open(path, "w", encoding="utf-8") as f:
                f.write(output)
            self._status(f"Saved to {os.path.basename(path)}", GREEN)
            messagebox.showinfo("Saved",
                                 f"Notes saved to:\n{path}\n\n{len(output):,} characters")
        except Exception as e:
            messagebox.showerror("Save Failed", f"Could not save:\n{e}")

    def _build_txt_notes(self, text):
        """Plain text — strip emoji-only lines, keep content."""
        from datetime import datetime
        header = f"AI Teacher Notes\n"
        header += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if self.selected_chapter:
            t, ps, pe = self.selected_chapter
            header += f"Chapter: {t}  (pages {ps}-{pe})\n"
        if self.active_pdf:
            header += f"Source: {os.path.basename(self.active_pdf)}\n"
        header += "=" * 70 + "\n\n"
        return header + text

    def _build_md_notes(self, text):
        """Markdown — preserves headings, lists, code, mermaid blocks."""
        from datetime import datetime
        header = "# AI Teacher Notes\n\n"
        header += f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        if self.selected_chapter:
            t, ps, pe = self.selected_chapter
            header += f"**Chapter:** {t}  (pages {ps}-{pe})\n\n"
        if self.active_pdf:
            header += f"**Source:** `{os.path.basename(self.active_pdf)}`\n\n"
        header += "---\n\n"
        return header + text

    def _build_html_notes(self, text):
        """HTML — clean study-notes layout with readable typography."""
        from datetime import datetime
        chap_title = ""
        chap_meta  = ""
        if self.selected_chapter:
            t, ps, pe = self.selected_chapter
            chap_title = t
            chap_meta  = f"Pages {ps}–{pe}"
        src_name = os.path.basename(self.active_pdf) if self.active_pdf else ""
        date_str = datetime.now().strftime("%B %d, %Y  %H:%M")
        html_body = self._text_to_html(text)

        return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{chap_title or 'Study Notes'}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink:      #1a1a2e;
    --ink-soft: #4a4a6a;
    --rule:     #d0d0e8;
    --accent:   #2563eb;
    --accent2:  #7c3aed;
    --paper:    #fefefe;
    --code-bg:  #f0f4ff;
    --code-ink: #1e3a8a;
    --warn:     #b45309;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'Lora', Georgia, serif;
    font-size: 16px;
    line-height: 1.85;
    color: var(--ink);
    background: #eef0f5;
    padding: 40px 20px;
  }}

  .page {{
    background: var(--paper);
    max-width: 860px;
    margin: 0 auto;
    padding: 56px 72px;
    border-radius: 4px;
    box-shadow: 0 2px 24px rgba(0,0,0,.10), 0 1px 4px rgba(0,0,0,.06);
    border-top: 5px solid var(--accent);
  }}

  /* ── Header ── */
  .note-header {{
    border-bottom: 2px solid var(--rule);
    padding-bottom: 20px;
    margin-bottom: 36px;
  }}
  .note-header h1 {{
    font-family: 'Inter', sans-serif;
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.02em;
    margin-bottom: 6px;
  }}
  .note-meta {{
    font-family: 'Inter', sans-serif;
    font-size: 0.8rem;
    color: var(--ink-soft);
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
  }}
  .note-meta span::before {{ margin-right: 4px; }}

  /* ── Headings ── */
  h1 {{ font-size: 1.45rem; color: var(--accent);  margin: 2rem 0 0.6rem; font-weight: 700; }}
  h2 {{
    font-family: 'Inter', sans-serif;
    font-size: 1.15rem;
    font-weight: 600;
    color: var(--accent);
    margin: 2.2rem 0 0.5rem;
    padding-bottom: 5px;
    border-bottom: 1.5px solid var(--rule);
    letter-spacing: -0.01em;
  }}
  h3 {{
    font-family: 'Inter', sans-serif;
    font-size: 1rem;
    font-weight: 600;
    color: var(--accent2);
    margin: 1.6rem 0 0.4rem;
  }}
  h4 {{
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--warn);
    margin: 1.2rem 0 0.3rem;
  }}

  /* ── Body text ── */
  p {{ margin-bottom: 1rem; }}
  strong {{ font-weight: 700; color: var(--ink); }}
  em {{ font-style: italic; color: var(--ink-soft); }}

  /* ── Lists ── */
  ul, ol {{
    margin: 0.6rem 0 1rem 1.4rem;
    padding: 0;
  }}
  li {{
    margin-bottom: 0.35rem;
    padding-left: 4px;
  }}
  li::marker {{ color: var(--accent); font-weight: 700; }}

  /* ── Inline code ── */
  code {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 0.85em;
    background: var(--code-bg);
    color: var(--code-ink);
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid #c7d2fe;
    white-space: nowrap;
  }}

  /* ── Code blocks ── */
  pre {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.84rem;
    line-height: 1.65;
    background: #0f172a;
    color: #e2e8f0;
    padding: 20px 24px;
    border-radius: 8px;
    overflow-x: auto;
    margin: 1.2rem 0;
    border-left: 4px solid var(--accent);
    box-shadow: 0 2px 8px rgba(0,0,0,.15);
  }}
  pre code {{
    background: none;
    color: inherit;
    border: none;
    padding: 0;
    font-size: inherit;
    white-space: pre;
  }}

  /* ── Mermaid diagrams ── */
  .mermaid {{
    background: #f8faff;
    border: 1px solid var(--rule);
    border-radius: 8px;
    padding: 20px;
    margin: 1.4rem 0;
    text-align: center;
    overflow-x: auto;
  }}

  /* ── Blockquote ── */
  blockquote {{
    border-left: 4px solid var(--accent2);
    margin: 1.2rem 0;
    padding: 10px 18px;
    background: #faf5ff;
    border-radius: 0 6px 6px 0;
    color: var(--ink-soft);
    font-style: italic;
  }}

  /* ── Divider ── */
  hr {{
    border: none;
    border-top: 1.5px solid var(--rule);
    margin: 2rem 0;
  }}

  /* ── Footer ── */
  .note-footer {{
    margin-top: 3rem;
    padding-top: 14px;
    border-top: 1px solid var(--rule);
    font-family: 'Inter', sans-serif;
    font-size: 0.75rem;
    color: var(--ink-soft);
    text-align: center;
  }}
  .note-footer a {{ color: var(--accent); text-decoration: none; }}

  /* ── Print ── */
  @media print {{
    body {{ background: white; padding: 0; }}
    .page {{ box-shadow: none; border-radius: 0; padding: 20mm 18mm; border-top: none; }}
    pre {{ break-inside: avoid; }}
    h2, h3 {{ break-after: avoid; }}
  }}
</style>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
</script>
</head>
<body>
<div class="page">
  <div class="note-header">
    <h1>📚 {chap_title or 'Study Notes'}</h1>
    <div class="note-meta">
      <span>📅 {date_str}</span>
      {"<span>📄 " + src_name + "</span>" if src_name else ""}
      {"<span>🔖 " + chap_meta + "</span>" if chap_meta else ""}
    </div>
  </div>
  {html_body}
  <div class="note-footer">
    Generated by <a href="https://github.com/Ashut90/pdf-tutor">PDF Tutor</a>
    &nbsp;·&nbsp; <a href="https://github.com/Ashut90/pdf-tutor">⭐ Star on GitHub</a>
  </div>
</div>
</body></html>"""

    def _text_to_html(self, text):
        """Convert chat text (with markdown-like formatting) to HTML."""
        # Escape HTML
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Convert fenced code blocks BEFORE line processing
        def code_block(m):
            lang = m.group(1) or ""
            body = m.group(2)
            if lang == "mermaid":
                return f'<pre class="mermaid">{body}</pre>'
            return f'<pre><code class="lang-{lang}">{body}</code></pre>'
        text = re.sub(r'```(\w+)?\n?([\s\S]*?)```', code_block, text)

        # Inline code
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

        # Convert lines
        out = []
        for line in text.split("\n"):
            if line.startswith("## "):
                out.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                out.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("# "):
                out.append(f"<h1>{line[2:]}</h1>")
            elif line.strip().startswith(("- ", "* ")):
                out.append(f"<li>{line.strip()[2:]}</li>")
            else:
                # Bold
                line = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', line)
                if line.strip():
                    out.append(f"<p>{line}</p>")
                else:
                    out.append("")
        return "\n".join(out)
