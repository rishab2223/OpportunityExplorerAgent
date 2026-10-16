"""Which button moves the form on, and which one sends the application.

Words alone cannot tell: "Continue" is a step on most forms and the final
send on some; "Apply" opens a form on a job page and sends it at the end of
one. So at the end of each step one model call looks at the buttons together
with the page around them - a step counter, the headings, how much of the
form is filled - and names each one: next, final, opens or other.

The model can only make a click SAFER, never riskier. A button whose words
plainly send the application (Submit, Send, Complete, Finish) is final
whatever the model says, and the candidate clicks it. The model decides only
the unclear words; when it is unsure, or the call fails, the code's own rules
stand as they always did.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

# Words that send an application. Never clicked by the agent, whatever the
# model thinks of the page: the final click is always the candidate's.
FINAL_WORDS = ("submit", "send", "complete", "finish", "apply now", "send application",
               "submit application")
_FINAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in FINAL_WORDS) + r")\b", re.IGNORECASE)
# Words that are a step on one form and the end of another: the model is
# asked about these before the agent clicks one.
UNCLEAR_WORDS = ("apply", "continue", "next", "confirm", "done", "save", "proceed", "review",
                 "i agree", "accept", "go")
_UNCLEAR_RE = re.compile(r"^\s*(?:" + "|".join(re.escape(w) for w in UNCLEAR_WORDS) + r")\b",
                         re.IGNORECASE)
# How sure "next" must be before a submit-type button is clicked as a step.
# On saved pages real steps came back 0.85-0.97 and a single-page form's
# final Apply 0.6 (Sonnet, low effort); Haiku put that Apply at 0.95, so it
# is not the model for this (Oct 2026).
NEXT_CONFIDENCE = 0.85
# Buttons in one call: the ones that matter first, then the form's others.
# Fifteen of anything let a careers site's header fill the list before the
# form's own Submit (eval on saved pages, Oct 2026) - and every extra one is
# more for the model to write about while you wait.
MAX_BUTTONS = 10
# Site buttons outside the form, as context only.
MAX_OUTSIDE = 2

Kind = Literal["next", "final", "opens", "other"]


class ButtonVerdict(BaseModel):
    id: int
    kind: Kind
    # Required: left optional, Workday's answers came back without it and a
    # plain "Save and Continue" read as 0.0 sure (eval on saved pages, Oct 2026).
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class ButtonKinds(BaseModel):
    buttons: list[ButtonVerdict] = Field(default_factory=list)
    last_page: bool = False
    why: str = ""


SYSTEM = (
    "You label the buttons on one page of a job application, so an assistant filling the "
    "form knows which button moves to the next step and which one sends the application. "
    "The candidate always sends the application themselves; your labels decide which "
    "buttons the assistant may click.\n"
    "Kinds:\n"
    "- next: moves to another step of the same application WITHOUT sending it: Next, "
    "Continue, Save and continue, Proceed, Next page, Review (when a review page follows).\n"
    "- final: sends the whole application to the employer - after it there is nothing left "
    "to do: Submit, Send application, Complete, Finish, Confirm and submit, or Apply / "
    "Continue / Done on the LAST page of a filled form.\n"
    "- opens: opens the application form from a job description page that is not itself "
    "a form: Apply, Apply now, I'm interested, Apply for this job.\n"
    "- other: everything else: Add, Remove, Upload, Back, Previous, Save draft, Cancel, "
    "Sign in, yes/no choice buttons, links to other pages.\n"
    "Judge each button from its words AND the page: a step counter at its last step, a "
    "'Review your application' or summary page, a declaration or e-signature page at the "
    "end, a progress bar at its end, or no further steps listed all mean a Continue or "
    "Apply there is probably final. A form page with fields still to go is not the last "
    "page. When unsure between next and final, answer final with a low confidence: a wrong "
    "'next' can send an unfinished application, a wrong 'final' only means the candidate "
    "clicks it. Give every button listed a label, a confidence from 0 to 1, and a reason of "
    "at most ten words; say whether this is the last page of the application."
)


# The page around the buttons: what a person reading it would use to tell a
# step from the end. Runs in the form's frame.
PAGE_CONTEXT_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const words = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const headings = [];
  for (const h of document.querySelectorAll('h1, h2, h3, legend, [role=heading]')) {
    if (!vis(h)) continue;
    const t = words(h).slice(0, 90);
    if (t && !headings.includes(t)) headings.push(t);
    if (headings.length >= 8) break;
  }
  const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ');
  const counter = text.match(/\bstep\s*\d+\s*(?:of|\/)\s*\d+\b|\b\d+\s*(?:of|\/)\s*\d+\s*steps?\b|\bpage\s*\d+\s*of\s*\d+\b/i);
  const steps = [];
  for (const s of document.querySelectorAll('[aria-current=step], [aria-current=page] , [class*=step][class*=active], [class*=Step][class*=active], [class*=current][class*=step]')) {
    if (!vis(s)) continue;
    const t = words(s).slice(0, 60);
    if (t && !steps.includes(t)) steps.push(t);
    if (steps.length >= 3) break;
  }
  const bars = [];
  for (const b of document.querySelectorAll('[role=progressbar]')) {
    const now = b.getAttribute('aria-valuenow'), max = b.getAttribute('aria-valuemax');
    if (now && max) bars.push(now + ' of ' + max);
  }
  const ends = /\b(review (your|the) application|application summary|review and submit|declaration|e-?signature|electronic signature|certify that|attest)\b/i;
  return {
    title: (document.title || '').slice(0, 90),
    headings,
    counter: counter ? counter[0] : '',
    current_step: steps,
    progress: bars,
    says_review: ends.test(text.slice(0, 20000)),
  };
}
"""


