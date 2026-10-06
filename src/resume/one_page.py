"""Keep a tailored resume on one page by measuring, then trimming.

The model writes LaTeX blind - it never sees rendered output - so "keep it to
one page" in a prompt is a hope, not a guarantee. Here the PDF is compiled and
its pages counted, and while it is too long the next step of the candidate's
fit policy (src/resume/fit_policy.py, agreed in the Resume fit tab) is
applied and it is recompiled. Without a saved policy the old fixed order
stands:

    1. the CCNA bullet in Certifications
    2. the whole Certifications section
    (stop: anything still too long is flagged rather than gutted)

Every step is reported for the run log, so nothing is silently lost. A step
that breaks the compile is skipped, and the steps after it still get their
turn; what reaches disk always compiles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.pdf_compile import compile_tex
from src.resume import fit_policy

MAX_PAGES = 1

_PAGES_TREE = re.compile(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", re.S)
_PAGE_OBJ = re.compile(rb"/Type\s*/Page[^s]")


@dataclass
class TrimResult:
    latex: str = ""  # trimmed source, or "" when nothing was cut
    pages: int = 0  # pages after trimming (0 when it could not be measured)
    cuts: list[str] = field(default_factory=list)
    note: str = ""
    # (step label, pages after it), first entry ("as written", pages) - what
    # the Resume fit preview shows.
    trail: list[tuple[str, int]] = field(default_factory=list)


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


def fit_to_one_page(tex_path: Path, max_pages: int = MAX_PAGES,
                    steps: list[fit_policy.FitStep] | None = None) -> TrimResult:
    """Compile, and while the PDF is too long apply the next step and recompile.

    `steps` defaults to the saved fit policy. The .tex on disk is rewritten
    only when a step actually changes it; the caller gets the applied steps
    for logging and the final page count.
    """
    tex_path = Path(tex_path)
    if steps is None:
        steps = fit_policy.load().steps
    pdf_path, error = compile_tex(tex_path)
    if pdf_path is None:
        return TrimResult(note=f"could not compile: {error}")
    pages = page_count(pdf_path)
    if pages == 0:
        return TrimResult(pages=0, note="could not read the compiled PDF")
    trail = [("as written", pages)]
    if pages <= max_pages:
        return TrimResult(pages=pages, trail=trail)

    good = tex_path.read_text(encoding="utf-8")   # the last source that compiled
    cuts: list[str] = []
    skipped: list[str] = []
    for step in steps:
        trimmed = fit_policy.apply_step(good, step)
        if not trimmed:
            continue  # already gone, or this resume never had it
        tex_path.write_text(trimmed, encoding="utf-8")
        pdf_path, error = compile_tex(tex_path)
        if pdf_path is None:
            # A step that breaks the document is skipped, not the policy:
            # the ones after it still get their turn on the last good source.
            skipped.append(f"'{step.label}' broke the compile ({error[:120]}) and was skipped")
            tex_path.write_text(good, encoding="utf-8")
            compile_tex(tex_path)
            continue
        good = trimmed
        cuts.append(step.label)
        pages = page_count(pdf_path)
        trail.append((step.label, pages))
        if pages and pages <= max_pages:
            return TrimResult(latex=good, pages=pages, cuts=cuts, trail=trail,
                              note="; ".join(skipped))

    notes = list(skipped)
    if cuts:
        # Steps were applied but it still does not fit: keep them (the
        # candidate chose them) and let the caller flag the row.
        notes.append(f"still {pages} pages after trimming")
    else:
        notes.append(f"{pages} pages and nothing left that may be cut")
    return TrimResult(latex=good if cuts else "", pages=pages, cuts=cuts, trail=trail,
                      note="; ".join(notes))
