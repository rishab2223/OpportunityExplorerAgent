"""What gives way when a tailored resume runs past one page.

The candidate decides, in the Resume fit tab, by talking it through with the
model; what they agree on is saved here (localData/resume_fit_policy.json)
and every run applies it from then on. Until they do, the old fixed rule
stands: the CCNA bullet, then the whole Certifications section.

A policy is an ordered list of steps. When a compiled resume is too long the
first step is applied and it is compiled again, then the next, until it fits
- so a later step only ever happens to a resume the earlier ones could not
save. Every step is mechanical and checked: the model proposes them in the
chat, but what runs is this code, never free-form edits.

    layout          margin, font size, space around headings or bullets -
                    only ever tighter than what the resume has
    drop_item       one bullet (in one section, or anywhere) naming some words
    drop_section    a whole section
    replace_section a fixed shorter version of a section, written and agreed
                    in chat (it replaces whatever the tailoring wrote there,
                    so it suits sections tailoring leaves alone)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from src import atomic
from src.config import ROOT
from src.resume.latex_sections import normalize_heading, split_sections, strip_fences, validate_body

POLICY_PATH = ROOT / "localData" / "resume_fit_policy.json"

# Bounds a layout step must stay inside: past these a resume stops reading
# like one (a 0.3in margin, 8pt type).
LAYOUT_LIMITS: dict[str, tuple[float, float, str]] = {
    "margin": (0.4, 1.0, "in"),
    "font_size": (10, 12, "pt"),
    "section_spacing": (0.0, 2.0, "ex"),
    "item_spacing": (0.0, 6.0, "pt"),
}
SETTING_NAMES = {"margin": "Margins", "font_size": "Font size",
                 "section_spacing": "Space around headings", "item_spacing": "Space between bullets"}


class StepFields(BaseModel):
    """A step as the model writes it: checked afterwards, one by one, so one
    bad step costs that step and not the whole reply."""
    kind: Literal["layout", "drop_item", "drop_section", "replace_section"]
    label: str = Field("", description="What the candidate sees, e.g. 'Margins down to 0.6in'")
    section: str = Field("", description="Section heading, copied from the resume")
    match: str = Field("", description="drop_item: words the bullet contains, e.g. 'CCNA'")
    latex_body: str = Field("", description="replace_section: the complete shorter body, raw LaTeX")
    setting: Literal["margin", "font_size", "section_spacing", "item_spacing", ""] = Field(
        "", description="layout: which setting")
    value: float = Field(0, description="layout: inches for margin, pt for font_size "
                                        "(10, 11 or 12), ex for section_spacing, pt for item_spacing")


class FitStep(StepFields):
    @model_validator(mode="after")
    def _complete(self) -> "FitStep":
        if self.kind == "layout":
            if self.setting not in LAYOUT_LIMITS:
                raise ValueError("a layout step names its setting: margin, font_size, "
                                 "section_spacing or item_spacing")
            low, high, unit = LAYOUT_LIMITS[self.setting]
            if not low <= self.value <= high:
                raise ValueError(f"{self.setting} must be {low}-{high}{unit}")
            if self.setting == "font_size" and self.value not in (10, 11, 12):
                raise ValueError("font_size must be 10, 11 or 12 (what the article class offers)")
        elif self.kind == "drop_item" and not self.match.strip():
            raise ValueError("a drop_item step names words the bullet contains")
        elif self.kind in ("drop_section", "replace_section") and not self.section.strip():
            raise ValueError(f"a {self.kind} step names its section")
        if self.kind == "replace_section":
            # The model fences code now and then; the fence must never reach
            # the PDF.
            self.latex_body = strip_fences(self.latex_body)
            problems = validate_body(self.latex_body)
            if problems or not self.latex_body.strip():
                raise ValueError("the shorter section is not usable LaTeX: "
                                 + ("; ".join(problems) or "it is empty"))
        if not self.label.strip():
            self.label = describe_step(self)
        return self


class FitPolicy(BaseModel):
    steps: list[FitStep] = Field(default_factory=list)
    # Read by the tailoring model, never executed: "never cut AWS", "keep
    # the summary to two lines".
    guidance: str = ""
    agreed_at: str = ""


DEFAULT_STEPS: list[dict[str, Any]] = [
    {"kind": "drop_item", "section": "Certifications", "match": "ccna", "label": "Drop the CCNA bullet"},
    {"kind": "drop_section", "section": "Certifications", "label": "Drop the Certifications section"},
]


def default_policy() -> FitPolicy:
    return FitPolicy(steps=[FitStep.model_validate(s) for s in DEFAULT_STEPS])


def describe_step(step: FitStep) -> str:
    if step.kind == "layout":
        unit = LAYOUT_LIMITS[step.setting][2]
        return f"{SETTING_NAMES[step.setting]} to {step.value:g}{unit}"
    if step.kind == "drop_item":
        where = f" in {step.section}" if step.section else ""
        return f"Drop the bullet mentioning '{step.match}'{where}"
    if step.kind == "drop_section":
        return f"Drop the {step.section} section"
    return f"Shorter {step.section} section"


def parse_steps(raw: Any) -> tuple[list[FitStep], list[str]]:
    """Steps that validate, and a sentence for each that did not."""
    steps: list[FitStep] = []
    problems: list[str] = []
    for i, item in enumerate(raw or [], start=1):
        try:
            if isinstance(item, FitStep):
                steps.append(item)
                continue
            data = item.model_dump() if isinstance(item, BaseModel) else item
            steps.append(FitStep.model_validate(data))
        except ValidationError as exc:
            problems.append(f"step {i}: " + "; ".join(e.get("msg", "") for e in exc.errors()))
    return steps, problems


def missing_sections(steps: list[FitStep], source: str) -> list[str]:
    """A sentence for each step naming a section the resume does not have:
    a typo would otherwise be skipped silently on every run."""
    have = {normalize_heading(s.heading) for s in split_sections(source).sections}
    out = []
    for step in steps:
        if step.section and normalize_heading(step.section) not in have:
            out.append(f"'{step.label}' names a section the resume does not have: {step.section}")
    return out


# ------------------------------------------------------------------ storage

def saved_policy() -> FitPolicy | None:
    try:
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        return FitPolicy.model_validate(data)
    except (OSError, ValueError, ValidationError):
        return None


def load() -> FitPolicy:
    """The candidate's agreed policy, or the old fixed rule."""
    return saved_policy() or default_policy()