def words(field: dict[str, Any]) -> str:
    """What the button says: its text, else its label or value."""
    for key in ("text", "label", "value"):
        text = str(field.get(key) or "").strip()
        if text:
            return re.sub(r"\s+", " ", text)[:80]
    return ""


# Step buttons whose words leave no doubt: none of them ever sends an
# application. One of these (not submit-type) needs no model call - four calls
# on LinkedIn's Next / Next / Next / Review cost 13 s and changed nothing.
# "Continue" is not here: it is the end of some forms.
_CLEAR_STEP_RE = re.compile(
    r"^\s*(?:next|next step|next page|continue to next step|review|review your application|"
    r"save and continue|save & continue|save and next)\s*[>›→]?\s*$", re.IGNORECASE)


def clearly_a_step(field: dict[str, Any]) -> bool:
    """A Next / Review that no form uses to send an application."""
    return _CLEAR_STEP_RE.match(words(field)) is not None


def plainly_final(field: dict[str, Any]) -> bool:
    """Words that send the application. The model cannot overrule these."""
    return _FINAL_RE.search(words(field)) is not None


def unclear(field: dict[str, Any]) -> bool:
    """Words that are a step on one form and the end on another."""
    return _UNCLEAR_RE.match(words(field)) is not None


def is_button(field: dict[str, Any]) -> bool:
    tag = field.get("tag")
    kind = str(field.get("type") or "").lower()
    return (tag == "button" or field.get("role") == "button"
            or (tag == "input" and kind in ("submit", "button", "image")))


def matters(field: dict[str, Any]) -> bool:
    """A button whose label can change what the agent does: one that sends
    its form, or reads like a step, an end, or an opener."""
    return (bool(field.get("submits")) or str(field.get("type") or "").lower() == "submit"
            or unclear(field) or plainly_final(field))


