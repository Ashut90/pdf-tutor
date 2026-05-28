"""
Visual rendering: convert AI-generated mermaid/chart specs into images.
Render chain for mermaid: mermaid.ink -> kroki.io -> local graphviz fallback.
Charts rendered locally with matplotlib.
"""
import io
import os
import re
import json
import shutil
import zlib
import base64
import tempfile
import subprocess
import urllib.request
import urllib.error

from pdf_tutor.config import BG, ACCENT, GREEN, ORANGE, WARN, BLUE, CODE_BLUE, MUTED, PANEL

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MPL_OK = True
except ImportError:
    MPL_OK = False

try:
    import graphviz
    GRAPHVIZ_OK = True
except ImportError:
    GRAPHVIZ_OK = False

MMDC_INSTALLED = shutil.which("mmdc") is not None
MERMAID_OK = True  # always available via online APIs + graphviz fallback


def mermaid_to_graphviz(mmd_code):
    """
    Convert a simple mermaid flowchart to a graphviz Digraph and render as PNG.
    Handles flowchart TD/LR, basic nodes, edges with labels. Skips advanced features.
    Returns (PIL.Image | None, error_str)
    """
    if not GRAPHVIZ_OK or not PIL_OK:
        return None, "graphviz/Pillow not installed"
    try:
        # Detect direction
        rankdir = "TB"
        first_line = mmd_code.strip().split("\n", 1)[0].lower()
        if "lr" in first_line:
            rankdir = "LR"
        elif "rl" in first_line:
            rankdir = "RL"
        elif "bt" in first_line:
            rankdir = "BT"

        g = graphviz.Digraph(format="png")
        g.attr(bgcolor="#0d1117", rankdir=rankdir, pad="0.4")
        g.attr("node", shape="box", style="filled,rounded",
               fillcolor="#1c2333", fontcolor="#e6edf3",
               color="#58a6ff", fontname="Helvetica", fontsize="11")
        g.attr("edge", color="#8b949e", fontcolor="#79c0ff",
               fontname="Helvetica", fontsize="10")

        # Parse nodes and edges
        nodes_seen = set()

        def add_node(node_id, label):
            if node_id in nodes_seen:
                return
            nodes_seen.add(node_id)
            # Clean label
            label = label.strip().replace('"', '\\"')
            g.node(node_id, label)

        # Parse each line for: nodeA[label] --> nodeB[label]  or with edge labels
        edge_re = re.compile(
            r'(\w+)(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})?'
            r'\s*(?:--?\>?\|([^|]+)\||--?\>?)\s*'
            r'(\w+)(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})?'
        )

        for raw_line in mmd_code.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith(("flowchart", "graph", "subgraph", "end",
                                              "style", "classDef", "linkStyle", "%%")):
                continue
            m = edge_re.search(line)
            if not m:
                # Maybe a node-only declaration: NodeID[label]
                node_m = re.match(r'^(\w+)\[([^\]]+)\]\s*$', line)
                if node_m:
                    add_node(node_m.group(1), node_m.group(2))
                continue
            src_id = m.group(1)
            src_label = m.group(2) or m.group(3) or m.group(4) or src_id
            dst_id = m.group(6)
            dst_label = m.group(7) or m.group(8) or m.group(9) or dst_id
            edge_label = (m.group(5) or "").strip()
            add_node(src_id, src_label)
            add_node(dst_id, dst_label)
            if edge_label:
                g.edge(src_id, dst_id, label=edge_label)
            else:
                g.edge(src_id, dst_id)

        if not nodes_seen:
            return None, "No parseable nodes found"

        data = g.pipe(format="png")
        return Image.open(io.BytesIO(data)), None
    except Exception as e:
        return None, f"graphviz: {e}"


