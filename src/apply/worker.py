from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.apply import browser, profile, session
from src.apply.session import Aborted, ApplySession
from src.config import AppConfig, EnvSettings

MAX_STEPS = 80
LOW_CONFIDENCE = 0.6
HISTORY_LIMIT = 12
MAX_ATTEMPTS_PER_FIELD = 3

MAX_CONSECUTIVE_ERRORS = 5
MAX_NOOP_STREAK = 4

SUBMIT_WORDS = ("submit", "apply now", "send application", "finish", "submit application")
CONFIRM_WORDS = ("apply", "yes", "confirm", "submit", "go ahead")
CONTINUE_WORDS = ("done", "ok", "next", "continue", "ready")
FINISHED_WORDS = ("done", "applied", "submitted", "finished", "it went through")
SKIP_WORDS = ("skip", "skip it", "leave it", "leave blank", "ignore", "no answer")
AFFIRMATIVE_WORDS = ("yes", "y", "true", "check", "agree", "accept", "confirm", "tick")
LEGAL_WORDS = (
    "agree",
    "consent",
    "certify",
    "declare",
    "authorize",
    "terms",
    "privacy",
    "gdpr",
    "sponsorship",
    "visa",
    "disability",
    "veteran",
    "ethnicity",
    "gender",
    "race",
)

SYSTEM = """You are filling in one job application form in a browser for a candidate.
You are given the visible form fields (each with a numeric id), the page text, the
candidate profile, previously learned answers, and the candidate's resume.

Rules:
- Only use values that come from the profile, the learned answers, or the resume.
- Never invent visa status, salary, notice period, legal declarations, or demographics.
- If a field is not covered by that information, use action "ask" and write a short,
  specific question for the candidate.
- Use "ask" for one-time passwords, captchas, consent or legal checkboxes, and anything
  you are unsure about.
- A tailored resume PDF is already attached to this session. For any file input, use
  action "upload" and leave value empty; never ask the candidate for the file or its path.
- Skip fields that already contain a sensible value; never re-enter a value that is
  already there, and never pick a field marked already_handled.
- Once every required field has a value, click the submit button.
- Use action "done" only when the page clearly confirms the application was submitted.
- Handle one field per response, in the order a person would fill the form.
Set reusable=true only when the answer would apply to other applications too."""


class ApplyAction(BaseModel):
    action: Literal["fill", "select", "check", "click", "upload", "ask", "wait", "done"]
    field_id: int = -1
    value: str = ""
    question: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reusable: bool = False


def start_apply(
    stamp: str,
    job: dict[str, Any],
    resume_text: str,
    pdf_path: str,
    cfg: AppConfig,
    env: EnvSettings,
    on_finish: Callable[[str], None] | None = None,
) -> ApplySession:
    """Begin one assisted-apply session on a worker thread."""
    label = f"{job.get('company', '')} {job.get('title', '')}".strip()
    sess = session.start(stamp, str(job.get("job_id") or ""), label)

    def target() -> None:
        try:
            run_session(sess, job, resume_text, pdf_path, cfg, env)
        finally:
            if on_finish is not None:
                on_finish(sess.status)

    threading.Thread(target=target, name=f"apply-{sess.id}", daemon=True).start()
    return sess


