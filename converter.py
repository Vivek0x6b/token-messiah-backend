from __future__ import annotations
import re
import io
import fitz        # pymupdf — text extraction
import pdfplumber  # table extraction only


# ─── Spacing & Cleaning ───────────────────────────────────────────────────────

def fix_spacing(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = re.sub(r'([.,;:!?])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'(\d)([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])(\d)', r'\1 \2', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def clean_text(text: str) -> str:
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if re.match(r'^\s*\d+\s*$', line):           # page numbers
            continue
        if re.match(r'^\s*[.\-_]{4,}\s*$', line):    # TOC leaders
            continue
        line = re.sub(r'\(cid:\d+\)', '', line)       # PDF artifacts
        # Remove common journal footer/header lines
        if re.search(r'VOLUME\s*\d+|IEEE\s+ACCESS|creativecommons\.org', line, re.I):
            continue
        lines.append(line if line.strip() else "")

    text = "\n".join(lines)
    text = fix_spacing(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def detect_heading(line: str) -> str:
    s = line.strip()
    if not s or len(s) > 120:
        return line

    # Numbered section: "I. INTRODUCTION" or "2.1 Overview"
    if re.match(r'^[IVX]+\.\s+[A-Z]', s) and len(s) < 80:      # Roman numeral
        return f"## {s}"
    if re.match(r'^(\d+\.)*\d+\s+[A-Z][a-z]', s) and len(s) < 80:
        depth = s.count('.')
        return f"{'###' if depth >= 1 else '##'} {s}"

    # ALL CAPS heading — strict: short, no digits, looks like a real title
    # Exclude author names (contain dots or are very long)
    if (re.match(r'^[A-Z][A-Z\s,.\-]+$', s)
            and 4 < len(s) < 60
            and not re.search(r'\d', s)
            and s.count('.') <= 1):
        return f"# {s}"

    return line


# ─── Column Detection ─────────────────────────────────────────────────────────

def is_two_column(page) -> tuple[bool, float]:
    """
    Detect if a page has two columns by analyzing the horizontal
    distribution of text blocks. Returns (is_two_col, split_x).
    """
    blocks = page.get_text("blocks")
    if not blocks:
        return False, 0

    page_width = page.rect.width
    mid = page_width / 2

    # Count blocks clearly in left vs right half
    left_blocks = [b for b in blocks if b[6] == 0 and b[2] < mid * 0.9]
    right_blocks = [b for b in blocks if b[6] == 0 and b[0] > mid * 1.1]

    total = len([b for b in blocks if b[6] == 0])
    if total == 0:
        return False, mid

    # If significant blocks exist on both sides → two column
    is_two_col = len(left_blocks) >= 2 and len(right_blocks) >= 2
    return is_two_col, mid


def extract_column_text(page, x0: float, x1: float) -> str:
    """Extract text from a vertical strip of the page (one column)."""
    clip = fitz.Rect(x0, 0, x1, page.rect.height)
    blocks = page.get_text("blocks", clip=clip, sort=True)
    lines = []
    prev_y = None
    for block in blocks:
        if block[6] != 0:
            continue
        text = block[4].strip()
        if not text:
            continue
        y0 = block[1]
        if prev_y is not None and (y0 - prev_y) > 20:
            lines.append("")
        lines.append(text)
        prev_y = block[3]
    return "\n".join(lines)


def extract_text_from_page(page) -> str:
    """
    Smart extraction: detect column layout and extract accordingly.
    Single column → full page extract.
    Two column → extract left then right, concatenate.
    """
    is_two_col, mid = is_two_column(page)

    if is_two_col:
        # Add small margin to avoid overlap
        left = extract_column_text(page, 0, mid - 5)
        right = extract_column_text(page, mid + 5, page.rect.width)
        return left + ("\n\n" if left and right else "") + right
    else:
        # Single column — standard block extraction
        blocks = page.get_text("blocks", sort=True)
        lines = []
        prev_y = None
        for block in blocks:
            if block[6] != 0:
                continue
            text = block[4].strip()
            if not text:
                continue
            y0 = block[1]
            if prev_y is not None and (y0 - prev_y) > 20:
                lines.append("")
            lines.append(text)
            prev_y = block[3]
        return "\n".join(lines)


# ─── Table Extraction (pdfplumber) ────────────────────────────────────────────

def table_to_markdown(table: list) -> str:
    if not table or not table[0]:
        return ""
    rows = [
        [re.sub(r'\(cid:\d+\)', '', str(cell or "")).strip().replace("\n", " ")
         for cell in row]
        for row in table
    ]
    rows = [row for row in rows if any(c.strip() for c in row)]
    if not rows:
        return ""
    col_count = max(len(row) for row in rows)
    rows = [row + [""] * (col_count - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ] + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def extract_tables(file_bytes: bytes, page_index: int) -> list[str]:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if page_index >= len(pdf.pages):
                return []
            tables = pdf.pages[page_index].extract_tables()
            return [table_to_markdown(t) for t in (tables or []) if t]
    except Exception:
        return []


# ─── Main ─────────────────────────────────────────────────────────────────────

def extract_markdown(file_obj) -> tuple[str, int]:
    file_bytes = file_obj.read()
    parts = []

    fitz_doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = len(fitz_doc)

    for i, page in enumerate(fitz_doc):
        page_parts = []

        # Tables via pdfplumber
        for t in extract_tables(file_bytes, i):
            if t:
                page_parts.append(t)

        # Text via pymupdf with column detection
        raw = extract_text_from_page(page)
        if raw:
            lines = [detect_heading(l) for l in raw.splitlines()]
            cleaned = clean_text("\n".join(lines))
            if cleaned:
                page_parts.append(cleaned)

        if page_parts:
            header = f"## Page {i + 1}\n\n" if page_count > 1 else ""
            parts.append(header + "\n\n".join(page_parts))

    fitz_doc.close()

    markdown = "\n\n---\n\n".join(parts)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown).strip()
    return markdown, page_count


# ─── Stats ────────────────────────────────────────────────────────────────────

def get_stats(markdown: str, page_count: int) -> dict:
    char_count = len(markdown)
    word_count = len(markdown.split())
    tokens_md = char_count // 4
    tokens_pdf = page_count * 1700
    tokens_saved = max(0, tokens_pdf - tokens_md)
    savings_pct = round((tokens_saved / tokens_pdf) * 100) if tokens_pdf > 0 else 0
    return {
        "pages": page_count,
        "characters": char_count,
        "words": word_count,
        "tokens_markdown": tokens_md,
        "tokens_pdf": tokens_pdf,
        "tokens_saved": tokens_saved,
        "savings_percent": savings_pct,
    }