def save(steps: list[FitStep], guidance: str) -> FitPolicy:
    policy = FitPolicy(steps=steps, guidance=guidance.strip(),
                       agreed_at=datetime.now(timezone.utc).isoformat())
    atomic.write_text(POLICY_PATH, policy.model_dump_json(indent=2))
    return policy


def forget() -> None:
    try:
        POLICY_PATH.unlink()
    except FileNotFoundError:
        pass


def prompt_text(policy: FitPolicy | None = None) -> str:
    """For the tailoring prompt: what happens if its resume overflows."""
    policy = policy or load()
    if policy.steps:
        order = "; ".join(f"{i}. {s.label}" for i, s in enumerate(policy.steps, start=1))
        text = ("if the page overflows, these are applied automatically, in this order, "
                f"until it fits: {order}. So only add what is worth more for this job "
                "than what those steps would cost, and never pad.")
    else:
        text = "nothing is cut automatically if the page overflows, so never add length."
    if policy.guidance:
        text += f" The candidate's own rules for space: {policy.guidance}"
    return text


# ------------------------------------------------------------------ layout steps

_GEOMETRY_PKG = re.compile(r"\\usepackage\[([^\]]*)\]\{geometry\}")
_GEOMETRY_CMD = re.compile(r"\\geometry\{([^}]*)\}")
_MARGIN_OPT = re.compile(r"(?<![a-z])margin\s*=\s*([\d.]+)\s*(in|cm|mm|pt)?")
_DOCCLASS = re.compile(r"\\documentclass(\[([^\]]*)\])?\{")
_PT_OPT = re.compile(r"\b(\d+)pt\b")
_TITLESPACING = re.compile(
    r"\\titlespacing(\*?)\{\\section\}\{([^}]*)\}\{([\d.]+)\s*([a-z]+)\}\{([^}]*)\}")
