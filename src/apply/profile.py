from __future__ import annotations

import json
import os
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

# Government identifiers. These are never stored - the profile is plaintext on
# disk - and never guessed, so the only place an answer can come from is the
# candidate. On a REQUIRED field that is worth stopping to ask; on an optional
# one it is not, and a Worldline application parked in the chat waiting on an
# optional "Permanent account number" that nobody had to answer.
# Each hint is a whole phrase on purpose: a bare "pan" is inside "Company
# Name", and an optional field silently skipped is worse than one asked about.
IDENTIFIER_HINTS = (
    "pan number",
    "pan card",
    "permanent account number",
    "aadhaar",
    "aadhar",
    "passport",
    "social security",
    "ssn",
    "national insurance",
    "national id",
    "driving licence",
    "driver's license",
    "drivers license",
    "tax id",
    "uan number",
)

# Answered questions live in the answer bank (src/answers.py, SQLite), not
# here; this file is the curated, hand-edited part of the candidate's data.
# Every key a rule can read is listed, empty, so the file the candidate opens
# shows the whole surface rather than hiding half of it in the README. An
# empty value costs nothing: as_prompt_text() skips it, and no rule fires.
TEMPLATE: dict[str, Any] = {
    "full_name": "",
    "email": "",
    "phone": "",
    "phone_country_code": "",
    "location": "",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "current_company": "",
    "current_company_location": "",
    "current_title": "",
    "total_experience_years": "",
    "notice_period": "",
    "current_ctc": "",
    "expected_ctc": "",
    # The dropdowns that sit beside a salary amount, e.g. "INR" and "Annual".
    "salary_currency": "",
    "salary_period": "",
    "willing_to_relocate": "",
    "preferred_location": "",
    "address_line1": "",
    "city": "",
    "state": "",
    "postal_code": "",
    "date_of_birth": "",
    # Eligibility and demographic questions (work authorisation, sponsorship,
    # citizenship, gender, disability, veteran status) are deliberately NOT
    # here: they are legal declarations, asked once and stored only in the
    # answer bank after the candidate confirms them. "work_authorization" was
    # a key here until its rule was removed; offering a box for it invited an
    # answer that no rule could ever use and that every prompt would carry.
    "willing_to_travel": "",
    # Only ever what the candidate wrote here; nothing infers it.
    "pronouns": "",
    # Indian forms ask it ("Father's name"); the family rows of a form are
    # never filled from the profile otherwise.
    "father_name": "",
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
    # The degree's first and last day, DD/MM/YYYY, for the forms whose
    # education boxes are full date pickers (EPAM's "Education Years").
    "education_start_date": "",
    "education_end_date": "",
    # Indian forms ask both, and neither changes between applications:
    # the accrediting body ("UGC", "AICTE") and where the employer's own
    # tier list puts the college ("Tier 1", "Tier 2", "Other/Not Listed").
    "degree_recognized_by": "",
    "college_tier": "",
    # Both scales, because a form asks for one or the other and converting on
    # the spot is how a 7.4 becomes a 7.4 out of 5. Only ever filled where the
    # form makes it mandatory - see _ONLY_WHEN_REQUIRED in resolver.py.
    "gpa_10_point": "",
    "gpa_5_point": "",
    # Which scale was actually studied on ("10" or "5"). A form asking
    # about the other one offers "I attended a university using a
    # 10-point scale", and that is the true answer rather than a
    # converted figure.
    "gpa_scale": "",
    # One line per school, separated by ";" or a newline, for the forms that
    # want the whole thing in one box:
    #   "Example University - Bachelors, Computer Science, 2015-2019"
    "education": "",
    # Strongest first: a form capped at ten skills takes the first ten.
    "skills": "",
    # "English - Intermediate; Hindi - Fluent"
    "languages": "",
    # Employment history, most recent first. Filled by the script, never by
    # the model: it does not change between applications. Each row takes
    # title, company, location, start and end as "MM/YYYY", current, and a
    # description. The candidate's own project work does NOT belong here.
    "jobs": [],
    # Resume entries that are the candidate's own projects rather than jobs.
    # Never entered as work experience, and removed again when a site creates
    # one from the uploaded resume.
    "not_employment": "",
}

