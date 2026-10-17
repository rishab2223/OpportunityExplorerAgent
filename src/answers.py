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
TOTAL_EXPERIENCE_WORDS = _TOTAL_EXPERIENCE_WORDS
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


# A question about notice that is not asking for the notice period: the last
# working day, a date in a set shape, or what to enter when it does not apply.
# "If you are serving notice period or your last working day is completed,
# please share the details (... enter as 19112026, if not serving enter "0")"
# (Oolio, Oct 2026) took the saved notice period, "join immediately", where
# the model had read it right and answered 0. Over normalized text (no
# punctuation); the profile's notice rule in resolver.py uses it too.
NOT_NOTICE_PERIOD = (
    r"\b(last|final) working (day|date)\b|\blwd\b|\breliev(ing|al) date\b"
    r"|\bdate of (leaving|relieving|resignation)\b|\benter (it |the date )?as\b"
    r"|\bif (not|none|no)\b.{0,40}\benter\b|\bdd ?mm ?yy(yy)?\b"
    # A yes or no, not a length of time: "Are you currently serving your
    # notice period?" took the saved "join immediately".
    r"|^(are|r) (you|u) (currently |presently |now )?(serving|on)\b"
)
_NOT_NOTICE_PERIOD_RE = re.compile(NOT_NOTICE_PERIOD)

# A question about a link that is not asking for one: "How did you hear about
# us? (LinkedIn, Naukri...)" was keyed as the LinkedIn URL, and so was "I agree
# to allow my application statuses to be visible inside LinkedIn Recruiter" -
# whose saved "yes" then stood as the candidate's LinkedIn answer. Over
# normalized text; the profile's link rules in resolver.py use it too.
NOT_A_LINK = (
    r"^(?!(do|does|did|are|is|have|has|can|will|would)\b)"
    r"(?!.*\b(how did you|hear|heard|source|referr\w*|recruiter|agree|allow|consent|visible"
    r"|experience|years|familiar|actions|copilot)\b)"
)

# Countries a work-permit question can name. The candidate's own country is
# the plain key, shared with "the country you are applying for": a question
# about any other is a different question, with a different answer - "Are you
# authorized to work in the United States?" shared one saved "Yes" with India.
_COUNTRIES = (
    ("us", r"\bunited states\b|\bu ?s ?a\b|\bu s\b|\b(in|the) us\b|\bamerica\b"),
    ("uk", r"\bunited kingdom\b|\bu ?k\b|\bbritain\b|\bengland\b"),
    ("canada", r"\bcanad\w*\b"),
    ("australia", r"\baustralia\w*\b"),
    ("eu", r"\beurope\w*\b|\beu\b|\beea\b|\bschengen\b|\bgermany\b|\bnetherlands\b|\bireland\b|\bfrance\b"),
    ("singapore", r"\bsingapore\b"),
    ("uae", r"\buae\b|\bunited arab emirates\b|\bdubai\b"),
    ("india", r"\bindia\b"),
)

# Curated topics: slug -> a pattern over the normalized question text, or a
# predicate taking that text. Order matters only for readability; keys are
# checked in definition order.
TOPICS: dict[str, Any] = {
    "serving_notice": re.compile(r"^(are|r) (you|u) (currently |presently |now )?(serving|on)\b.*\bnotice\b"),
    "notice_period": lambda text: bool(
        re.search(r"\bnotice\b", text) and not _NOT_NOTICE_PERIOD_RE.search(text)),
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
    # Never when the question is about expected pay: "Expected CTC (Current
    # CTC + hike %)" got the current figure (code review, Oct 2026).
    "current_ctc": re.compile(
        r"^(?!.*\b(expected|desired|expectations?|ectc)\b).*?(?:"
        r"\b(current|present)\b.*\b(ctc|salary|compensation|pay)\b|\bcctc\b"
        # The same word order the expected pattern needs: "Salary paid by your
        # current employer" names the noun first and was read as a question of
        # its own, so the profile could not answer it.
        r"|\b(ctc|salary|compensation|pay|package|remuneration)\b"
        r".*\b(current|present|currently|today|now)\b)"),
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
    # Holding a visa or passport is not needing a sponsor: "Do you hold a
    # valid passport and visa?" shared "sponsorship" and its saved "no".
    "visa_held": lambda text: bool(
        re.search(r"\b(valid|hold|holding|have|possess|own)\b.*\b(visa|passport|work permit)\b", text)
        and not re.search(r"\bsponsor\w*\b", text)),
    "sponsorship": re.compile(r"\bsponsor\w*\b|\bvisa\b"),
    # Help with moving is not willingness to move.
    "relocation_assistance": re.compile(
        r"\brelocation (assistance|support|package|benefit|benefits|allowance|expenses?|bonus)\b"
        r"|\b(require|need|expect)\w*\b.*\brelocation\b"),
    "relocation": re.compile(r"\brelocat\w*\b"),
    "remote_preference": re.compile(r"\b(remote|hybrid|work from home|wfh|on[- ]?site)\b"),
    "start_date": re.compile(r"\b(start date|available to start|joining date|when can you (start|join))\b"),
    "current_location": re.compile(r"\b(current|present)\b.*\b(location|city)\b|\bwhere\b.*\b(located|based)\b"),
    # Before the links: "How did you hear about us? (LinkedIn, Naukri)".
    "referral_source": re.compile(r"\bhow did you (hear|find|learn)\b"),
    "linkedin_url": re.compile(NOT_A_LINK + r".*\blinkedin\b"),
    "github_url": re.compile(NOT_A_LINK + r".*\bgithub\b"),
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
    # Agreeing to a check is not a record: "Are you willing to undergo a
    # background check?" shared "criminal_record" with "Have you ever been
    # convicted?", so a saved Yes would have ticked Yes on a conviction.
    "background_check": lambda text: bool(
        re.search(r"\bbackground (check|verification|screening|investigation)s?\b|\bbgv\b", text)
        and not re.search(r"\bcriminal\b|\bconvict\w*\b", text)),
    "criminal_record": re.compile(r"\bcriminal\b|\bconvict\w*\b"),
    # A first or only citizenship. "Citizenship 2" (SuccessFactors) is a
    # second one: given its own key, it is never filled with the first's
    # answer.
    "citizenship": lambda text: bool(
        re.search(r"\b(citizenship|nationality)\b|\bcitizen of\b", text)
        and not re.search(r"\b(2|second|secondary|additional|dual|another)\b", text)),
    # "Do you have a potential Conflict of Interest as described above?"
    "conflict_of_interest": re.compile(r"\bconflicts? of interest\b"),
    "marital_status": re.compile(r"\bmarital\b|\bmarried\b"),
    # Holding one, yes or no. Its NUMBER is a government identifier and is
    # never stored (remember() refuses it). "(Yes/No)" in a label is not a
    # number, so a bare "no" does not count.
    "driving_licence_held": lambda text: bool(
        re.search(r"\bdriv(ing|ers?) licen[cs]e\b", text)
        and not re.search(r"\b(number|no\.|id)\b", text)),
}