def run_session(
    sess: ApplySession,
    job: dict[str, Any],
    resume_text: str,
    pdf_path: str,
    cfg: AppConfig,
    env: EnvSettings,
) -> None:
    url = job.get("apply_url") or job.get("listing_url") or ""
    pw = context = None
    try:
        if not url:
            raise RuntimeError("this job has no apply_url or listing_url")
        if not env.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        sess.log(f"Opening {url}")
        pw, context, page = browser.launch(url)
        sess.log("Chrome is open. Log in or dismiss dialogs yourself, then type done.")
        if pdf_path:
            sess.log(f"Resume ready to upload: {pdf_path}")
        else:
            sess.log("No compiled PDF for this job; resume uploads will need your help.")
        sess.ask("Ready to start filling this form? Type done when the form is visible.")

        llm = ChatOpenAI(
            model=cfg.openai.enrich_model,
            api_key=env.openai_api_key or None,
            temperature=0,
        ).with_structured_output(ApplyAction)

        history: list[str] = []
        notes: list[str] = []
        submit_confirmed = False
        errors_in_a_row = 0
        # Session-only, never written to disk: keeps us from re-asking the same
        # question (an OTP in particular) every time the model eyes that field.
        session_answers: dict[str, str] = {}
        attempts: dict[str, int] = {}
        handled: set[str] = set()
        submit_keys: set[str] = set()
        noop_streak = 0
        submitted = False
        outcome = ""
        outcome_text = ""

        for step in range(1, MAX_STEPS + 1):
            if sess.aborted():
                raise Aborted("user aborted")
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                raise RuntimeError(f"{MAX_CONSECUTIVE_ERRORS} browser actions failed in a row")
            if noop_streak >= MAX_NOOP_STREAK:
                noop_streak = 0
                if submitted:
                    reply = sess.ask(
                        "I clicked submit but cannot tell whether it went through. "
                        "Type done if the application was submitted, or tell me what to fix."
                    )
                    if reply.lower() in FINISHED_WORDS:
                        outcome, outcome_text = "applied", "confirmed by you"
                        break
                    notes.append(f"guidance from the candidate: {reply}")
                    continue
                reply = sess.ask(
                    "I am not making progress on this form. Tell me what to do next, "
                    "type apply to submit it, or type abort to stop."
                )
                if reply.lower() in CONFIRM_WORDS:
                    submit_confirmed = True
                    handled -= submit_keys
                    attempts.clear()
                else:
                    notes.append(f"guidance from the candidate: {reply}")
                continue
            fields = browser.snapshot(page)
            if not fields:
                answer = sess.ask(
                    "I cannot see any form fields on this page. "
                    "Navigate to the form and type done, or type abort to stop."
                )
                notes.append(f"user: {answer}")
                continue

            prompt = _build_prompt(job, resume_text, fields, page, history, notes, handled)
            action = llm.invoke(prompt)
            if not isinstance(action, ApplyAction):
                action = ApplyAction.model_validate(action)

            field = _field_by_id(fields, action.field_id)
            label = (field or {}).get("label") or (field or {}).get("name") or ""
            key = _field_key(field, label)

            if field is not None and _is_submit(field) and key:
                submit_keys.add(key)

            if key and key in handled:
                notes.append(f"'{label}' is already handled; move on to another field")
                noop_streak += 1
                continue
            if key:
                attempts[key] = attempts.get(key, 0) + 1
                if attempts[key] > MAX_ATTEMPTS_PER_FIELD:
                    handled.add(key)
                    sess.log(f"Leaving '{label}' alone after {MAX_ATTEMPTS_PER_FIELD} attempts.")
                    notes.append(f"'{label}' was attempted too many times and is now off limits")
                    noop_streak += 1
                    continue

            if action.action == "done":
                sess.log(f"Agent reports the application is complete: {action.reason}")
                outcome, outcome_text = "applied", "application submitted"
                break

            if action.action == "wait":
                sess.log(f"Waiting: {action.reason}")
                page.wait_for_timeout(1500)
                continue

            if action.action == "ask" or _needs_user(action, field, label):
                question = action.question or f"What should I enter for '{label}'?"
                remembered = session_answers.get(key) if key else ""
                if remembered:
                    sess.log(f"Reusing your earlier answer for {label}.")
                    answer = remembered
                else:
                    answer = sess.ask(question)
                    if key:
                        session_answers[key] = answer
                lowered = answer.lower()
                if lowered in CONTINUE_WORDS:
                    notes.append(f"user handled '{label}' manually")
                    history.append(f"user handled {label}")
                    continue
                if lowered in SKIP_WORDS:
                    notes.append(f"leave '{label}' alone, the candidate said to skip it")
                    history.append(f"skipped {label}")
                    if key:
                        handled.add(key)
                    continue
                if field is not None and _accepts_value(field):
                    try:
                        _apply_value(page, field, answer, pdf_path, sess)
                        history.append(f"set #{field['id']} {label} (from you)")
                        errors_in_a_row = 0
                        if key:
                            handled.add(key)
                    except Exception as exc:
                        errors_in_a_row += 1
                        sess.log(f"Could not set {label}: {_short(exc)}")
                        notes.append(f"setting '{label}' failed: {_short(exc)}")
                else:
                    notes.append(f"about '{label or question}': {answer}")
                    noop_streak += 1
                if action.reusable and label and profile.remember(label, answer):
                    sess.log(f"Saved '{label}' for future applications.")
                continue

            if field is None:
                notes.append(f"invalid field id {action.field_id} for {action.action}")
                noop_streak += 1
                continue

            if action.action == "click" and _is_submit(field):
                if submitted:
                    notes.append("submit was already clicked; do not click it again")
                    noop_streak += 1
                    continue
                if not submit_confirmed:
                    reply = sess.ask(
                        f"Ready to click '{_field_label(field)}' and submit this application. "
                        "Type apply to confirm, or tell me what to change first."
                    )
                    if reply.lower() not in CONFIRM_WORDS:
                        notes.append(f"the candidate did not confirm submit and said: {reply}")
                        noop_streak += 1
                        continue
                    submit_confirmed = True

            try:
                _execute(page, action, field, pdf_path, sess)
                history.append(f"{action.action} #{field['id']} {label}")
                errors_in_a_row = 0
                noop_streak = 0
                if action.action == "click" and _is_submit(field):
                    submitted = True
                    if key:
                        handled.add(key)
                    sess.log("Submitted. Waiting for the site to confirm.")
                    page.wait_for_timeout(3000)
            except Exception as exc:
                errors_in_a_row += 1
                sess.log(f"Could not {action.action} {label}: {_short(exc)}")
                notes.append(f"{action.action} on '{label}' failed: {_short(exc)}")
            if len(history) > HISTORY_LIMIT:
                del history[: len(history) - HISTORY_LIMIT]
            page.wait_for_timeout(400)
        else:
            outcome, outcome_text = "failed", f"gave up after {MAX_STEPS} steps"

        # Ask before finishing: once the session emits done the chat pane closes,
        # so a question asked after that could never be answered.
        if outcome == "applied":
            try:
                sess.ask("Type close when you have finished checking the page.")
            except Aborted:
                pass
        sess.finish(outcome, outcome_text)
    except Aborted:
        sess.log("Aborted.")
        sess.finish("aborted", "aborted by user")
    except Exception as exc:
        sess.log(f"Error: {exc}")
        sess.finish("failed", str(exc))
    finally:
        browser.close(pw, context)


