from __future__ import annotations  # Fix: tuple[str, int] type hint requires Python 3.9+ without this
import pdfplumber
import re


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r'^\s*[.\-_]{4,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


def detect_heading(line: str) -> str:
    line = line.strip()
    if not line:
        return line
    if re.match(r'^(\d+\.)+\d*\s+[A-Z]', line) and len(line) < 100:
        return f"## {line}"
    if line.isupper() and 3 < len(line) < 80:
        return f"# {line}"
    return line


def table_to_markdown(table: list) -> str:
    if not table or not table[0]:
        return ""
    rows = [
        [str(cell or "").strip().replace("\n", " ") for cell in row]
        for row in table
    ]
    col_count = max(len(row) for row in rows)
    rows = [row + [""] * (col_count - len(row)) for row in rows]

    header = rows[0]
    body = rows[1:]

    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in body:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def extract_markdown(file_obj) -> tuple[str, int]:
    parts = []

    with pdfplumber.open(file_obj) as pdf:
        page_count = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            page_parts = []

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    md_table = table_to_markdown(table)
                    if md_table:
                        page_parts.append(md_table)

            text = page.extract_text()
            if text:
                lines = text.splitlines()
                processed = [detect_heading(line) for line in lines]
                cleaned = clean_text("\n".join(processed))
                if cleaned:
                    page_parts.append(cleaned)

            if page_parts:
                if page_count > 1:
                    parts.append(f"## Page {i + 1}\n\n" + "\n\n".join(page_parts))
                else:
                    parts.append("\n\n".join(page_parts))

    markdown = "\n\n---\n\n".join(parts)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown).strip()

    return markdown, page_count


def get_stats(markdown: str, page_count: int) -> dict:
    char_count = len(markdown)
    word_count = len(markdown.split())
    approx_tokens_markdown = char_count // 4
    approx_tokens_pdf = page_count * 1700
    tokens_saved = max(0, approx_tokens_pdf - approx_tokens_markdown)
    savings_pct = round((tokens_saved / approx_tokens_pdf) * 100) if approx_tokens_pdf > 0 else 0

    return {
        "pages": page_count,
        "characters": char_count,
        "words": word_count,
        "tokens_markdown": approx_tokens_markdown,
        "tokens_pdf": approx_tokens_pdf,
        "tokens_saved": tokens_saved,
        "savings_percent": savings_pct,
    }
