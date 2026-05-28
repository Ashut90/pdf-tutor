"""
PDF handling: table-of-contents extraction, text extraction, page rendering,
and chapter-list construction. Backed by PyMuPDF (fitz).
"""
import io

try:
    import fitz  # PyMuPDF
    PDF_OK = True
except ImportError:
    PDF_OK = False

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False


def get_toc_and_total(path):
    try:
        doc = fitz.open(path)
        toc = doc.get_toc(simple=True)
        total = doc.page_count
        doc.close()
        return toc, total
    except Exception:
        return [], 0


def extract_text(path, p_start, p_end):
    doc = fitz.open(path)
    text = "\n\n".join(doc[i].get_text() for i in range(p_start, p_end))
    doc.close()
    return text.strip()


def render_page_image(path, page_idx, zoom=2.0):
    doc = fitz.open(path)
    page = doc[page_idx]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img_bytes = pix.tobytes("ppm")
    doc.close()
    return Image.open(io.BytesIO(img_bytes))


def build_chapter_list(toc, total):
    if not toc:
        return [(1, f"Pages {s+1}–{min(s+20, total)}", s+1, min(s+20, total))
                for s in range(0, total, 20)]
    result = []
    for i, (level, title, page) in enumerate(toc):
        end = total
        for j in range(i + 1, len(toc)):
            nl, _, np = toc[j]
            if nl <= level:
                end = np - 1
                break
        end = max(page, min(end, total))
        result.append((level, title.strip(), page, end))
    return result


# ── MERMAID RENDERING ─────────────────────────────────────────────────────────
