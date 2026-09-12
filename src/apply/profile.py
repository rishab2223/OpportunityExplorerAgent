from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.config import ROOT

PROFILE_PATH = ROOT / "localData" / "apply_profile.json"

# Anything matching these never gets stored anywhere, however the LLM labels it.
SECRET_HINTS = (
    "otp",
    "one time",
    "one-time",
    "verification code",
    "passcode",
    "password",
    "pin",
    "cvv",
    "captcha",
    "security code",
)

# Answered questions live in the answer bank (src/answers.py, SQLite), not
# here; this file is the curated, hand-edited part of the candidate's data.
TEMPLATE: dict[str, Any] = {
    "full_name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "current_company": "",
    "current_title": "",
    "total_experience_years": "",
    "notice_period": "",
    "current_ctc": "",
    "expected_ctc": "",
    "work_authorization": "",
    "willing_to_relocate": "",
    "preferred_location": "",
    "city": "",
    "postal_code": "",
    "date_of_birth": "",
    # Eligibility and demographic questions (sponsorship, citizenship,
    # gender, disability, veteran status) are deliberately NOT here:
    # they are legal declarations, asked once and stored only in the
    # answer bank after the candidate confirms them.
    "willing_to_travel": "",
    "earliest_start_date": "",
    "how_did_you_hear": "",
    "relevant_experience_years": "",
    # Split out of the one "education" line, so a degree list and a 345-entry
    # "Field of study" dropdown are answered from the profile rather than
    # guessed at by the model.
    "university": "",
    "highest_education_level": "",
    "field_of_study": "",
    "graduation_year": "",
    # Employment history, most recent first. Filled by the script, never by
    # the model: it does not change between applications. Each row takes
    # title, company, location, start and end as "MM/YYYY", current, and a
    # description. The candidate's own project work does NOT belong here.
    "jobs": [],
}

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return dict(TEMPLATE)
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(TEMPLATE)
    if not isinstance(data, dict):
        return dict(TEMPLATE)
    return data


def ensure_profile_file() -> Path:
    if not PROFILE_PATH.exists():
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
    return PROFILE_PATH


def fingerprint(label: str) -> str:
    text = _NON_ALNUM.sub(" ", (label or "").lower())
    return _SPACES.sub(" ", text).strip()


def is_secret(label: str) -> bool:
    text = (label or "").lower()
    return any(hint in text for hint in SECRET_HINTS)


def _job_lines(jobs: list[Any]) -> list[str]:
    """One readable line per job, and never the descriptions: a raw dict repr
    of every role would ride along in every single model call."""
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        where = str(job.get("location") or "").strip()
        start = str(job.get("start") or "").strip()
        end = str(job.get("end") or "").strip() or "present"
        when = f"{start} - {end}" if start else ""
        out.append(
            f"job: {job.get('title', '')} at {job.get('company', '')}"
            + (f", {where}" if where else "")
            + (f" ({when})" if when else "")
            + " [the script fills this entry; do not enter it yourself]")
    return out


def as_prompt_text(profile: dict[str, Any] | None = None) -> str:
    data = profile if profile is not None else load_profile()
    lines = []
    for key, value in data.items():
        if key == "learned" or not value:
            continue
        if key == "jobs" and isinstance(value, list):
            lines.extend(_job_lines(value))
            continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines) or "(profile is empty)"