def candidates(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The buttons worth labelling, in page order: every one that matters,
    then the form's other buttons, then a couple from outside it for
    context, up to MAX_BUTTONS. A site footer's controls are never the
    form's, and a link counts only when it reads like one of the kinds."""
    # A footer's buttons are left out unless they matter: a modal draws its
    # Next in a <footer> of its own (LinkedIn's Easy Apply), and leaving those
    # out labelled Dismiss and an uploaded PDF while the Next itself was never
    # seen (Axionix, Oct 2026).
    found = [f for f in fields
             if words(f) and (not f.get("footer") or matters(f))
             and (is_button(f) or (f.get("tag") == "a" and (unclear(f) or plainly_final(f))))]
    key = [f for f in found if matters(f)]
    inside = [f for f in found if not matters(f) and f.get("form", -1) != -1]
    outside = [f for f in found if not matters(f) and f.get("form", -1) == -1][:MAX_OUTSIDE]
    picked = {id(f) for f in (key + inside + outside)[:MAX_BUTTONS]}
    return [f for f in found if id(f) in picked]


def prompt(fields: list[dict[str, Any]], buttons: list[dict[str, Any]], context: dict[str, Any],
           steps_done: int, url: str) -> str:
    fillable = [f for f in fields if f.get("tag") in ("input", "select", "textarea")
                and str(f.get("type") or "").lower() not in ("submit", "button", "image", "hidden")]
    filled = [f for f in fillable if str(f.get("value") or "").strip() or f.get("checked")]
    lines = [
        f"PAGE: {url}",
        f"TITLE: {context.get('title') or '-'}",
        f"HEADINGS: {' | '.join(context.get('headings') or []) or '-'}",
        f"STEP COUNTER: {context.get('counter') or 'none shown'}",
        f"CURRENT STEP MARKED: {', '.join(context.get('current_step') or []) or 'none'}",
        f"PROGRESS BAR: {', '.join(context.get('progress') or []) or 'none'}",
        f"PAGE MENTIONS A REVIEW / DECLARATION / SIGNATURE: {'yes' if context.get('says_review') else 'no'}",
        f"FIELDS ON THIS PAGE: {len(fillable)} ({len(filled)} filled)",
        f"STEPS ALREADY MOVED THROUGH IN THIS APPLICATION: {steps_done}",
        "",
        "BUTTONS:",
    ]
    for field in buttons:
        bits = [f"id {field['id']}", f"'{words(field)}'", str(field.get("tag") or "")]
        if field.get("type"):
            bits.append(f"type={field['type']}")
        if field.get("submits"):
            bits.append("sends its form when clicked")
        if field.get("form", -1) == -1:
            bits.append("outside any form")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines)


def read_context(doc) -> dict[str, Any]:
    try:
        return doc.evaluate(PAGE_CONTEXT_JS) or {}
    except Exception:
        return {}


def kind_of(field: dict[str, Any], verdicts: dict[int, ButtonVerdict]) -> str:
    """The agent's decision for one button: 'final', 'next', 'opens',
    'other', or '' when nothing confident is known (the code's own rules
    then decide, as before). Plainly final words are final, always."""
    if plainly_final(field):
        return "final"
    verdict = verdicts.get(field.get("id"))
    if verdict is None:
        return ""
    if verdict.kind == "final":
        return "final"           # safer: believed at any confidence
    if verdict.kind == "next":
        return "next" if verdict.confidence >= NEXT_CONFIDENCE else ""
    return verdict.kind


def classify(invoke, fields: list[dict[str, Any]], context: dict[str, Any], steps_done: int,
             url: str) -> ButtonKinds | None:
    """One call for the page's buttons, or None when there are none."""
    buttons = candidates(fields)
    if not buttons:
        return None
    result = invoke(SYSTEM, prompt(fields, buttons, context, steps_done, url), ButtonKinds)
    if not isinstance(result, ButtonKinds):
        result = ButtonKinds.model_validate(result)
    known = {f["id"] for f in buttons}
    result.buttons = [b for b in result.buttons if b.id in known]
    if result.last_page or context.get("says_review"):
        # The end of the form, by the model's reading or the page's own words:
        # nothing on it is clicked as a step, however sure the "next".
        for verdict in result.buttons:
            if verdict.kind == "next":
                verdict.confidence = min(verdict.confidence, NEXT_CONFIDENCE - 0.01)
    return result
