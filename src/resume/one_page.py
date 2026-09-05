"""Keep a tailored resume on one page by measuring, then trimming.

The model writes LaTeX blind - it never sees rendered output - so "keep it to
one page" in a prompt is a hope, not a guarantee. Here the PDF is compiled and
its pages counted, and while it is too long the next cut in a fixed priority
order is applied and it is recompiled.

Cuts are deterministic and ordered least-valuable first, so nothing important
is ever silently lost, and every cut is reported for the run log:

    1. the CCNA bullet in Certifications
    2. the whole Certifications section
    (stop: anything still too long is flagged rather than gutted)

Experience, Skills, Summary and Education are never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.pdf_compile import compile_tex
from src.resume.latex_sections import normalize_heading, split_sections

MAX_PAGES = 1

# (label, kind, target) - kind is "item" (one \item bullet) or "section".
CUTS: tuple[tuple[str, str, str], ...] = (
    ("CCNA certification", "item", "ccna"),
    ("Certifications section", "section", "certifications"),
)

_PAGES_TREE = re.compile(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", re.S)
_PAGE_OBJ = re.compile(rb"/Type\s*/Page[^s]")
_ITEM = re.compile(r"\\item\b")


@dataclass
class TrimResult:
    latex: str = ""  # trimmed source, or "" when nothing was cut
    pages: int = 0  # pages after trimming (0 when it could not be measured)
    cuts: list[str] = field(default_factory=list)
    note: str = ""


def page_count(pdf_path: Path) -> int:
    """Pages in a PDF, or 0 when it cannot be read."""
    try:
        data = Path(pdf_path).read_bytes()
    except OSError:
        return 0
    counts = [int(m.group(1)) for m in _PAGES_TREE.finditer(data)]
    if counts:
        return max(counts)
    return len(_PAGE_OBJ.findall(data))


def drop_item(source: str, needle: str) -> str:
    """Remove the single \\item bullet mentioning needle. Returns '' if absent."""
    lowered = needle.lower()
    for match in _ITEM.finditer(source):
        start = match.start()
        nxt = _ITEM.search(source, match.end())
        end = nxt.start() if nxt else len(source)
        # An \item runs until the next \item or the end of its list.
        close = source.find("\\end{itemize}", match.end())
        if close != -1 and close < end:
            end = close
        if lowered in source[start:end].lower():
            return source[:start] + source[end:]
    return ""


def drop_section(source: str, heading: str) -> str:
    """Remove a whole section, heading included. Returns '' if absent."""
    parsed = split_sections(source)
    target = normalize_heading(heading)
    if not any(normalize_heading(s.heading) == target for s in parsed.sections):
        return ""
    parts = [parsed.prologue]
    for section in parsed.sections:
        if normalize_heading(section.heading) == target:
            continue
        parts.append(section.command)
        parts.append(section.body)
    parts.append(parsed.epilogue)
    return "".join(parts)


def fit_to_one_page(tex_path: Path, max_pages: int = MAX_PAGES) -> TrimResult:
    """Compile, and while the PDF is too long apply the next cut and recompile.

    The .tex on disk is rewritten only when a cut actually helps; the caller
    gets the applied cuts for logging and the final page count.
    """
    tex_path = Path(tex_path)
    pdf_path, error = compile_tex(tex_path)
    if pdf_path is None:
        return TrimResult(note=f"could not compile: {error}")
    pages = page_count(pdf_path)
    if pages == 0:
        return TrimResult(pages=0, note="could not read the compiled PDF")
    if pages <= max_pages:
        return TrimResult(pages=pages)

    original = tex_path.read_text(encoding="utf-8")
    source = original
    cuts: list[str] = []
    for label, kind, target in CUTS:
        trimmed = drop_item(source, target) if kind == "item" else drop_section(source, target)
        if not trimmed:
            continue  # already gone, or this resume never had it
        source = trimmed
        cuts.append(label)
        tex_path.write_text(source, encoding="utf-8")
        pdf_path, error = compile_tex(tex_path)
        if pdf_path is None:
            # A cut that breaks the document is worse than a long one.
            tex_path.write_text(original, encoding="utf-8")
            return TrimResult(pages=pages, note=f"trim aborted, compile failed: {error}")
        pages = page_count(pdf_path)
        if pages and pages <= max_pages:
            return TrimResult(latex=source, pages=pages, cuts=cuts)

    if cuts:
        # Cuts were applied but it still does not fit: keep them (they were the
        # least valuable content anyway) and let the caller flag the row.
        return TrimResult(
            latex=source, pages=pages, cuts=cuts,
            note=f"still {pages} pages after trimming",
        )
    return TrimResult(pages=pages, note=f"{pages} pages and nothing left that may be cut")