# How the Profile page lays the keys out: (section, note, fields), and per
# field (key, label, kind, hint). kind is text | number | email | url | long
# | choice:<a>|<b> | jobs. A "choice" is a suggestion list, not a lock: a
# value already in the file that is not on the list is kept as it is.
# A key in the file but in no section (an old one, say) still appears, under
# "Other", and is never dropped on save.
FORMS_AND_FILTER = "Application forms and the experience filter"
SECTIONS: list[dict[str, Any]] = [
    {"title": "Contact", "note": "Typed into the matching boxes on every application form.",
     "fields": [
         ("full_name", "Full name", "text", ""),
         ("email", "Email", "email", ""),
         ("phone", "Phone", "text", "Number only; the country code goes in its own box"),
         ("phone_country_code", "Phone country code", "text", "e.g. +91"),
         ("location", "Location", "text", "City, Country"),
         ("address_line1", "Address line 1", "text", ""),
         ("city", "City", "text", ""),
         ("state", "State", "text", ""),
         ("postal_code", "Postal / PIN code", "text", ""),
         ("date_of_birth", "Date of birth", "text", "DD/MM/YYYY"),
         ("pronouns", "Pronouns", "text", "Optional. Used only for a Pronouns box, e.g. He/Him"),
         ("father_name", "Father's name", "text", "For a \"Father's name\" box"),
     ]},
    {"title": "Experience", "note": "",
     "fields": [
         ("current_title", "Current or last title", "text", ""),
         ("current_company", "Current or last company", "text", ""),
         ("current_company_location", "Company location", "text", ""),
         ("total_experience_years", "Total experience (years)", "number",
          FORMS_AND_FILTER + ". e.g. 6 or 6.5"),
         ("relevant_experience_years", "Relevant experience (years)", "number", ""),
         ("notice_period", "Notice period", "text", "e.g. Immediate, 30 days"),
         ("earliest_start_date", "Earliest start date", "text", ""),
     ]},
    {"title": "Compensation", "note": "",
     "fields": [
         ("current_ctc", "Current CTC", "text", "e.g. 25 LPA"),
         ("expected_ctc", "Expected CTC", "text", ""),
         ("salary_currency", "Salary currency", "text", "The dropdown beside an amount, e.g. INR"),
         ("salary_period", "Salary period", "choice:Annual|Monthly", ""),
     ]},
    {"title": "Work preferences", "note": "",
     "fields": [
         ("willing_to_relocate", "Willing to relocate", "choice:Yes|No", ""),
         ("preferred_location", "Preferred location", "text", ""),
         ("willing_to_travel", "Willing to travel", "choice:Yes|No", ""),
         ("how_did_you_hear", "How did you hear about us", "text", "Default answer, e.g. LinkedIn"),
     ]},
    {"title": "Education", "note": "",
     "fields": [
         ("highest_education_level", "Highest education level", "text", "e.g. Bachelor's"),
         ("field_of_study", "Field of study", "text", ""),
         ("university", "University", "text", ""),
         ("graduation_year", "Graduation year", "number", ""),
         ("education_start_date", "Education start date", "text", "DD/MM/YYYY, for full-date boxes"),
         ("education_end_date", "Education end date", "text", "DD/MM/YYYY"),
         ("degree_recognized_by", "Degree recognised by", "text", "e.g. UGC, AICTE"),
         ("college_tier", "College tier", "text", "e.g. Tier 1"),
         ("gpa_scale", "GPA scale studied on", "choice:10|5", ""),
         ("gpa_10_point", "GPA (10-point)", "number", "Only filled where a form requires it"),
         ("gpa_5_point", "GPA (5-point)", "number", "Only filled where a form requires it"),
         ("education", "Education, one line per school", "long",
          "For forms that want it in one box: University - Degree, Field, 2015-2019"),
     ]},
    {"title": "Work history", "note": "Most recent first. Filled into a form's work-experience "
     "entries by the agent, never by the model. Your own projects do not belong here.",
     "fields": [("jobs", "Jobs", "jobs", "")]},
    {"title": "Skills and links", "note": "",
     "fields": [
         ("skills", "Skills", "long", "Strongest first: a form capped at ten takes the first ten"),
         ("languages", "Languages", "text", "e.g. English - Fluent; Hindi - Native"),
         ("linkedin", "LinkedIn URL", "url", ""),
         ("github", "GitHub URL", "url", ""),
         ("portfolio", "Portfolio URL", "url", ""),
         ("not_employment", "Resume entries that are not jobs", "long",
          "Your own projects. Never entered as work experience"),
     ]},
]
JOB_FIELDS = ("title", "company", "location", "start", "end", "current", "description")
NEVER_STORED = ("Government IDs (PAN, Aadhaar, passport, SSN, driving licence)",
                "Passwords, OTPs and security codes",
                "Work authorisation, sponsorship and self-identification answers: "
                "asked once, then kept under Saved answers")

