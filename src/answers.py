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

# Every word that can appear in a question about someone's TOTAL experience.
# Anything else in the question names a SKILL, and the answer belongs to that
# skill alone.
#
# This is a word list rather than a pattern because the shapes are endless
# ("total years of experience", "experience in years", "how many years of work
# experience do you have") while the vocabulary is tiny - and because the
# pattern that was here matched every skill-specific question LinkedIn asks.
# "How many years of Travel Arrangements experience do you have?" answered 0,
# correctly, was stored as this candidate's total experience and replayed into
# nineteen later applications, including a "Years of work experience *" box on
# a profile that says six.
#
# "relevant" is deliberately absent: relevant experience is a different
# question and gets its own entry in the bank rather than overwriting this one.
_TOTAL_EXPERIENCE_WORDS = frozenset("""
a an and any applicable as at be do date enter experience for full has have how
if in including industry internship internships is it many much must number of
only optional overall combined cumulative please required mandatory s specify
the till time to total what with work working professional year years yr yrs
you your
""".split())
_YEAR_WORDS = frozenset(("year", "years", "yr", "yrs"))
# "Total experience till date" asks for the whole career without saying
# "years"; a bare "Experience" does not say enough to be sure.
_WHOLE_WORDS = frozenset(("total", "overall", "cumulative", "combined"))


def _is_total_experience(text: str) -> bool:
    words = text.split()
    if "experience" not in words:
        return False
    if not (_YEAR_WORDS.intersection(words) or _WHOLE_WORDS.intersection(words)):
        return False
    return all(word in _TOTAL_EXPERIENCE_WORDS for word in words)


# Curated topics: slug -> a pattern over the normalized question text, or a
# predicate taking that text. Order matters only for readability; keys are
# checked in definition order.
TOPICS: dict[str, Any] = {
    "notice_period": re.compile(r"\bnotice\b"),
    # The abbreviations are how Indian forms label these: ECTC, CCTC, Exp CTC.
    # Without them a box labelled just "ECTC" was not an expected-pay box at
    # all, so the salary estimator - which quotes a band for the role and
    # explains where the figure came from - never ran, and the number came
    # from the profile with no working shown.
    #
    # A bare "CTC" is deliberately left out. It usually means current pay, but
    # a form that means expected by it would get the candidate's current
    # salary quoted as their expectation, and guessing wrong in that direction
    # costs them money. The model reads it in context instead.
    "current_ctc": re.compile(
        r"\b(current|present)\b.*\b(ctc|salary|compensation|pay)\b|\bcctc\b"
        # The same word order the expected pattern needs: "Salary paid by your
        # current employer" names the noun first and was read as a question of
        # its own, so the profile could not answer it.
        r"|\b(ctc|salary|compensation|pay|package|remuneration)\b"
        r".*\b(current|present|currently|today|now)\b"),
    "expected_ctc": re.compile(
        # "E-CTC" fingerprints to "e ctc": punctuation is stripped, not joined.
        r"\b(expected|desired)\b.*\b(ctc|salary|compensation|pay)\b"
        # ...and the other word order, which is the commoner one in prose:
        # "What are your overall compensation expectations?", "Salary
        # expectations", "What are your salary requirements?". Asking for the
        # qualifier FIRST missed every one of them, so no estimate ran and the
        # saved figure went in unchanged, whatever the job was worth.
        r"|\b(ctc|salary|compensation|pay|package|remuneration)\b"
        r".*\b(expectation|expectations|expected|requirement|requirements)\b"
        r"|\bectc\b|\bexp ctc\b|\be ctc\b"),
    "total_experience": lambda text: _is_total_experience(text),
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
    for slug, test in TOPICS.items():
        if test(text) if callable(test) else test.search(text):
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
