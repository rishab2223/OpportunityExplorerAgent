"""What the candidate has done with held-back jobs, and what that suggests.

A filter in review mode is a guess about how strict the candidate is. Their
clicks are the evidence: keep held-back jobs again and again and the rule is
stricter than they are; skip every one and review mode is spending scoring
calls on jobs they never want. This reads that evidence from what is already
on disk - each run's held_back.json, its decisions and the job history - so
nothing new is stored, and it only ever SUGGESTS. settings.yaml is the
candidate's to change.

Kept:    moved to the shortlist or applied (decision "yes"), marked applied,
         or sent for a referral.
Skipped: skipped.
Neither: still waiting, or closed - a dead posting says nothing about the rule.
"""

from __future__ import annotations

import math
import re
from typing import Any

from src import experience, history
from src.apply import profile
from src.web import run_options
from src.web import runs

# Enough clicks to be a pattern rather than a mood.
MIN_KEPT = 3
KEEP_SHARE = 0.75
MIN_SKIPPED = 5

_REASON_RE = re.compile(r"asks for ([\d.]+)\+ years", re.IGNORECASE)
_KEPT_HISTORY = {"applied", *history.REFERRAL_STATUSES}


def _outcome(decision: str, status: str) -> str:
    if status == "closed":
        return ""
    if decision == "yes" or status in _KEPT_HISTORY:
        return "kept"
    if decision == "no" or status == "skipped":
        return "skipped"
    return ""


def outcomes() -> list[dict[str, Any]]:
    """One entry per held-back job ever shown: its asked years and outcome."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_id, by_fp = history.snapshot()
    for stamp in runs.list_stamps():                # newest first
        path = runs.OUTPUT_DIR / stamp / "held_back.json"
        if not path.exists():
            continue
        decisions = runs.load_decisions(stamp)
        for row in runs._rows(path):
            job_id = row.get("job_id") or ""
            fp = history.fingerprint(row.get("company") or "", row.get("title") or "")
            # One job, one vote: a posting held in three runs is still one
            # decision of the candidate's.
            keys = {k for k in (job_id, fp) if k}
            if keys & seen:
                continue
            seen |= keys
            entry = by_id.get(job_id) or by_fp.get(fp)
            outcome = _outcome((decisions.get(job_id) or {}).get("decision", ""),
                               entry["status"] if entry else "")
            asked = _REASON_RE.search(row.get("held_back") or "")
            found.append({"job_id": job_id, "outcome": outcome,
                          "asked": float(asked.group(1)) if asked else None})
    return found


def summary() -> dict[str, Any]:
    rows = outcomes()
    kept = [r for r in rows if r["outcome"] == "kept"]
    skipped = [r for r in rows if r["outcome"] == "skipped"]
    result: dict[str, Any] = {"kept": len(kept), "skipped": len(skipped), "suggestion": ""}

    rule = run_options.effective_config().experience   # what web runs actually use
    if not rule.enabled or rule.mode != "review":
        return result
    decided = len(kept) + len(skipped)
    if len(kept) >= MIN_KEPT and len(kept) / decided >= KEEP_SHARE:
        years = experience.candidate_years(profile.load_profile().get("total_experience_years"))
        asked = [r["asked"] for r in kept if r["asked"] is not None]
        if years is not None and asked:
            # Enough to have let every kept job through, rounded up to a half year.
            needed = math.ceil((max(asked) - years) * 2) / 2
            if needed > rule.tolerance_years:
                result["suggestion"] = (
                    f"You kept {len(kept)} of the {decided} held-back jobs you decided on. "
                    f"Your rule may be stricter than you are: experience.tolerance_years: "
                    f"{needed:g} in settings.yaml (now {rule.tolerance_years:g}) would have "
                    f"let all of them through.")
    elif len(skipped) >= MIN_SKIPPED and not kept:
        result["suggestion"] = (
            f"You skipped all {len(skipped)} held-back jobs you decided on. "
            "experience.mode: drop in settings.yaml would stop scoring them at all.")
    return result
