"""The minimum years of experience a job description asks for.

Esko's "Software Engineer Specialist" asked for "8+ years of professional
software engineering experience" and reached the shortlist at relevance 7: the
scorer listed the gap and scored the rest of the role. Whether a gap like that
rules a job out is the candidate's call, so the filter that uses this is off
unless settings.yaml turns it on (`experience:`).

Only the GENERAL requirement counts. "8+ years of professional software
engineering experience" is one; "3+ years experience in React, TypeScript" is
a skill line, and taking it as the bar would drop jobs on one technology. A
line counts as general when a word about the work itself - software,
engineering, development, backend, industry - follows within a few words. A
line that cannot be placed is ignored, which keeps the job: a missed
requirement costs one look at a posting, a wrong one costs a job that fit.

Checked against 157 real postings from earlier runs (Oct 2 2026).
"""

from __future__ import annotations

import re

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
}
_NUM = r"(\d{1,2}(?:\.\d)?|" + "|".join(_WORDS) + r")"

# "8+ years", "8 + yrs", "8-10 years", "8 to 10 years", "8 or more years",
# "3 year(s)". The number is group 1; a range keeps its lower end. Every dash
# postings use for a range: hyphen, non-breaking hyphen (U+2011, ANZ), figure
# dash, en and em dash.
_YEARS_RE = re.compile(
    r"\b" + _NUM + r"\s*(?:\+|plus|or more)?\s*"
    r"(?:(?:[-‐-―~]|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)(?:\(s\))?(?!\w)",
    re.IGNORECASE,
)
_EXPERIENCE_RE = re.compile(r"\bexp(?:erience|\.)?\b", re.IGNORECASE)
# A clause ends at a sentence stop, a bullet or a line; "Node.js" does not end one.
_CLAUSE_END_RE = re.compile(r"\.\s|[;\n•*✔✅]")

# A word about the work itself, as opposed to a tool.
_ANCHORS = frozenset("""
software engineering engineer development developer developing backend
fullstack programming coding industry professional relevant related overall
total applied production building
""".split())
# The company talking about itself, or schooling: "25 years later, having
# pioneered an industry" (Dexcom), "15 years full time education" (Accenture).
_NOT_EXPERIENCE = frozenset("we our us later ago history education schooling old founded anniversary".split())
# "Over the past ten years, we have built ... engineering practice" (Ace).
_HISTORY_HEAD_RE = re.compile(r"\b(?:past|last|next|first)\s*$", re.IGNORECASE)
# A bare "N years of experience (is required)", ended or followed by
# anything but a tool: "6 years (Minimum experience required) Strong ...".
_BARE = frozenset("of experience exp is are required preferred minimum at least s".split())
# ...whereas "N years of experience with/in X" names X, a skill.
_PREPOSITIONS = frozenset("with in on using across as of for at".split())
_WORD_RE = re.compile(r"[a-z]+")
_ANCHOR_WINDOW = 8


def _number(text: str) -> float:
    text = text.lower()
    return float(_WORDS[text]) if text in _WORDS else float(text)


def _words(text: str) -> list[str]:
    text = text.lower().replace("full stack", "fullstack").replace("full-stack", "fullstack")
    text = text.replace("back end", "backend").replace("back-end", "backend")
    return _WORD_RE.findall(text)


def _is_general(clause: str) -> bool:
    words = _words(clause)[:_ANCHOR_WINDOW]
    if not words or _NOT_EXPERIENCE.intersection(words):
        return False
    if _ANCHORS.intersection(words):
        return True
    run = 0
    while run < len(words) and words[run] in _BARE:
        run += 1
    if "experience" not in words[:run]:
        return False
    return run == len(words) or words[run] not in _PREPOSITIONS


_PROFILE_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|years?|yrs?)?", re.IGNORECASE)
_PROFILE_MONTHS_RE = re.compile(r"(\d+)\s*(?:months?|mos?)\b", re.IGNORECASE)


def candidate_years(value: object) -> float | None:
    """The profile's total_experience_years as a number: "6", "6.5", "6+",
    "6 years 7 months". None when it is empty or holds no number."""
    text = str(value or "").strip()
    years = _PROFILE_YEARS_RE.match(text)
    if not years:
        return None
    months = _PROFILE_MONTHS_RE.search(text[years.end():])
    return float(years.group(1)) + (int(months.group(1)) / 12 if months else 0)


def required_years(description: str) -> float | None:
    """The largest GENERAL minimum the description states, or None."""
    text = description or ""
    found: list[float] = []
    for match in _YEARS_RE.finditer(text):
        years = _number(match.group(1))
        if not 1 <= years <= 25:
            continue                      # "over 50 years of history", "0-3 years"
        if _HISTORY_HEAD_RE.search(text[max(0, match.start() - 12):match.start()]):
            continue
        tail = text[match.end():match.end() + 160]
        end = _CLAUSE_END_RE.search(tail)
        clause = tail[:end.start()] if end else tail
        if _is_general(clause):
            found.append(years)
            continue
        # "Experience: 8-10 years", "Years of Experience 8-12 years of ..."
        if _EXPERIENCE_RE.search(text[max(0, match.start() - 30):match.start()]):
            found.append(years)
    return max(found) if found else None
