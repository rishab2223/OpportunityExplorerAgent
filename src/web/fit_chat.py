"""The Resume fit tab: agree with the model on what gives way when a tailored
resume runs past one page.

The conversation is the candidate's and the model's; the policy it produces
is a list of mechanical steps (src/resume/fit_policy.py) that this module
checks, applies to a real resume and compiles, so every turn ends with a PDF
showing exactly what a run would do. Nothing is used by a run until the
candidate presses Save - the model can say it thinks they are done, but the
closing is theirs.

The draft (conversation, steps, guidance) is kept in
localData/resume_fit_draft.json so a reload or a restart loses nothing.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src import atomic
from src.agent.nodes.dump import FULL_DIR
from src.config import ROOT, load_env
from src.resume import fit_policy
from src.resume.one_page import fit_to_one_page
from src.web import run_options, runs

DRAFT_PATH = ROOT / "localData" / "resume_fit_draft.json"
PREVIEW_DIR = ROOT / "localData" / "fit_preview"
BASE_SUBJECT = "base"
# How many messages of the conversation go back to the model each turn.
HISTORY_MESSAGES = 24
# Previews kept on disk and remembered, so going back to a step order already
# seen costs no compile.
PREVIEWS_KEPT = 8
_PREVIEW_LOCK = threading.Lock()
_previews: dict[str, dict[str, Any]] = {}

OPENING = (
    "When a tailored resume runs past one page, something has to give. Right now "
    "that is your CCNA bullet first, then the whole Certifications section. Tell me "
    "what you would rather give up, and in what order - or ask what the options are. "
    "I can tighten the margins, font size or spacing, drop a particular bullet or "
    "section, or write a shorter version of a section for you to approve. The preview "
    "on the right shows what a run would do to a resume that overflowed."
)

SYSTEM = """You help a job candidate decide what should give way when their \
tailored resume runs past one page. Their resume is LaTeX. A run tailors it \
for each job, compiles it, and if it is longer than one page applies the \
candidate's FIT POLICY: an ordered list of steps, applied one at a time with \
a recompile after each, stopping as soon as it fits. Your job is to agree \
that policy with them in conversation.

Step kinds (the only things a policy can do):
- layout: setting "margin" (inches, 0.4-1.0), "font_size" (10, 11 or 12 pt), \
"section_spacing" (ex, 0-2: space around section headings) or "item_spacing" \
(pt, 0-6: space between bullets), with value. A layout step only ever \
tightens: a value looser than the resume already has does nothing.
- drop_item: remove the one bullet containing `match` (in `section` if given). \
If it is the only bullet in its list, the list goes with it.
- drop_section: remove `section` entirely.
- replace_section: swap `section` for latex_body, a shorter version you write \
now from the resume's own text, for the candidate to approve. It is a FIXED \
text that replaces whatever the tailoring wrote in that section, so use it \
only for sections the tailoring leaves alone (Education, Certifications, \
Projects) - never Summary or Skills, which are tailored per job. Reuse the \
section's exact environments and macros; never invent facts; no \\section, \
\\documentclass or \\usepackage inside it.

Each turn, return:
- reply: what you say to the candidate. Conversational, short, plain words. \
Explain trade-offs when they matter (recruiters skim; a 0.5in margin looks \
cramped; certifications matter more for some roles). Ask one question at a \
time when you need to.
- steps: the COMPLETE policy as it now stands, in order - not just the change. \
Keep the candidate's earlier decisions unless they change their mind. Cheap, \
invisible steps (small layout changes) usually come before losing content.
- guidance: optional short rules for the model that tailors each resume \
(e.g. "never remove the AWS certification", "keep the summary to two lines"). \
Empty if none.
- ready_to_save: true only when the candidate has said they are happy with \
the policy as it stands; they still press Save themselves.