def _build_prompt(
    job: dict[str, Any],
    resume_text: str,
    fields: list[dict[str, Any]],
    page,
    history: list[str],
    notes: list[str],
    handled: set[str],
) -> str:
    annotated = []
    for field in fields:
        item = dict(field)
        if _field_key(field, _field_label(field)) in handled:
            item["already_handled"] = True
        annotated.append(item)
    return "\n\n".join(
        [
            SYSTEM,
            f"JOB: {job.get('title', '')} at {job.get('company', '')}",
            f"CANDIDATE PROFILE:\n{profile.as_prompt_text()}",
            f"RESUME:\n{resume_text[:4000]}",
            f"PAGE URL: {_safe_url(page)}",
            f"PAGE TEXT:\n{browser.page_text(page)}",
            f"FORM FIELDS:\n{json.dumps(annotated, ensure_ascii=False)}",
            f"ALREADY DONE:\n{chr(10).join(history) or '(nothing yet)'}",
            f"NOTES FROM THE CANDIDATE:\n{chr(10).join(notes[-10:]) or '(none)'}",
            "Respond with the single next action.",
        ]
    )


def _field_key(field: dict[str, Any] | None, label: str) -> str:
    if field is None:
        return ""
    return profile.fingerprint(label or field.get("name") or "") or f"id-{field.get('id')}"


