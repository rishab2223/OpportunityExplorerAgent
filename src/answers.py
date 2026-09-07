"""The apply answer bank: questions you have answered once, reused everywhere.

Every application asks the same things - notice period, CTC, experience, work
authorization. This module keeps those answers in the known_answers table of
localData/job_history.db so the resolver can fill them with zero LLM calls and
the agent stops re-asking.

Keying is two-layer. A curated topic table collapses every phrasing of a common
question onto one slug ("Notice period", "What is your notice period?",
"Notice Period (in days)" -> notice_period), which is what survives sites
wording things differently. Anything unmatched falls back to the normalized
question text via profile.fingerprint().

Never stored: secrets (OTP, passwords, captchas - profile.is_secret) and
anything mentioning the company being applied to, so "Why do you want to work
at Capgemini?" is never replayed at Google. Sensitive answers (work auth, EEO,
criminal record) are stored only after a one-time confirmation and every reuse
is logged visibly by the caller.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src import db
from src.apply import profile

# Curated topics: slug -> pattern over the normalized question text.
# Order matters only for readability; keys are checked in definition order.
TOPICS: dict[str, re.Pattern[str]] = {
    "notice_period": re.compile(r"\bnotice\b"),
    "current_ctc": re.compile(r"\b(current|present)\b.*\b(ctc|salary|compensation|pay)\b"),
    "expected_ctc": re.compile(r"\b(expected|desired)\b.*\b(ctc|salary|compensation|pay)\b"),
    "total_experience": re.compile(r"\b(years?|yrs?)\b.*\bexperience\b|\bexperience\b.*\b(years?|yrs?)\b"),
    "work_authorization": re.compile(r"\b(authori[sz]ed?|authori[sz]ation|legally|eligib\w*)\b.*\bwork\b|\bwork\b.*\b(authori[sz]ed?|authori[sz]ation|permit)\b"),
    "sponsorship": re.compile(r"\bsponsor\w*\b|\bvisa\b"),
    "relocation": re.compile(r"\brelocat\w*\b"),
    "remote_preference": re.compile(r"\b(remote|hybrid|work from home|wfh|on[- ]?site)\b"),
    "start_date": re.compile(r"\b(start date|available to start|joining date|when can you (start|join))\b"),
    "current_location": re.compile(r"\b(current|present)\b.*\b(location|city)\b|\bwhere\b.*\b(located|based)\b"),
    "linkedin_url": re.compile(r"\blinkedin\b"),
    "github_url": re.compile(r"\bgithub\b"),
    "portfolio_url": re.compile(r"\b(portfolio|personal (web)?site)\b"),
    # Anchored: a loose \bphone\b collapsed "Phone Device Type", "Country
    # Phone Code" and "Phone Extension" onto one key, so "Mobile" (the device
    # type) was replayed as a country code. The profile fills the real phone
    # and email fields anyway; the bank only needs the plain question.
    "phone": re.compile(r"^(mobile|phone|cell|telephone|contact)( phone)?( number| no)?$"),
    "email": re.compile(r"^(work |primary |personal |your )?e[- ]?mail( address| id)?$"),
    "gender": re.compile(r"\bgender\b|\bsex\b"),
    "disability": re.compile(r"\bdisabilit\w*\b"),
    "veteran": re.compile(r"\bveteran\b|\bmilitary\b"),
    "race_ethnicity": re.compile(r"\brace\b|\bethnicit\w*\b"),
    "criminal_record": re.compile(r"\bcriminal\b|\bconvict\w*\b|\bbackground check\b"),
    "referral_source": re.compile(r"\bhow did you (hear|find|learn)\b"),
}

# Topics whose answers are legally meaningful; stored only after a one-time
# confirmation, and every reuse is logged loudly by the caller.
SENSITIVE_TOPICS = frozenset(
    ("work_authorization", "sponsorship", "gender", "disability", "veteran",
     "race_ethnicity", "criminal_record")
)


def question_key(label: str, group: str = "") -> str:
    """Stable key for a question, however the site phrases or places it.

    Combines the field label with its group (the legend/block text where the
    real question often lives for radios), matches topics first, then falls
    back to the normalized text itself.
    """
    text = profile.fingerprint(f"{label or ''} {group or ''}")
    if not text:
        return ""
    for slug, pattern in TOPICS.items():
        if pattern.search(text):
            return slug
    return text


def classify(label: str, group: str = "") -> str:
    """'secret' (never stored), 'sensitive' (confirm once), or 'neutral'."""
    combined = f"{label or ''} {group or ''}"
    if profile.is_secret(combined):
        return "secret"
    if question_key(label, group) in SENSITIVE_TOPICS:
        return "sensitive"
    return "neutral"


def recall(label: str, group: str = "") -> dict[str, str] | None:
    """The stored entry for this question, bumping its usage counter."""
    key = question_key(label, group)
    if not key:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT question_key, question, answer, kind FROM known_answers"
            " WHERE question_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        with conn:
            conn.execute(
                "UPDATE known_answers SET times_used = times_used + 1, last_used = ?"
                " WHERE question_key = ?",
                (datetime.now(timezone.utc).isoformat(), key),
            )
    finally:
        conn.close()
    return {"key": row["question_key"], "question": row["question"],
            "answer": row["answer"], "kind": row["kind"]}


def lookup(key: str) -> dict[str, str] | None:
    """The stored entry for a topic slug, without counting it as a use."""
    if not key:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT question_key, question, answer, kind FROM known_answers"
            " WHERE question_key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"key": row["question_key"], "question": row["question"],
            "answer": row["answer"], "kind": row["kind"]}


def remember(label: str, answer: str, group: str = "", company: str = "") -> str:
    """Store one answer. Returns the key used, or '' when it must not be stored."""
    key = question_key(label, group)
    text = f"{label or ''} {group or ''}"
    answer = (answer or "").strip()
    if not key or not answer:
        return ""
    if profile.is_secret(text) or profile.is_secret(answer):
        return ""
    # A question or answer naming this company is company-specific by
    # definition; replaying it elsewhere would be wrong.
    if company and len(company) >= 3 and (
        company.lower() in text.lower() or company.lower() in answer.lower()
    ):
        return ""
    kind = "sensitive" if key in SENSITIVE_TOPICS else "neutral"
    now = datetime.now(timezone.utc).isoformat()
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO known_answers"
                " (question_key, question, answer, kind, times_used, first_seen, last_used)"
                " VALUES (?, ?, ?, ?, 0, ?, ?)"
                " ON CONFLICT(question_key) DO UPDATE SET"
                "  question = excluded.question, answer = excluded.answer,"
                "  kind = excluded.kind, last_used = excluded.last_used",
                # For radios the group carries the real question ("Do you
                # require sponsorship?"), the label just the option ("No").
                (key, (group or label or "").strip(), answer, kind, now, now),
            )
    finally:
        conn.close()
    return key


def forget(key: str) -> bool:
    conn = db.connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM known_answers WHERE question_key = ?", (key,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def entries(limit: int = 40) -> list[dict[str, Any]]:
    """Most-used answers first, for the KNOWN ANSWERS block in the prompt."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT question_key, question, answer, kind FROM known_answers"
            " ORDER BY times_used DESC, last_used DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def migrate_learned(learned: dict[str, str]) -> int:
    """One-time copy of apply_profile.json's old 'learned' map. Copy-only:
    the JSON is left untouched, and existing bank rows are not overwritten."""
    count = 0
    for question, answer in (learned or {}).items():
        key = question_key(question)
        if not key or not (answer or "").strip():
            continue
        conn = db.connect()
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO known_answers"
                    " (question_key, question, answer, kind, times_used, first_seen)"
                    " VALUES (?, ?, ?, ?, 0, ?)",
                    (key, question, answer.strip(),
                     "sensitive" if key in SENSITIVE_TOPICS else "neutral",
                     datetime.now(timezone.utc).isoformat()),
                )
                count += cursor.rowcount
        finally:
            conn.close()
    return count