_BEGIN_DOC = re.compile(r"\\begin\{document\}")
_LIST_OPTS = re.compile(r"\\begin\{itemize\}\[([^\]]*)\]")
_ITEMSEP = re.compile(r"itemsep\s*=\s*[^,\]]+")
ITEM_SPACING_MARK = "% resume fit: space between bullets"
_TO_INCH = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27}


def _before_document(source: str, line: str) -> str:
    match = _BEGIN_DOC.search(source)
    if not match:
        return ""
    return source[:match.start()] + line + "\n" + source[match.start():]


def _uses(source: str, package: str) -> bool:
    return bool(re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*\b" + package + r"\b", source))


def _current(source: str, setting: str) -> float | None:
    """What the resume has now, so a step only ever tightens it: a
    "fit" step that widened the margins would make the resume longer."""
    if setting == "margin":
        for pattern in (_GEOMETRY_PKG, _GEOMETRY_CMD):
            found = pattern.search(source)
            opt = _MARGIN_OPT.search(found.group(1)) if found else None
            if opt:
                return float(opt.group(1)) * _TO_INCH.get(opt.group(2) or "in", 1.0)
        return 1.0 if not (_GEOMETRY_PKG.search(source) or _GEOMETRY_CMD.search(source)) else None
    if setting == "font_size":
        doc = _DOCCLASS.search(source)
        size = _PT_OPT.search(doc.group(2) or "") if doc else None
        return float(size.group(1)) if size else 10.0
    if setting == "section_spacing":
        spacing = _TITLESPACING.search(source)
        return float(spacing.group(3)) if spacing and spacing.group(4) == "ex" else None
    if setting == "item_spacing":
        mark = re.search(r"itemsep=([\d.]+)pt\} " + re.escape(ITEM_SPACING_MARK), source)
        return float(mark.group(1)) if mark else None
    return None


def _layout(source: str, setting: str, value: float) -> str:
    have = _current(source, setting)
    if have is not None and value >= have:
        return ""   # not tighter: nothing to gain, so nothing to compile
    if setting == "margin":
        for pattern in (_GEOMETRY_PKG, _GEOMETRY_CMD):
            found = pattern.search(source)
            if not found:
                continue
            options = found.group(1)
            if _MARGIN_OPT.search(options):
                options = _MARGIN_OPT.sub(f"margin={value:g}in", options, count=1)
            else:
                options = f"{options}, margin={value:g}in" if options.strip() else f"margin={value:g}in"
            return source[:found.start(1)] + options + source[found.end(1):]
        return _before_document(source, f"\\usepackage[margin={value:g}in]{{geometry}}")
    if setting == "font_size":
        doc = _DOCCLASS.search(source)
        if not doc:
            return ""
        size = f"{int(value)}pt"
        options = doc.group(2)
        if options is None:
            return source[:doc.start()] + f"\\documentclass[{size}]{{" + source[doc.end():]
        if _PT_OPT.search(options):
            new = _PT_OPT.sub(size, options, count=1)
        else:
            new = f"{size}, {options}" if options.strip() else size
        return source[:doc.start(2)] + new + source[doc.end(2):]
    if setting == "section_spacing":
        spacing = _TITLESPACING.search(source)
        amount = f"{value:g}ex"
        if spacing:
            star, left = spacing.group(1), spacing.group(2)
            return (source[:spacing.start()]
                    + f"\\titlespacing{star}{{\\section}}{{{left}}}{{{amount}}}{{{amount}}}"
                    + source[spacing.end():])
        extra = "" if _uses(source, "titlesec") else "\\usepackage{titlesec}\n"
        return _before_document(source, f"{extra}\\titlespacing{{\\section}}{{0pt}}{{{amount}}}{{{amount}}}")
    if setting == "item_spacing":
        # A list's own [nosep] or [itemsep=...] beats a global \setlist, so
        # every list is told as well; on a resume where every list is
        # [nosep] and the value is 0 there is nothing to tighten.
        kept = "\n".join(ln for ln in source.split("\n") if ITEM_SPACING_MARK not in ln)

        def retune(match: re.Match) -> str:
            opts = match.group(1)
            if _ITEMSEP.search(opts):
                opts = _ITEMSEP.sub(f"itemsep={value:g}pt", opts, count=1)
            elif re.search(r"\bnosep\b", opts):
                opts = f"{opts}, itemsep={value:g}pt" if value else opts
            else:
                opts = f"{opts}, itemsep={value:g}pt" if opts.strip() else f"itemsep={value:g}pt"
            return f"\\begin{{itemize}}[{opts}]"

        retuned = _LIST_OPTS.sub(retune, kept)
        line = f"\\setlist[itemize]{{itemsep={value:g}pt}} {ITEM_SPACING_MARK}"
        extra = "" if _uses(retuned, "enumitem") else "\\usepackage{enumitem}\n"
        return _before_document(retuned, extra + line)
    return ""


# ------------------------------------------------------------------ content steps

_SECTION_RE = re.compile(r"\\section\*?\{")
_TOKEN = re.compile(r"\\begin\{itemize\}(\[[^\]]*\])?|\\end\{itemize\}|\\item\b")


def _section_span(source: str, heading: str) -> tuple[int, int] | None:
    """Where the named section's body sits in the source."""
    parsed = split_sections(source)
    target = normalize_heading(heading)
    offset = len(parsed.prologue)
    for section in parsed.sections:
        start = offset + len(section.command)
        end = start + len(section.body)
        if normalize_heading(section.heading) == target:
            return start, end
        offset = end
    return None


def _bullets(source: str, lo: int, hi: int) -> list[tuple[int, int, int, int]]:
    """Every bullet in [lo, hi): (start, end, list_start, list_end), where
    the bullet runs to the next \\item or \\end at ITS OWN depth - a nested
    list inside it stays with it - and list_start/list_end bound its list."""
    tokens = list(_TOKEN.finditer(source, lo, hi))
    stack: list[int] = []            # positions of open \begin{itemize}
    opened: dict[int, int] = {}      # list start -> matching \end start
    pairs: list[tuple[int, int]] = []
    for tok in tokens:
        text = tok.group(0)
        if text.startswith("\\begin"):
            stack.append(tok.start())
        elif text.startswith("\\end") and stack:
            start = stack.pop()
            opened[start] = tok.end()
            pairs.append((start, tok.end()))
    out = []
    depth_stack: list[int] = []
    for i, tok in enumerate(tokens):
        text = tok.group(0)
        if text.startswith("\\begin"):
            depth_stack.append(tok.start())
            continue
        if text.startswith("\\end"):
            if depth_stack:
                depth_stack.pop()
            continue
        if not depth_stack:
            continue
        list_start = depth_stack[-1]
        depth = len(depth_stack)
        end = hi
        level = depth
        for later in tokens[i + 1:]:
            later_text = later.group(0)
            if later_text.startswith("\\begin"):
                level += 1
            elif later_text.startswith("\\end"):
                if level == depth:
                    end = later.start()
                    break
                level -= 1
            elif level == depth:
                end = later.start()
                break
        out.append((tok.start(), end, list_start, opened.get(list_start, hi)))
    return out


def _drop_item(source: str, needle: str, section: str = "") -> str:
    lo, hi = 0, len(source)
    if section:
        span = _section_span(source, section)
        if span is None:
            return ""
        lo, hi = span
    lowered = needle.lower()
    bullets = _bullets(source, lo, hi)
    for start, end, list_start, list_end in bullets:
        if lowered not in source[start:end].lower():
            continue
        siblings = [b for b in bullets if b[2] == list_start]
        if len(siblings) == 1:
            # The only bullet: an empty itemize is a LaTeX error, so the
            # list goes with it (and the line it sat on).
            cut_end = list_end
            if source[cut_end:cut_end + 1] == "\n":
                cut_end += 1
            return source[:list_start] + source[cut_end:]
        return source[:start] + source[end:]
    return ""


def _drop_section(source: str, heading: str) -> str:
    parsed = split_sections(source)
    target = normalize_heading(heading)
    if not any(normalize_heading(s.heading) == target for s in parsed.sections):
        return ""
    parts = [parsed.prologue]
    for section in parsed.sections:
        if normalize_heading(section.heading) != target:
            parts += [section.command, section.body]
    parts.append(parsed.epilogue)
    return "".join(parts)


def _replace_section(source: str, heading: str, body: str) -> str:
    span = _section_span(source, heading)
    if span is None:
        return ""
    lo, hi = span
    text = body if body.startswith("\n") else "\n" + body
    text = text if text.endswith("\n") else text + "\n"
    if source[lo:hi] == text:
        return ""
    return source[:lo] + text + source[hi:]


def apply_step(source: str, step: FitStep) -> str:
    """The source with one step applied, or '' when it changes nothing (the
    section is not there, the bullet is already gone, the layout is already
    at least that tight)."""
    if step.kind == "layout":
        changed = _layout(source, step.setting, step.value)
    elif step.kind == "drop_item":
        changed = _drop_item(source, step.match, step.section)
    elif step.kind == "drop_section":
        changed = _drop_section(source, step.section)
    else:
        changed = _replace_section(source, step.section, step.latex_body)
    return changed if changed and changed != source else ""


# ------------------------------------------------------------------ old runs

_ITEM = re.compile(r"\\item\b")


def restore_trims(tailored: str, base: str) -> str:
    """Put back what the old fixed rule cut from a tailored resume, from the
    base resume: a whole section that is missing, or a section that lost
    bullets the base has. For runs made before the full version was kept.
    Sections keep their order; the tailored text is kept wherever it has as
    much as the base. Returns the tailored source unchanged when nothing
    was cut."""
    have = split_sections(tailored)
    base_parsed = split_sections(base)
    if not have.sections or not base_parsed.sections:
        return tailored
    base_by = {normalize_heading(s.heading): s for s in base_parsed.sections}
    have_keys = {normalize_heading(s.heading) for s in have.sections}
    base_order = [normalize_heading(s.heading) for s in base_parsed.sections]
    out = [have.prologue]
    emitted: set[str] = set()

    def base_sections_before(key: str) -> None:
        # Base sections that come before `key` and are missing from the
        # tailored resume go back where they were.
        for earlier in base_order:
            if earlier == key:
                break
            if earlier not in have_keys and earlier not in emitted:
                out.extend([base_by[earlier].command, base_by[earlier].body])
                emitted.add(earlier)

    for section in have.sections:
        key = normalize_heading(section.heading)
        base_sections_before(key)
        base_section = base_by.get(key)
        body = section.body
        if base_section is not None:
            mine_items = [m.start() for m in _ITEM.finditer(body)]
            base_items = [m.start() for m in _ITEM.finditer(base_section.body)]
            if (len(base_items) > len(mine_items)
                    and all(_bullet(body, i) in base_section.body for i in mine_items)):
                body = base_section.body
        out.extend([section.command, body])
        emitted.add(key)
    for key in base_order:
        if key not in have_keys and key not in emitted:
            out.extend([base_by[key].command, base_by[key].body])
    out.append(have.epilogue)
    return "".join(out)


def _bullet(body: str, start: int) -> str:
    nxt = _ITEM.search(body, start + 1)
    return body[start:nxt.start() if nxt else len(body)].split("\\end{itemize}")[0].strip()