def _execute(page, action: ApplyAction, field: dict[str, Any], pdf_path: str, sess: ApplySession) -> None:
    locator = browser.locate(page, field["id"])
    label = _field_label(field)
    try:
        locator.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    if action.action == "click":
        locator.click(timeout=15000)
        sess.log(f"Clicked {label}")
        return
    if action.action == "upload":
        if not pdf_path:
            raise RuntimeError("no compiled PDF available to upload")
        locator.set_input_files(pdf_path, timeout=20000)
        sess.log(f"Uploaded resume to {label}")
        return
    _apply_value(page, field, action.value, pdf_path, sess)


def _apply_value(
    page,
    field: dict[str, Any],
    value: str,
    pdf_path: str,
    sess: ApplySession,
) -> None:
    """Set a value the right way for this control: file, select, checkbox or text."""
    locator = browser.locate(page, field["id"])
    label = _field_label(field)
    tag = field.get("tag")
    field_type = (field.get("type") or "").lower()

    if field_type == "file":
        target = value if value and Path(value).is_file() else pdf_path
        if not target:
            raise RuntimeError("no file available to upload")
        locator.set_input_files(target, timeout=20000)
        sess.log(f"Uploaded {Path(target).name} to {label}")
        return

    if tag == "select":
        try:
            locator.select_option(label=value, timeout=10000)
        except Exception:
            locator.select_option(value, timeout=10000)
        sess.log(f"Selected '{value}' for {label}")
        return

    if field_type in ("checkbox", "radio"):
        wants_on = _is_affirmative(value)
        if wants_on:
            locator.check(timeout=10000)
        else:
            locator.uncheck(timeout=10000)
        sess.log(f"{'Checked' if wants_on else 'Unchecked'} {label}")
        return

    locator.fill(value, timeout=10000)
    sess.log(f"Filled {label} = {value}")


def _is_affirmative(value: str) -> bool:
    text = (value or "").strip().lower()
    return any(word in text for word in AFFIRMATIVE_WORDS)


def _short(exc: Exception) -> str:
    return str(exc).splitlines()[0][:200]


def _needs_user(action: ApplyAction, field: dict[str, Any] | None, label: str) -> bool:
    if action.confidence < LOW_CONFIDENCE:
        return True
    if profile.is_secret(label):
        return True
    haystack = f"{label} {(field or {}).get('name', '')}".lower()
    if action.action in ("check", "fill", "select") and any(w in haystack for w in LEGAL_WORDS):
        return not profile.recall(label)
    return False


def _accepts_value(field: dict[str, Any]) -> bool:
    """True for controls you can type into, pick from, tick, or attach a file to."""
    if field.get("tag") not in ("input", "textarea", "select"):
        return False
    return (field.get("type") or "").lower() not in ("submit", "button", "reset", "image")


def _is_submit(field: dict[str, Any]) -> bool:
    text = f"{field.get('text', '')} {field.get('label', '')} {field.get('value', '')}".lower()
    if field.get("type") == "submit":
        return True
    return any(word in text for word in SUBMIT_WORDS)


def _field_by_id(fields: list[dict[str, Any]], field_id: int) -> dict[str, Any] | None:
    for field in fields:
        if field.get("id") == field_id:
            return field
    return None


def _field_label(field: dict[str, Any]) -> str:
    return field.get("label") or field.get("text") or field.get("name") or f"#{field.get('id')}"


def _safe_url(page) -> str:
    try:
        return page.url
    except Exception:
        return ""