_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?\+?$")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MONTH_YEAR_RE = re.compile(r"^(0?[1-9]|1[0-2])/\d{4}$")

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


_cache: tuple[str, tuple[int, int], dict[str, Any]] | None = None


def load_profile() -> dict[str, Any]:
    """The profile, re-read only when the file changes. resolve() asked for it
    up to three times per field, and a sweep covers every field of every
    snapshot, so one 250-field page was several hundred reads and parses of
    the same JSON. The stat is the cost now; a copy is returned so a caller
    that edits the dict cannot change what the next caller sees.

    Keyed on size as well as the nanosecond mtime: two writes inside one
    clock tick share an mtime, and on a busy machine mykaarma_check's
    write-then-restore came back with the first write's value."""
    global _cache
    try:
        info = PROFILE_PATH.stat()
        stamp = (info.st_mtime_ns, info.st_size)
    except OSError:
        return dict(TEMPLATE)
    key = str(PROFILE_PATH)
    if _cache is not None and _cache[0] == key and _cache[1] == stamp:
        return dict(_cache[2])
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(TEMPLATE)
    if not isinstance(data, dict):
        return dict(TEMPLATE)
    _cache = (key, stamp, data)
    return dict(data)


def _kinds() -> dict[str, str]:
    return {key: kind for section in SECTIONS for key, _label, kind, _hint in section["fields"]}


