"""Referral, outreach, follow-up and thank-you messages for an application,
and the small chat that edits them (#42, Oct 2026).

Drafts only. The candidate sends every message themselves - LinkedIn's terms
forbid automating that - and presses Mark sent, which sets the follow-up date.

The model gets the posting, the candidate's profile and resume, and the
contact as the candidate described them. It may mention a shared employer or
college ("the connection line") only when the contact's relation says so, and
never invents a fact: no metric, employer or skill that is not in the resume.
The candidate's own AI project work is projects, never a job.

The chat keeps one Claude process for the server's life (when apply runs on
Claude's agent SDK): a fresh one takes ~17 s to start, a kept one answers in
two or three.
"""

from __future__ import annotations

import atexit
import threading
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from src import tracker
from src.config import load_env

KIND_WORDS = {
    "referral": "a referral request to someone at the company: ask them to refer the candidate for this "
                "role, say in one line why the candidate fits, offer the resume",
    "outreach": "a note to the recruiter or hiring manager after applying: the role and when they applied, "
                "one or two lines of fit from the resume, an offer to share more",
    "follow_up": "a polite follow-up on the application or an earlier message: still interested, available, "
                 "asks if anything else would help - no pressure, no guilt",
    "thank_you": "a thank-you after an interview: thanks for the time, one specific thing from the role "
                 "or company the candidate is excited about, looking forward to next steps",
}

SYSTEM = """You write short job-search messages that a candidate sends \
themselves on LinkedIn or by e-mail, and you edit them when asked.

Rules:
- Plain, warm, professional; 50-120 words unless asked otherwise. No subject \
line unless asked. First person, the candidate's voice.
- Only facts from the CANDIDATE PROFILE and RESUME. Never invent a metric, \
employer, title, skill or year.
- Entries listed under NOT EMPLOYMENT are the candidate's own projects: call \
them projects, never a job.
- The connection line: when CONTACT says how the candidate knows them (a \
shared past employer, the same college, met at an event), use it in one short \
line. Otherwise do not claim any connection.
- When the contact's name is unknown, greet with "Hi [Name],".
- The greeting and sign-off use the candidate's first name; no placeholders \
for anything else.

Each turn return:
- draft: the complete message as it should now read.
- reply: one short sentence to the candidate about what you changed (or wrote)."""


class MessageTurn(BaseModel):
    draft: str = ""
    reply: str = ""


_lock = threading.Lock()
_session = None


def _invoker():
    """The pick model (Sonnet by default), on a kept process when apply runs
    on the agent SDK."""
    global _session
    from src.llm import make_invoker, open_claude_session
    from src.web import run_options

    cfg = run_options.effective_config()
    env = load_env()
    if cfg.apply_provider == "claude" and cfg.claude.backend == "agent-sdk":
        if _session is None:
            _session = open_claude_session(cfg)
            if _session is not None:
                atexit.register(_session.close)
        return make_invoker(cfg, env, "pick", claude_session=_session)
    return make_invoker(cfg, env, "pick")


def warm() -> bool:
    """Start the kept process in the background (the page opening is the
    cue), so the first Draft it does not wait ~20 s for it. True if started."""
    try:
        invoke = _invoker()
    except Exception:
        return False
    if hasattr(invoke, "warm"):
        invoke.warm(SYSTEM, MessageTurn)
        return True
    return False


def _candidate() -> str:
    """The profile facts a message may use, and the resume's text."""
    from src.apply import profile, worker
    from src.resume.loader import load_resume
    from src.web import run_options

    data = profile.load_profile()
    keys = ("full_name", "current_title", "current_company", "total_experience_years", "notice_period",
            "location", "skills", "highest_education_level", "field_of_study", "university",
            "graduation_year", "linkedin", "github")
    facts = "\n".join(f"{k}: {data[k]}" for k in keys if data.get(k))
    try:
        resume = load_resume(run_options.effective_config())[0]
    except Exception:
        resume = ""
    return (f"CANDIDATE PROFILE:\n{facts}\n\n{worker._not_employment_note()}\n\n"
            f"RESUME:\n{resume[:7000]}")


def _context(app: dict[str, Any], kind: str) -> str:
    contact = app.get("contact") or {}
    who = ", ".join(x for x in (contact.get("name"), contact.get("role")) if x) or "unknown"
    relation = contact.get("relation") or "not given - claim no connection"
    return (
        # Today's date: without it the model guessed "I applied yesterday".
        f"TODAY: {datetime.now():%d %b %Y}\n"
        f"MESSAGE: {kind.replace('_', ' ')} - {KIND_WORDS[kind]}\n"
        f"JOB: {app.get('title')} at {app.get('company')} ({app.get('location') or 'location not given'})\n"
        f"APPLIED: {str(app.get('applied_at') or '')[:10]} via {app.get('source') or 'the site'}; "
        f"stage now: {app.get('stage')}\n"
        f"CONTACT: {who}; how the candidate knows them: {relation}\n\n"
        f"POSTING:\n{(app.get('description') or '')[:5000]}\n\n"
        f"{_candidate()}"
    )


def _ask(user: str, invoke=None) -> MessageTurn:
    with _lock:
        call = invoke or _invoker()
        result = call(SYSTEM, user, MessageTurn)
    return result if isinstance(result, MessageTurn) else MessageTurn.model_validate(result)


def draft(job_id: str, kind: str, invoke=None) -> dict[str, Any]:
    """A fresh draft of this message, saved, replacing any earlier one."""
    if kind not in tracker.MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {tracker.MESSAGE_KINDS}")
    app = tracker.detail(job_id)
    turn = _ask(_context(app, kind) + "\n\nWrite the message.", invoke)
    text = turn.draft.strip()
    if not text:
        raise ValueError("the model returned an empty draft")
    tracker.save_message(job_id, kind, text)
    return {"draft": text, "reply": turn.reply.strip() or "Here is a draft."}


def chat(job_id: str, kind: str, text: str, current: str = "",
         history: list[dict[str, str]] | None = None, invoke=None) -> dict[str, Any]:
    """The candidate asks for a change ("shorter", "drop the college line");
    the model rewrites the draft. The new draft is saved."""
    if kind not in tracker.MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {tracker.MESSAGE_KINDS}")
    text = (text or "").strip()
    if not text:
        raise ValueError("say what to change")
    app = tracker.detail(job_id)
    stored = (tracker.get_message(job_id, kind) or {}).get("text") or ""
    current = (current or stored).strip()
    said = "\n".join(f"{m.get('role', 'you').upper()}: {m.get('text', '')}"
                     for m in (history or [])[-12:])
    user = (_context(app, kind)
            + f"\n\nCURRENT DRAFT:\n{current or '(none yet - write one)'}"
            + (f"\n\nEARLIER IN THIS CHAT:\n{said}" if said else "")
            + f"\n\nTHE CANDIDATE ASKS: {text}")
    turn = _ask(user, invoke)
    new = turn.draft.strip() or current
    tracker.save_message(job_id, kind, new)
    return {"draft": new, "reply": turn.reply.strip() or "Updated."}
