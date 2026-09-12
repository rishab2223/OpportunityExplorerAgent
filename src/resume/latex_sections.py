"""Split a single-file LaTeX resume into sections and splice in per-section edits.

The enrich step asks the model for replacement bodies per section instead of a
full document rewrite: bytes the model never touches (preamble, headings,
spacing) cannot drift, and sections cannot be dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_SECTION = re.compile(r"\\section\*?\{([^}]*)\}")
_END_DOCUMENT = re.compile(r"\\end\{document\}")
_FENCE_OPEN = re.compile(r"^```(?:latex|tex)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")
_COMMENT = re.compile(r"(?<!\\)%.*")
_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")

# Commands that must never appear inside a section body replacement.
FORBIDDEN_COMMANDS = (
    "\\documentclass",
    "\\usepackage",
    "\\begin{document}",
    "\\end{document}",
    "\\section",
    "\\input",
    "\\include",
    "\\pagestyle",
)

# A tailored document should stay close to the original's length: sections are
# preserved by construction, so a large delta means bloated or gutted bodies.
# The upper bound allows genuinely useful additions (~15%, roughly what
# dropping the certifications reclaims - the one_page trim order); runaway
# rewrites beyond that would stay two pages even after trimming (measured:
# 102 of 150 tailored resumes overflowed at the old 1.30 cap).
MIN_LENGTH_RATIO = 0.85
MAX_LENGTH_RATIO = 1.15


@dataclass
class Section:
    heading: str
    command: str  # the literal \section*{...} text from the source
    body: str  # everything between this heading and the next (or \end{document})


@dataclass
class ParsedResume:
    prologue: str  # everything before the first \section
    sections: list[Section]
    epilogue: str  # \end{document} and anything after it


@dataclass
class EditResult:
    latex: str = ""  # spliced document, or "" when nothing usable was applied
    applied: int = 0
    problems: list[str] = field(default_factory=list)


def normalize_heading(heading: str) -> str:
    return " ".join((heading or "").split()).casefold()


def strip_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = _FENCE_OPEN.sub("", s, count=1)
        s = _FENCE_CLOSE.sub("", s)
    return s.strip()


def split_sections(source: str) -> ParsedResume:
    end_doc = _END_DOCUMENT.search(source)
    end_pos = end_doc.start() if end_doc else len(source)
    matches = [m for m in _SECTION.finditer(source) if m.start() < end_pos]
    if not matches:
        return ParsedResume(prologue=source, sections=[], epilogue="")
    sections: list[Section] = []
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else end_pos
        sections.append(
            Section(
                heading=m.group(1).strip(),
                command=m.group(0),
                body=source[m.end() : body_end],
            )
        )
    return ParsedResume(
        prologue=source[: matches[0].start()],
        sections=sections,
        epilogue=source[end_pos:],
    )


def validate_body(body: str) -> list[str]:
    """Return the reasons a replacement body is unsafe to splice (empty if fine)."""
    text = strip_fences(body)
    if not text:
        return ["empty replacement body"]
    problems: list[str] = []
    for command in FORBIDDEN_COMMANDS:
        if command in text:
            problems.append(f"must not contain {command}")
    code = _COMMENT.sub("", text)
    if _brace_delta(code) != 0:
        problems.append("unbalanced braces")
    begins = Counter(m.group(2) for m in _ENV.finditer(code) if m.group(1) == "begin")
    ends = Counter(m.group(2) for m in _ENV.finditer(code) if m.group(1) == "end")
    mismatched = sorted(
        name for name in set(begins) | set(ends) if begins[name] != ends[name]
    )
    if mismatched:
        problems.append("unbalanced \\begin/\\end for: " + ", ".join(mismatched))
    return problems


_DOCUMENTCLASS = re.compile(r"^[^%\n]*\\documentclass[^\n]*\n", re.MULTILINE)


FONT_PREAMBLE = "\\usepackage{lmodern}\n\\usepackage[T1]{fontenc}\n"
# A preamble that picked its own typeface is left alone.
_HAS_FONT_RE = re.compile(
    r"\\usepackage(\[[^\]]*\])?\{(lmodern|fontspec|newtx\w*|times|helvet|mathptmx"
    r"|charter|libertine|kpfonts|palatino|tgtermes|tgheros|cmbright)\}")
_FONTENC_LINE_RE = re.compile(
    r"^[^%\n]*\\usepackage(\[[^\]]*\])?\{fontenc\}[^\n]*\n", re.MULTILINE)


def ensure_font_encoding(source: str) -> str:
    """Give the preamble a T1 font encoding, and the vector font that goes
    with it, when it has neither.

    T1 on its own was not enough. Without any encoding, pdfTeX's default OT1
    fonts carry no Unicode mapping for the underscore, so an e-mail address
    extracts from the PDF as "a candidate@example.invalid" and an ATS that
    parses the resume fills its form with that broken address. But adding T1
    alone makes pdfTeX fall back to the bitmap EC fonts, and the PDF then
    embeds Type 3 fonts, which look soft and a little heavier on screen.
    Latin Modern is the same design as Computer Modern with real T1 outlines,
    so the underscore survives and the page looks as it did before.
    """
    text = source or ""
    if re.search(r"\\usepackage(\[[^\]]*\])?\{(fontenc|fontspec)\}", text):
        return _ensure_vector_font(text)
    match = _DOCUMENTCLASS.search(text)
    if match is None:
        return text
    return text[:match.end()] + FONT_PREAMBLE + text[match.end():]


def _ensure_vector_font(text: str) -> str:
    """A preamble that already asks for T1 but names no typeface still renders
    as bitmaps: give it Latin Modern."""
    if _HAS_FONT_RE.search(text):
        return text
    match = _FONTENC_LINE_RE.search(text)
    if match is None:
        return text
    return text[:match.start()] + "\\usepackage{lmodern}\n" + text[match.start():]


def apply_section_edits(parsed: ParsedResume, accepted: dict[str, str]) -> str:
    """Reassemble the document, swapping bodies whose normalized heading is in accepted."""
    parts = [ensure_font_encoding(parsed.prologue)]
    for section in parsed.sections:
        parts.append(section.command)
        replacement = accepted.get(normalize_heading(section.heading))
        if replacement is None:
            parts.append(section.body)
        else:
            parts.append("\n" + replacement.strip("\n") + "\n\n")
    parts.append(parsed.epilogue)
    return "".join(parts)


def tailor_latex(
    source: str,
    edits: Iterable[tuple[str, str]],
    parsed: ParsedResume | None = None,
) -> EditResult:
    """Validate (heading, body) edits and splice the good ones into source.

    Rejected edits leave their section unchanged and are reported in problems.
    A document whose length drifts outside the allowed band is rejected whole.
    """
    if parsed is None:
        parsed = split_sections(source)
    known = {normalize_heading(s.heading): s.heading for s in parsed.sections}
    accepted: dict[str, str] = {}
    problems: list[str] = []
    for heading, body in edits:
        key = normalize_heading(heading)
        if key not in known:
            problems.append(f"unknown section heading: {heading.strip()!r}")
            continue
        cleaned = strip_fences(body)
        issues = validate_body(cleaned)
        if issues:
            problems.append(f"{known[key]}: " + "; ".join(issues))
            continue
        accepted[key] = cleaned
    if not accepted:
        return EditResult(problems=problems)
    latex = apply_section_edits(parsed, accepted)
    # Measure the candidate's edit, not ours: apply_section_edits adds the
    # font preamble, and counting those lines as growth rejected a perfectly
    # good rewrite of a short section.
    ratio = len(latex) / max(len(ensure_font_encoding(source)), 1)
    if not (MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO):
        problems.append(
            f"document length became x{ratio:.2f} of the original; keep each "
            "replacement close to the original section's length"
        )
        return EditResult(problems=problems)
    return EditResult(latex=latex, applied=len(accepted), problems=problems)


def validate_document(source: str) -> list[str]:
    """Structural checks on a WHOLE hand-edited document (the apply modal's
    edit-source path), before it is compiled: complete skeleton, balanced
    braces and environments. Returns the problems, empty when sound."""
    text = (source or "").strip()
    if not text:
        return ["the document is empty"]
    problems: list[str] = []
    for required in (r"\documentclass", r"\begin{document}", r"\end{document}"):
        if required not in text:
            problems.append(f"missing {required}")
    code = _COMMENT.sub("", text)
    if _brace_delta(code) != 0:
        problems.append("unbalanced braces")
    begins = Counter(m.group(2) for m in _ENV.finditer(code) if m.group(1) == "begin")
    ends = Counter(m.group(2) for m in _ENV.finditer(code) if m.group(1) == "end")
    mismatched = sorted(name for name in set(begins) | set(ends) if begins[name] != ends[name])
    if mismatched:
        problems.append("unbalanced \\begin/\\end for: " + ", ".join(mismatched))
    return problems


def _brace_delta(text: str) -> int:
    depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        i += 1
    return depth