# Topics whose answers are legally meaningful; stored only after a one-time
# confirmation, and every reuse is logged loudly by the caller.
SENSITIVE_TOPICS = frozenset(
    ("work_authorization", "sponsorship", "visa_held", "gender", "disability", "veteran",
     "race_ethnicity", "criminal_record", "background_check", "citizenship",
     "conflict_of_interest", "marital_status")
)


def _country_named(text: str) -> str:
    """The country a work-permit question names, '' for none or for the
    candidate's own."""
    for slug, pattern in _COUNTRIES:
        if re.search(pattern, text):
            data = profile.load_profile()
            home = str(data.get("country") or "") or str(data.get("location") or "").split(",")[-1]
            return "" if re.search(pattern, profile.fingerprint(home or "India")) else slug
    return ""


def _work_mode(text: str) -> str:
    """The one way of working a yes-or-no question asks about: "Are you open
    to remote work?" and "Are you comfortable working onsite 5 days a week?"
    shared one saved Yes. '' when the question names several (a preference)."""
    modes = set()
    if re.search(r"\b(remote|remotely|work from home|wfh)\b", text):
        modes.add("remote")
    if re.search(r"\bhybrid\b", text):
        modes.add("hybrid")
    if re.search(r"\b(on ?site|in office|office|wfo|in person|commut\w*)\b", text):
        modes.add("onsite")
    return modes.pop() if len(modes) == 1 else ""


# A topic whose questions differ in something the answer depends on gets that
# something in its key: "work_authorization:us" is never answered with the
# saved "work_authorization" (India).
_QUALIFIERS: dict[str, Any] = {
    "work_authorization": _country_named,
    "sponsorship": _country_named,
    "visa_held": _country_named,
    "remote_preference": _work_mode,
}


def topic_of(key: str) -> str:
    """The topic slug of a key, without its qualifier."""
    return (key or "").split(":", 1)[0]


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
            qualifier = _QUALIFIERS[slug](text) if slug in _QUALIFIERS else ""
            return f"{slug}:{qualifier}" if qualifier else slug
    return text


def classify(label: str, group: str = "") -> str:
    """'secret' (never stored), 'sensitive' (confirm once), or 'neutral'."""
    combined = f"{label or ''} {group or ''}"
    if profile.is_secret(combined):
        return "secret"
    if topic_of(question_key(label, group)) in SENSITIVE_TOPICS:
        return "sensitive"
    return "neutral"


# The whole table, read once per process and again after this process writes
# to it. The sweep looks a key up for every unresolved field of every
# snapshot, and each lookup was a connection, a query and a close. Only the
# web worker writes this table, so a stale read from another process is not
# a case that arises; the usage counters are not cached, so touch() need not
# invalidate.
_cache: dict[str, dict[str, str]] | None = None
_cache_path = ""


def _table() -> dict[str, dict[str, str]]:
    global _cache, _cache_path
    path = str(db._db_path())
    if _cache is not None and _cache_path == path:
        return _cache
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT question_key, question, answer, kind FROM known_answers"
        ).fetchall()
    finally:
        conn.close()
    _cache = {
        row["question_key"]: {"key": row["question_key"], "question": row["question"],
                              "answer": row["answer"], "kind": row["kind"]}
        for row in rows
    }
    _cache_path = path
    return _cache


