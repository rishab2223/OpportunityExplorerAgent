"""Salary answers in the unit the form asks for, plus a market estimate for
"expected salary" questions.

Forms disagree on units: "Current CTC (in LPA)" wants 25, "Annual salary
(INR)" wants 2500000, "Monthly salary" wants 208333. The answer bank stores
one canonical form ("25 LPA"); parse_annual_inr() reads any of the common
spellings back into rupees per year and for_field() writes it out the way
the field asks. A field that names no unit gets the stored text verbatim.

Expected salary is estimated by the model (ESTIMATE_SYSTEM) as a range in
LPA for THIS role at THIS company; the midpoint is used, never below the
candidate's current pay.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from src import answers

LAKH = 100_000
CRORE = 10_000_000

_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)?\s*(\d+(?:[.,]\d+)*)\s*"
    r"(lpa|lakhs?|lacs?|l\b|crores?|cr\b|thousand|k\b)?",
    re.IGNORECASE,
)
_MONTHLY_RE = re.compile(r"\b(per month|monthly|a month|p\.?m\.?)\b", re.IGNORECASE)
# Field text that fixes the unit. "in LPA", "(lakhs)", "in lacs per annum".
_LPA_FIELD_RE = re.compile(r"\blpa\b|\blakh|\blacs?\b|\blac\b", re.IGNORECASE)
_MONTHLY_FIELD_RE = re.compile(r"\b(per month|monthly|a month|p\.?m\.?)\b", re.IGNORECASE)
_ANNUAL_FIELD_RE = re.compile(
    r"\b(per annum|annual|annually|yearly|per year|p\.?a\.?|inr|rupees|rs\.?)\b|₹",
    re.IGNORECASE,
)


_SALARY_FIELD_RE = re.compile(
    r"\b(ctc|salary|compensation|remuneration|pay package|pay expectation)\b", re.IGNORECASE
)


def topic_of(field: dict[str, Any]) -> str:
    """'current_ctc', 'expected_ctc' or '' for this field's question."""
    key = answers.question_key(field.get("label") or "", field.get("group") or "")
    return key if key in ("current_ctc", "expected_ctc") else ""


def is_salary_field(field: dict[str, Any]) -> bool:
    """Any pay amount box - "Annual salary (INR)" counts even without a
    current/expected qualifier; unit conversion is right for either."""
    if topic_of(field):
        return True
    return bool(_SALARY_FIELD_RE.search(f"{field.get('label') or ''} {field.get('group') or ''}"))


def parse_annual_inr(text: str, unit_hint: str = "") -> int | None:
    """Rupees per year from '25 LPA', '25 lakhs', '2.5 cr', '2500000',
    '25,00,000', '80k per month' - or None when there is no number.

    A bare number is read by unit_hint ('lpa', 'monthly', 'annual') when the
    field named one; otherwise anything under 500 is taken as lakhs (no annual
    pay is under 500 rupees) and the rest as rupees.
    """
    match = _AMOUNT_RE.search(text or "")
    if match is None:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    monthly = bool(_MONTHLY_RE.search(text or ""))
    if unit in ("lpa",) or unit.startswith(("lakh", "lac")) or unit == "l":
        amount = number * LAKH
        monthly = False if unit == "lpa" else monthly
    elif unit.startswith("cr"):
        amount = number * CRORE
    elif unit in ("k", "thousand"):
        amount = number * 1000
    elif number < 500 or (unit_hint == "lpa" and number < 10_000):
        # "25" is lakhs; so is "2500" in an LPA box - but "2500000" typed
        # into that same box is rupees, whatever the label says.
        amount = number * LAKH
    elif unit_hint == "monthly":
        amount, monthly = number, True
    else:
        amount = number
    if monthly:
        amount *= 12
    return int(round(amount))


def unit_of(field: dict[str, Any]) -> str:
    """The unit the field asks for: 'lpa', 'monthly', 'annual' or ''."""
    text = " ".join(
        str(field.get(k) or "") for k in ("label", "group", "name", "placeholder", "text")
    )
    if _LPA_FIELD_RE.search(text):
        return "lpa"
    if _MONTHLY_FIELD_RE.search(text):
        return "monthly"
    if _ANNUAL_FIELD_RE.search(text):
        return "annual"
    return ""


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def canonical(annual_inr: int) -> str:
    """The bank's form: '25 LPA', '25.5 LPA'."""
    return f"{_trim(annual_inr / LAKH)} LPA"


def format_for(annual_inr: int, field: dict[str, Any], fallback: str) -> str:
    """The amount written the way this field asks; the fallback text when the
    field names no unit and is not numeric."""
    unit = unit_of(field)
    numeric = (field.get("type") or "").lower() == "number"
    if unit == "lpa":
        return _trim(annual_inr / LAKH)
    if unit == "monthly":
        return str(int(round(annual_inr / 12)))
    if unit == "annual" or numeric:
        return str(annual_inr)
    return fallback


def for_field(value: str, field: dict[str, Any]) -> str:
    """A salary answer (from the bank, the profile, the model or the user)
    converted to this field's unit. Non-salary fields and unparsable text
    pass through untouched, so this is safe at the one place values are
    written."""
    if not is_salary_field(field):
        return value
    if len(re.findall(r"\d+(?:[.,]\d+)*", value or "")) != 1:
        return value  # a range ("25-30 LPA") or prose: the writer's words stand
    annual = parse_annual_inr(value, unit_of(field))
    if annual is None:
        return value
    return format_for(annual, field, value)


def normalize(value: str, field: dict[str, Any]) -> str:
    """What to STORE for a salary answer: '25' typed into an '(in LPA)' field
    becomes '25 LPA', so a plain 'Current salary' field elsewhere reads it
    right. Non-salary or unparsable answers are stored as typed."""
    if not is_salary_field(field):
        return value
    annual = parse_annual_inr(value, unit_of(field))
    return canonical(annual) if annual else value


# -- market estimate -------------------------------------------------------

class SalaryEstimate(BaseModel):
    low_lpa: float = Field(default=0.0, ge=0.0)
    high_lpa: float = Field(default=0.0, ge=0.0)
    basis: str = ""


ESTIMATE_SYSTEM = (
    "You estimate the annual compensation band, in Indian rupees lakhs per "
    "annum (LPA), that THIS company pays for THIS role at the stated years of "
    "experience. Use what you know of the company's pay scale, the role's "
    "seniority in the job description and the location. Return low_lpa and "
    "high_lpa as a realistic narrow band (not a wide market survey), and one "
    "short sentence of basis. Numbers only in the fields; never zero unless "
    "you truly cannot estimate."
)


def estimate_prompt(job: dict[str, Any], experience_years: str) -> str:
    """Only what the market rate depends on: the job and the level. The
    candidate's current pay is deliberately left out - it anchors the model
    toward it; the floor against current pay is applied in code afterwards."""
    return "\n\n".join(
        [
            f"JOB: {job.get('title', '')} at {job.get('company', '')}",
            f"LOCATION: {job.get('location', '') or '(see description)'}",
            f"JOB DESCRIPTION:\n{(job.get('description') or '')[:2500] or '(none)'}",
            f"YEARS OF EXPERIENCE: {experience_years or '(as the description requires)'}",
            "Estimate the band for this role at this level.",
        ]
    )


def midpoint_annual(estimate: SalaryEstimate) -> int | None:
    low, high = float(estimate.low_lpa), float(estimate.high_lpa)
    if low <= 0 and high <= 0:
        return None
    if low <= 0 or high <= 0:
        low = high = max(low, high)
    if high < low:
        low, high = high, low
    return int(round((low + high) / 2 * LAKH))