def _check_job(index: int, job: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(job, dict):
        return {}, [f"job {index + 1} is not an entry"]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for key in JOB_FIELDS:
        value = job.get(key, False if key == "current" else "")
        if key == "current":
            clean[key] = bool(value)
            continue
        clean[key] = str(value or "").strip()
    for key in ("start", "end"):
        if clean[key] and not _MONTH_YEAR_RE.match(clean[key]):
            errors.append(f"job {index + 1}: {key} must be MM/YYYY")
    if clean["current"]:
        clean["end"] = ""                 # a current job has no end
    if not (clean["title"] or clean["company"]):
        errors.append(f"job {index + 1}: needs a title or a company")
    # Keys the page does not edit are carried over, never dropped.
    clean.update({k: v for k, v in job.items() if k not in JOB_FIELDS})
    return clean, errors


def validate(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The values cleaned for saving, and an error per key that cannot be."""
    kinds = _kinds()
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in values.items():
        kind = kinds.get(key, "text")
        if kind == "jobs":
            if not isinstance(value, list):
                errors[key] = "must be a list of jobs"
                continue
            checked = [_check_job(i, job) for i, job in enumerate(value)]
            problems = [p for _job, found in checked for p in found]
            if problems:
                errors[key] = "; ".join(problems)
                continue
            clean[key] = [job for job, _found in checked]
            continue
        if isinstance(value, (dict, list)):
            errors[key] = "must be text"
            continue
        text = "" if value is None else str(value).strip()
        if text and kind == "number" and not _NUMBER_RE.match(text):
            errors[key] = "must be a number, e.g. 6 or 6.5"
        elif text and kind == "email" and not _EMAIL_RE.match(text):
            errors[key] = "must be an email address"
        elif text and kind == "url" and not _URL_RE.match(text):
            errors[key] = "must start with http:// or https://"
        else:
            clean[key] = text
    return clean, errors


def page_data() -> dict[str, Any]:
    """Everything the Profile page shows: the layout, and the values with
    every template key present (empty when the file lacks it)."""
    data = load_profile() if PROFILE_PATH.exists() else {}
    values = {**TEMPLATE, **data}
    placed = {key for section in SECTIONS for key, *_ in section["fields"]}
    other = [key for key in values if key not in placed and key != "learned"]
    sections = [
        {"title": s["title"], "note": s["note"],
         "fields": [{"key": k, "label": l, "kind": kind, "hint": h} for k, l, kind, h in s["fields"]]}
        for s in SECTIONS
    ]
    if other:
        sections.append({
            "title": "Other", "note": "Keys in your profile file that no section above covers.",
            "fields": [{"key": k, "label": k.replace("_", " ").capitalize(), "kind": "text", "hint": ""}
                       for k in other]})
    return {"sections": sections, "values": values, "never_stored": list(NEVER_STORED),
            "exists": PROFILE_PATH.exists()}


def save_profile(updates: dict[str, Any]) -> dict[str, str]:
    """Merge `updates` into the profile file. Returns errors by key; on any
    error nothing is written. Keys the page does not know are kept, and only
    known keys or keys already in the file can be set."""
    global _cache
    current: dict[str, Any] = {}
    if PROFILE_PATH.exists():
        # Read raw, not through load_profile: that hands back the template
        # for a file that does not parse, and saving over it would replace
        # everything the candidate had in there with blanks.
        try:
            loaded = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"_file": f"the profile file could not be read ({exc}); fix it by hand first"}
        current = loaded if isinstance(loaded, dict) else {}
    allowed = set(TEMPLATE) | set(current)
    unknown = [key for key in updates if key not in allowed]
    clean, errors = validate({k: v for k, v in updates.items() if k in allowed})
    for key in unknown:
        errors[key] = "not a profile field"
    if errors:
        return errors
    merged = {**TEMPLATE, **current, **clean}
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the file, then swapped in: an apply session reading it
    # mid-write would otherwise see half a JSON document.
    temp = PROFILE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, PROFILE_PATH)
    _cache = None
    return {}


def ensure_profile_file() -> Path:
    if not PROFILE_PATH.exists():
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
    return PROFILE_PATH


def fingerprint(label: str) -> str:
    text = _NON_ALNUM.sub(" ", (label or "").lower())
    return _SPACES.sub(" ", text).strip()


_SECRET_RE = re.compile(r"\b(" + "|".join(re.escape(h) for h in SECRET_HINTS) + r")\b")
# A postal PIN is an address, not a secret: "Pincode", "PIN Code", "Zip / PIN".
# Stripped before the secret hints are matched so that "pin" only counts on
# its own ("Security PIN", "4-digit PIN").
_POSTAL_PIN_RE = re.compile(
    r"\bpin\s*-?\s*code\b|\bpincode\b"
    r"|\b(?:zip|postal|post)\s*/\s*pin\b|\bpin\s*/\s*(?:zip|postal|post)\b"
)


def is_secret(label: str) -> bool:
    """Substring matching here made "Shipping address" and "Your opinion" a
    secret (both contain "pin"), and refused every Indian "Pincode" box, so
    the postal_code rule could never reach them. Whole words only."""
    text = _POSTAL_PIN_RE.sub(" ", (label or "").lower())
    return _SECRET_RE.search(text) is not None


def is_identifier(label: str) -> bool:
    """A box for a government identifier's NUMBER. Not a photo whose size is
    a passport's - "Upload Your Recent passport size photo" (DentCare, Oct
    2026) was refused as one - nor a yes/no about holding the document."""
    text = (label or "").lower()
    if not any(hint in text for hint in IDENTIFIER_HINTS):
        return False
    if re.search(r"\b(photo|photograph|picture|image|pic)s?\b", text):
        return False
    if re.search(r"\b(do|does|have|has|hold|holds|possess)\b.*\?\s*\*?\s*$", text) \
            and not re.search(r"\b(number|no\.?|#|id)\b", text):
        return False
    return True


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
        # "learned" is a legacy key: saved answers live in the answer bank now,
        # and an old profile still holding one must not bloat every prompt.
        if key == "learned" or not value:
            continue
        if key == "jobs" and isinstance(value, list):
            lines.extend(_job_lines(value))
            continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines) or "(profile is empty)"
