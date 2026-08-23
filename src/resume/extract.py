from __future__ import annotations

from pathlib import Path

from docx import Document
from pypdf import PdfReader


def extract_text_from_bytes(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _pdf_bytes(data)
    if suffix == ".docx":
        return _docx_bytes(data)
    if suffix in {".txt", ".md", ".html"}:
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported resume type: {suffix or 'unknown'} ({filename})")


def extract_text_from_path(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return extract_text_from_bytes(path.read_bytes(), path.name)


def _pdf_bytes(data: bytes) -> str:
    import io

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError("PDF resume extracted empty text")
    return text


def _docx_bytes(data: bytes) -> str:
    import io

    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs).strip()
    if not text:
        raise ValueError("DOCX resume extracted empty text")
    return text
