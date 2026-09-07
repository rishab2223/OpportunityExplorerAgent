"""Cover letters, written when a form actually asks for one.

Nothing is generated ahead of time: a form with no cover-letter field costs
nothing. When one is detected (or you press the button), the model drafts a
short letter, you edit it in the modal - free - or ask for changes, and only
then is it filled in or compiled to a small PDF and uploaded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.agent.filenames import sanitize_filename_part
from src.pdf_compile import compile_tex

# Narrow on purpose: a generic "Why do you want this role?" textarea stays with
# the normal per-field flow, which already handles free text.
COVER_LETTER_RE = re.compile(
    r"cover[\s_-]*letter|covering[\s_-]*letter"
    r"|motivation[\s_-]*(letter|statement)|letter[\s_-]*of[\s_-]*motivation",
    re.IGNORECASE,
)

SYSTEM = (
    "You write a short cover letter for one specific job, in the candidate's "
    "own voice. Roughly three short paragraphs of three to four lines each "
    "(about 120-170 words total): why this role at this company, the one or "
    "two most relevant things the candidate has actually done, and a plain "
    "close. Write like a person: plain words, varied sentence lengths, no "
    "buzzword strings, no 'I am excited to leverage', no throat-clearing. "
    "Never invent employers, dates, metrics or skills - use only what the "
    "resume and profile support. Output the letter body only: no address "
    "block, no date, no 'Dear Hiring Manager' header, no sign-off line (those "
    "are added around it)."
)

REVISE_SYSTEM = (
    "You revise a cover letter. Apply ONLY the change the candidate asks for "
    "and leave everything else exactly as it is. Keep the same voice and "
    "roughly the same length unless the change is about length. Output the "
    "revised letter body only."
)

# Substituted in ONE pass: sequential str.replace would re-escape the braces
# that \textbackslash{} and friends introduce.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESCAPE_RE = re.compile("[" + re.escape("".join(_ESCAPES)) + "]")

TEMPLATE = r"""\documentclass[11pt, letterpaper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{parskip}
\begin{document}
\pagestyle{empty}

\noindent \textbf{%(name)s}%(contact)s

\vspace{12pt}

%(salutation)s%(body)s%(signoff)s

\end{document}
"""

# A greeting or sign-off the user typed into the body themselves wins over the
# template's own - never render "Dear X team," or "Regards," twice.
_SALUTATION_RE = re.compile(r"^(dear|hi|hello|respected|greetings)\b", re.IGNORECASE)
_SIGNOFF_RE = re.compile(
    r"^(regards|best regards|kind regards|warm regards|sincerely|best wishes"
    r"|best|thanks|thank you|yours)\b[,.!]?\s*$",
    re.IGNORECASE,
)


class CoverLetter(BaseModel):
    text: str = ""


def is_cover_letter(field: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(field.get(key) or "") for key in ("label", "name", "elid", "group", "text")
    )
    return bool(COVER_LETTER_RE.search(haystack))


def escape_latex(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group()], text or "")


def frame(text: str, job: dict[str, Any], profile: dict[str, Any]) -> str:
    """The complete letter as the user should see it in the modal: greeting and
    sign-off added when the draft lacks them, so the editable text IS the
    letter - nothing is appended invisibly later. build_pdf never duplicates
    lines that are already present."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if not paras:
        return (text or "").strip()
    company = str(job.get("company") or "").strip()
    name = str(profile.get("full_name") or "").strip() or "Candidate"
    framed = list(paras)
    if not _SALUTATION_RE.match(paras[0]):
        framed.insert(0, f"Dear {company} team," if company else "Dear Hiring Team,")
    if not _SIGNOFF_RE.match(paras[-1].splitlines()[0].strip()):
        framed.append(f"Regards,\n{name}")
    return "\n\n".join(framed)


def load_saved(job_id: str) -> str:
    """The letter drafted for this job in an earlier session, or ''. An
    aborted session must not cost a second draft call: the text is kept in
    the local database and the modal reopens on it."""
    if not job_id:
        return ""
    from src import db

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT text FROM cover_letters WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    return (row["text"] if row else "") or ""


def save(job_id: str, job: dict[str, Any], text: str) -> None:
    """Keep the current letter for this job: after the draft, each revision
    and the accepted edit, so whatever the session did last survives it."""
    if not job_id or not (text or "").strip():
        return
    from datetime import datetime, timezone

    from src import db

    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO cover_letters (job_id, company, title, text, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(job_id) DO UPDATE SET text = excluded.text,"
                "  updated_at = excluded.updated_at",
                (job_id, str(job.get("company") or ""), str(job.get("title") or ""),
                 text, datetime.now(timezone.utc).isoformat()),
            )
    finally:
        conn.close()


def draft(invoke, job: dict[str, Any], resume_text: str, profile_text: str) -> str:
    user = "\n\n".join(
        [
            f"JOB: {job.get('title', '')} at {job.get('company', '')}",
            f"JOB DESCRIPTION:\n{(job.get('description') or '')[:3000]}",
            f"CANDIDATE PROFILE:\n{profile_text}",
            f"RESUME:\n{resume_text[:4000]}",
            "Write the cover letter body.",
        ]
    )
    return (invoke(SYSTEM, user, CoverLetter).text or "").strip()


def revise(invoke, text: str, instruction: str) -> str:
    user = "\n\n".join(
        [f"CURRENT LETTER:\n{text}", f"REQUESTED CHANGE:\n{instruction}", "Return the revised letter."]
    )
    return (invoke(REVISE_SYSTEM, user, CoverLetter).text or "").strip()


def build_pdf(
    text: str, out_dir: Path, job: dict[str, Any], profile: dict[str, Any]
) -> tuple[Path | None, str]:
    """Compile the accepted letter into a small one-page PDF."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if not paras:
        return None, "the cover letter is empty"
    body = "\n\n".join(escape_latex(p) for p in paras)
    name = str(profile.get("full_name") or "").strip() or "Candidate"
    bits = [str(profile.get(k) or "").strip() for k in ("email", "phone")]
    contact = " \\\\\n" + escape_latex(" | ".join(b for b in bits if b)) if any(bits) else ""
    company = str(job.get("company") or "").strip()
    salutation = ""
    if not _SALUTATION_RE.match(paras[0]):
        greeting = f"Dear {company} team," if company else "Dear Hiring Team,"
        salutation = "\\noindent " + escape_latex(greeting) + "\n\n"
    signoff = ""
    if not _SIGNOFF_RE.match(paras[-1].splitlines()[0].strip()):
        signoff = (
            "\n\n\\vspace{12pt}\n\n\\noindent Regards, \\\\\n" + escape_latex(name)
        )
    source = TEMPLATE % {
        "name": escape_latex(name),
        "contact": contact,
        "salutation": salutation,
        "body": body,
        "signoff": signoff,
    }
    stem = "cover_" + sanitize_filename_part(
        f"{job.get('company', '')}_{job.get('title', '')}".strip("_")
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{stem}.tex"
    tex_path.write_text(source, encoding="utf-8")
    return compile_tex(tex_path)
