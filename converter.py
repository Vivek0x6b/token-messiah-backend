from __future__ import annotations
import re
import fitz  # pymupdf - for text extraction (better layout, spacing, columns)
import pdfplumber  # kept only for table extraction (better precision)


# ─── Text Cleaning ────────────────────────────────────────────────────────────

def fix_spacing(text: str) -> str:
    if not text:
        return ""
    # Fix missing space between lowercase→uppercase (merged words)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    # Fix missing space after punctuation
    text = re.sub(r'([.,;:!?])([A-Za-z])', r'\1 \2', text)
    # Fix digit↔letter merges
    text = re.sub(r'(\d)([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])(\d)', r'\1 \2', text)
    # Collapse multiple spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def clean_text(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.rstrip()
        # Skip standalone page numbers
        if re.match(r'^\s*\d+\s*$', line):
            continue
        # Skip TOC leader lines
        if re.match(r'^\s*[.\-_]{4,}\s*$', line):
            continue
        # Remove (cid:NNN) PDF artifacts
        line = re.sub(r'\(cid:\d+\)', '', line)
        # Remove lines that are only whitespace after cleanup
        cleaned.append(line if line.strip() else "")

    text = "\n".join(cleaned)
    text = fix_spacing(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def detect_heading(line: str) -> str:
    """Conservative heading detection — avoids false positives in references."""
    s = line.strip()
    if not s or len(s) > 120:
        return line
    # Numbered section: "1 Introduction", "2.1 Overview"
    if re.match(r'^(\d+\.)*\d+\s+[A-Z][a-z]', s) and len(s) < 80:
        depth = s.count('.')
        return f"{'###' if depth >= 1 else '##'} {s}"
    # ALL CAPS heading (strict: only letters and spaces)
    if re.match(r'^[A-Z][A-Z\s]{3,59}$', s):
        return f"# {s}"
    return line


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


def extract_tables_from_page(pdf_path_or_obj, page_index: int) -> list[str]:
    """Use pdfplumber just for table extraction on a specific page."""
    try:
        with pdfplumber.open(pdf_path_or_obj) as pdf:
            if page_index >= len(pdf.pages):
                return []
            tables = pdf.pages[page_index].extract_tables()
            return [table_to_markdown(t) for t in (tables or []) if t]
    except Exception:
        return []


# ─── Text Extraction (pymupdf) ────────────────────────────────────────────────

def extract_text_from_page(page) -> str:
    """
    Extract text using pymupdf with layout preservation.
    'blocks' mode respects reading order and column boundaries.
    """
    blocks = page.get_text("blocks", sort=True)  # sort=True = reading order
    lines = []
    prev_y = None

    for block in blocks:
        # block = (x0, y0, x1, y1, text, block_no, block_type)
        if block[6] != 0:  # skip image blocks
            continue
        text = block[4].strip()
        if not text:
            continue

        # Add blank line between blocks that are far apart vertically
        y0 = block[1]
        if prev_y is not None and (y0 - prev_y) > 20:
            lines.append("")

        lines.append(text)
        prev_y = block[3]  # y1 (bottom of block)

    return "\n".join(lines)


# ─── Main Extraction ──────────────────────────────────────────────────────────

def extract_markdown(file_obj) -> tuple[str, int]:
    import io

    # Read bytes once — share between pymupdf and pdfplumber
    file_bytes = file_obj.read()
    page_count = 0
    parts = []

    # Open with pymupdf for text
    fitz_doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = len(fitz_doc)

    for i, page in enumerate(fitz_doc):
        page_parts = []

        # 1. Tables via pdfplumber
        table_mds = extract_tables_from_page(io.BytesIO(file_bytes), i)
        page_parts.extend([t for t in table_mds if t])

        # 2. Text via pymupdf
        raw_text = extract_text_from_page(page)
        if raw_text:
            lines = raw_text.splitlines()
            processed = [detect_heading(line) for line in lines]
            cleaned = clean_text("\n".join(processed))
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