def render_mermaid_to_image(mmd_code):
    """
    Render Mermaid to PIL Image. Tries in order:
    1. mermaid.ink pako-compressed format (handles complex diagrams reliably)
    2. mermaid.ink simple base64 format (fallback)
    3. kroki.io
    Returns (PIL.Image | None, error_message_or_None)
    """
    if not PIL_OK:
        return None, "Pillow not installed"

    import base64, zlib

    # Strip styling lines that often break the API
    # (we keep the diagram structure but drop "style X fill:..." lines)
    clean_lines = []
    for line in mmd_code.split("\n"):
        stripped = line.strip()
        # Skip overly long URL-killer style/classDef lines for the API renderer
        if stripped.startswith(("style ", "classDef ", "linkStyle ")):
            continue
        clean_lines.append(line)
    clean_mmd = "\n".join(clean_lines).strip()

    # Try 1: mermaid.ink with pako compression (their recommended format)
    try:
        # Pako format: zlib compressed, base64 url-safe, with "pako:" prefix
        compressed = zlib.compress(clean_mmd.encode("utf-8"), 9)
        encoded = base64.urlsafe_b64encode(compressed).decode().rstrip("=")
        url = f"https://mermaid.ink/img/pako:{encoded}?type=png&bgColor=!white"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI-Teacher"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data))
        img.load()
        return img, None
    except Exception as e1:
        err1 = f"mermaid.ink/pako: {e1}"

    # Try 2: mermaid.ink simple base64 (no bgColor)
    try:
        encoded = base64.urlsafe_b64encode(clean_mmd.encode("utf-8")).decode().rstrip("=")
        url = f"https://mermaid.ink/img/{encoded}?type=png"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI-Teacher"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data))
        img.load()
        return img, None
    except Exception as e2:
        err2 = f"mermaid.ink/base64: {e2}"

    # Try 3: kroki.io
    try:
        compressed = zlib.compress(clean_mmd.encode("utf-8"), 9)
        encoded = base64.urlsafe_b64encode(compressed).decode().rstrip("=")
        url = f"https://kroki.io/mermaid/png/{encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI-Teacher"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data))
        img.load()
        return img, None
    except Exception as e3:
        err3 = f"kroki.io: {e3}"

    # Try 4: LOCAL graphviz — works offline, no Chrome, no online services needed
    img, gv_err = mermaid_to_graphviz(clean_mmd)
    if img is not None:
        return img, None
    err4 = gv_err or "graphviz failed"

    return None, f"All renderers failed:\n  {err1}\n  {err2}\n  {err3}\n  {err4}"


def render_chart_to_image(spec_text):
    """Parse a simple chart spec and render with matplotlib. Returns PIL Image or None."""
    if not MPL_OK or not PIL_OK:
        return None
    try:
        spec = {}
        for line in spec_text.strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                spec[k.strip().lower()] = v.strip()

        chart_type = spec.get("type", "bar").lower()
        title = spec.get("title", "")

        def parse_list(s):
            s = s.strip().strip("[]")
            return [x.strip().strip("'\"") for x in s.split(",")]

        x = parse_list(spec.get("x", "[]"))
        values_raw = parse_list(spec.get("values", spec.get("y", "[]")))
        try:
            values = [float(v) for v in values_raw]
        except ValueError:
            return None

        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=BG)
        ax.set_facecolor(PANEL)

        if chart_type in ("bar", "column"):
            ax.bar(x, values, color=BLUE, edgecolor=ACCENT)
        elif chart_type in ("line",):
            ax.plot(x, values, color=BLUE, linewidth=2, marker="o", markersize=8)
        elif chart_type in ("pie",):
            ax.pie(values, labels=x, autopct="%1.0f%%",
                   colors=[BLUE, GREEN, ORANGE, WARN, CODE_BLUE, ACCENT, MUTED])
        else:
            ax.bar(x, values, color=BLUE)

        if title:
            ax.set_title(title, color=TEXT, fontsize=14, pad=12)
        ax.tick_params(colors=MUTED)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", facecolor=BG, dpi=100)
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf)
    except Exception:
        return None