You get the resume source, the current policy, and what the last preview \
showed (pages before, and after each step that ran). Use section headings \
exactly as they appear in the resume."""


class FitTurn(BaseModel):
    reply: str = ""
    steps: list[fit_policy.StepFields] = Field(default_factory=list)
    guidance: str = ""
    ready_to_save: bool = False


# ------------------------------------------------------------------ draft

def load_draft() -> dict[str, Any]:
    try:
        data = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return data
    except (OSError, ValueError):
        pass
    start = fit_policy.load()
    return {
        "messages": [{"role": "assistant", "text": OPENING}],
        "steps": [s.model_dump() for s in start.steps],
        "guidance": start.guidance,
        "ready_to_save": False,
        "subject": "",
    }


def save_draft(draft: dict[str, Any]) -> None:
    atomic.write_text(DRAFT_PATH, json.dumps(draft, indent=2))


def reset_draft() -> dict[str, Any]:
    try:
        DRAFT_PATH.unlink()
    except FileNotFoundError:
        pass
    return load_draft()


def state() -> dict[str, Any]:
    """Everything the tab needs on open."""
    saved = fit_policy.saved_policy()
    return {
        "draft": load_draft(),
        "policy": saved.model_dump() if saved else None,
        "subjects": subjects(),
    }


def restart() -> dict[str, Any]:
    reset_draft()
    return state()


# ------------------------------------------------------------------ subjects

def _config():
    return run_options.effective_config()


def _base_source() -> str:
    path = Path(_config().resume.local_path or "")
    path = path if path.is_absolute() else ROOT / path
    if path.suffix.lower() != ".tex":
        raise ValueError("the base resume is not a .tex file, so there is nothing to fit")
    return path.read_text(encoding="utf-8")


def subjects(limit_runs: int = 3) -> list[dict[str, Any]]:
    """Resumes to preview the policy on: the tailored ones that overflowed
    first (where a policy actually does something), then the rest, then the
    base resume. Reads the run files only, no database."""
    try:
        base = _base_source()
    except (OSError, ValueError):
        base = ""
    over: list[dict[str, Any]] = []
    fine: list[dict[str, Any]] = []
    for stamp in runs.list_stamps()[:limit_runs]:
        try:
            rows = runs.raw_rows(stamp)
            directory = runs.run_dir(stamp)
        except runs.RunNotFound:
            continue
        for row in rows:
            name = row.get("resume_tex_file") or ""
            if not name or row.get("held_back"):
                continue
            entry = {"id": f"{stamp}/{row.get('job_id')}",
                     "label": f"{row.get('company') or '?'} · {row.get('title') or '?'}",
                     "stamp": stamp, "overflowed": False}
            if (directory / FULL_DIR / name).exists():
                entry["overflowed"] = True
            elif base and (directory / name).exists():
                tailored = (directory / name).read_text(encoding="utf-8", errors="replace")
                entry["overflowed"] = fit_policy.restore_trims(tailored, base) != tailored
            (over if entry["overflowed"] else fine).append(entry)
    found = over + fine
    if base:
        found.append({"id": BASE_SUBJECT, "label": "Your base resume", "overflowed": False})
    return found


def subject_source(subject_id: str) -> str:
    """The resume as written, before any fit step - the full version a run
    kept, or one rebuilt for runs made before it kept them."""
    base = _base_source()
    if not subject_id or subject_id == BASE_SUBJECT:
        return base
    stamp, _, job_id = subject_id.partition("/")
    row = next((r for r in runs.raw_rows(stamp) if r.get("job_id") == job_id), None)
    if row is None:
        raise runs.JobNotFound(f"no job {job_id} in run {stamp}")
    name = row.get("resume_tex_file") or ""
    if not name:
        raise ValueError("that job has no tailored resume")
    directory = runs.run_dir(stamp)
    full = directory / FULL_DIR / name
    if full.exists():
        return full.read_text(encoding="utf-8")
    return fit_policy.restore_trims((directory / name).read_text(encoding="utf-8"), base)


# ------------------------------------------------------------------ preview

def _preview_id(source: str, steps: list[fit_policy.FitStep]) -> str:
    digest = hashlib.sha256(source.encode("utf-8"))
    digest.update(json.dumps([s.model_dump() for s in steps], sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]


def preview(subject_id: str, raw_steps: list[Any]) -> dict[str, Any]:
    """Apply the steps to the subject exactly as a run would, compile, and say
    what happened. A preview already built for this source and these steps
    is handed back without compiling again."""
    steps, problems = fit_policy.parse_steps(raw_steps)
    source = subject_source(subject_id)
    problems += fit_policy.missing_sections(steps, source)
    pid = _preview_id(source, steps)
    with _PREVIEW_LOCK:
        done = _previews.get(pid)
        if done is not None and (PREVIEW_DIR / f"preview_{pid}.pdf").exists():
            return dict(done, problems=problems)
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        tex = PREVIEW_DIR / f"preview_{pid}.tex"
        tex.write_text(source, encoding="utf-8")
        result = fit_to_one_page(tex, steps=steps)
        pdf = tex.with_suffix(".pdf")
        built = {
            "pages": result.pages,
            "applied": result.cuts,
            "trail": [{"step": label, "pages": pages} for label, pages in result.trail],
            "note": result.note,
            "pdf": f"/api/fit/preview.pdf?v={pid}" if pdf.exists() else "",
        }
        _previews[pid] = built
        # The newest few stay for Back/Forward through step orders; the rest
        # go, best effort (a PDF still being sent cannot be deleted on Windows).
        for old in list(_previews)[:-PREVIEWS_KEPT]:
            _previews.pop(old, None)
            for stale in PREVIEW_DIR.glob(f"preview_{old}.*"):
                try:
                    stale.unlink()
                except OSError:
                    pass
    return dict(built, problems=problems)


def preview_pdf(version: str) -> Path | None:
    if not version.isalnum():
        return None
    pdf = PREVIEW_DIR / f"preview_{version}.pdf"
    return pdf if pdf.exists() else None


def _preview_words(result: dict[str, Any] | None) -> str:
    if not result:
        return "No preview yet."
    trail = result.get("trail") or []
    if not trail:
        return f"The preview did not compile: {result.get('note') or 'unknown error'}"
    parts = [f"{t['step']}: {t['pages']} page(s)" for t in trail]
    words = "Last preview - " + "; then ".join(parts) + "."
    if result.get("note"):
        words += f" ({result['note']})"
    if result.get("problems"):
        words += " Steps refused: " + "; ".join(result["problems"])
    return words


def _try_preview(subject_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return preview(subject_id, steps)
    except Exception as exc:
        return {"pages": 0, "applied": [], "trail": [], "note": str(exc), "problems": [], "pdf": ""}


# ------------------------------------------------------------------ chat

def chat(text: str, subject_id: str, invoke=None) -> dict[str, Any]:
    """One turn: the candidate's message in, the model's reply and revised
    policy out, previewed on the subject. The draft is saved either way."""
    text = (text or "").strip()
    if not text:
        raise ValueError("say something first")
    draft = load_draft()
    messages = list(draft.get("messages") or []) + [{"role": "you", "text": text}]
    if invoke is None:
        from src.llm import make_invoker

        invoke = make_invoker(_config(), load_env(), "enrich")

    source = subject_source(subject_id)   # a resume that cannot be read is an error, not a silent swap
    current = {"steps": draft.get("steps") or [], "guidance": draft.get("guidance") or ""}
    history = "\n".join(f"{m['role'].upper()}: {m['text']}" for m in messages[-HISTORY_MESSAGES:])
    user = (
        f"RESUME_LATEX (as written, before any fit step):\n{source}\n\n"
        f"CURRENT POLICY:\n{json.dumps(current, indent=1)}\n\n"
        f"{_preview_words(draft.get('last_preview'))}\n\n"
        f"CONVERSATION (latest last):\n{history}"
    )
    turn = invoke(SYSTEM, user, FitTurn)
    steps, problems = fit_policy.parse_steps(turn.steps)
    problems += fit_policy.missing_sections(steps, source)
    reply = turn.reply.strip() or "Done - have a look at the preview."
    if problems:
        reply += "\n\n(Some of my steps could not be used: " + "; ".join(problems) + ")"
    # The model took its time: anything the candidate did meanwhile (a
    # reorder, a reset) is in the draft on disk now, so build on that.
    draft = load_draft()
    draft.update({
        "messages": list(draft.get("messages") or []) + [{"role": "you", "text": text},
                                                         {"role": "assistant", "text": reply}],
        "steps": [s.model_dump() for s in steps],
        "guidance": turn.guidance.strip(),
        "ready_to_save": bool(turn.ready_to_save),
        "subject": subject_id,
    })
    draft["last_preview"] = _try_preview(subject_id, draft["steps"])
    save_draft(draft)
    return draft


def edit_steps(raw_steps: list[Any], guidance: str, subject_id: str) -> dict[str, Any]:
    """The candidate changed the policy by hand (removed or reordered a
    step), or picked another resume to try it on."""
    steps, problems = fit_policy.parse_steps(raw_steps)
    if problems:
        raise ValueError("; ".join(problems))
    draft = load_draft()
    changed = [s.model_dump() for s in steps] != (draft.get("steps") or [])
    draft.update({"steps": [s.model_dump() for s in steps], "guidance": guidance.strip(),
                  "subject": subject_id})
    if changed:
        draft["ready_to_save"] = False   # not what the assistant agreed to any more
    draft["last_preview"] = _try_preview(subject_id, draft["steps"])
    save_draft(draft)
    return draft


def agree(raw_steps: list[Any], guidance: str) -> dict[str, Any]:
    """Save the policy every run uses from now on."""
    steps, problems = fit_policy.parse_steps(raw_steps)
    if problems:
        raise ValueError("; ".join(problems))
    policy = fit_policy.save(steps, guidance)
    draft = load_draft()
    draft["messages"] = (draft.get("messages") or []) + [{
        "role": "assistant",
        "text": "Saved. From the next run on, a resume that runs past one page gets: "
                + ("; ".join(f"{i}. {s.label}" for i, s in enumerate(policy.steps, 1))
                   or "nothing cut - it is flagged instead") + ".",
    }]
    draft["ready_to_save"] = False
    save_draft(draft)
    return {"policy": policy.model_dump(), "draft": draft}


def back_to_default() -> dict[str, Any]:
    fit_policy.forget()
    return state()