def _invalidate() -> None:
    global _cache
    _cache = None


def recall(label: str, group: str = "") -> dict[str, str] | None:
    """The stored entry for this question, bumping its usage counter."""
    entry = saved_for(label, group)
    if entry is not None:
        touch(entry["key"])
    return entry


def touch(key: str) -> None:
    """Count one use of an entry. Separate from recall so that a sweep which
    looks an answer up and then cannot place it (no matching option, a type
    that takes no text) does not inflate the counter the prompt is ordered by."""
    if not key:
        return
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "UPDATE known_answers SET times_used = times_used + 1, last_used = ?"
                " WHERE question_key = ?",
                (datetime.now(timezone.utc).isoformat(), key),
            )
    finally:
        conn.close()


def lookup(key: str) -> dict[str, str] | None:
    """The stored entry for a topic slug, without counting it as a use.

    An entry is only ever the answer to its own question: one saved before a
    topic took qualifiers ("Are you authorized to work in the US?" under the
    plain "work_authorization") is read again to see which key it belongs to,
    and is not handed to a question that differs from it."""
    if not key:
        return None
    entry = _table().get(key)
    topic = topic_of(key)
    if entry is None and topic != key:
        entry = _table().get(topic)
    if entry is None:
        return None
    own = question_key(entry["question"]) if entry["question"].strip() else ""
    if topic_of(own) in TOPICS and own != key:
        return None
    shape = _ANSWER_SHAPES.get(topic)
    if shape is not None and not shape.search(entry["answer"] or ""):
        return None
    return dict(entry)


# What an answer to a topic must look like. "yes" was saved as the LinkedIn
# answer from a consent box that mentioned LinkedIn Recruiter, and would have
# gone into the next "LinkedIn Profile" box.
_ANSWER_SHAPES = {
    "linkedin_url": re.compile(r"[./]"),
    "github_url": re.compile(r"[./]"),
    "portfolio_url": re.compile(r"[./]"),
    "total_experience": re.compile(r"\d"),
    "current_ctc": re.compile(r"\d"),
    "expected_ctc": re.compile(r"\d"),
}
_YES_NO_RE = re.compile(r"^\s*(yes|no|y|n)\s*[.!]?\s*$", re.IGNORECASE)
# A question that wants something other than a yes or a no.
_OPEN_QUESTION_RE = re.compile(
    r"^(what|which|how|when|where|why|preferred|enter|specify|mention|list|describe"
    r"|please (specify|mention|enter|share|list|describe))\b")


def saved_for(label: str, group: str = "") -> dict[str, str] | None:
    """The stored answer to this question, when it can answer it: a saved
    "yes" is no answer to "Preferred work mode" or "Which visa do you hold?",
    whatever topic they share with a yes-or-no question."""
    entry = lookup(question_key(label, group))
    if entry is None:
        return None
    # The question is the group's when there is one: a radio's label is its
    # option, a dropdown button's its "Select One".
    if _YES_NO_RE.match(entry["answer"] or "") and \
            _OPEN_QUESTION_RE.search(profile.fingerprint(group or label or "")):
        return None
    return entry


def remember(label: str, answer: str, group: str = "", company: str = "") -> str:
    """Store one answer. Returns the key used, or '' when it must not be stored."""
    key = question_key(label, group)
    text = f"{label or ''} {group or ''}"
    answer = (answer or "").strip()
    if not key or not answer:
        return ""
    if profile.is_secret(text) or profile.is_secret(answer):
        return ""
    # A government identifier's number is never kept, here as in the profile.
    if profile.is_identifier(text) and key != "driving_licence_held":
        return ""
    # A question or answer naming this company is company-specific by
    # definition; replaying it elsewhere would be wrong.
    if company and len(company) >= 3 and (
        company.lower() in text.lower() or company.lower() in answer.lower()
    ):
        return ""
    kind = "sensitive" if topic_of(key) in SENSITIVE_TOPICS else "neutral"
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
    _invalidate()
    return key


def forget(key: str) -> bool:
    conn = db.connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM known_answers WHERE question_key = ?", (key,))
        return cursor.rowcount > 0
    finally:
        conn.close()
        _invalidate()


def all_entries() -> list[dict[str, Any]]:
    """Every saved answer, for the Saved answers page."""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT question_key, question, answer, kind, times_used, first_seen, last_used"
            " FROM known_answers ORDER BY question COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def update(key: str, answer: str) -> str:
    """Replace one saved answer by hand. Returns an error, or '' on success.
    The same guards as remember(): nothing secret is ever stored."""
    answer = (answer or "").strip()
    if not answer:
        return "an answer cannot be empty; delete it instead"
    if profile.is_secret(answer):
        return "that looks like a password or code, which is never stored"
    conn = db.connect()
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE known_answers SET answer = ? WHERE question_key = ?", (answer, key))
    finally:
        conn.close()
        _invalidate()
    return "" if cursor.rowcount else "no saved answer with that key"


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
