"""Cross-run memory of jobs you have already dealt with.

``outputs/{stamp}/applications.json`` only remembers decisions inside one run,
but Apify happily returns the same posting on the next run. This module keeps a
small SQLite database that survives runs, so a job you applied to is dropped
right after scrape - before it costs a scoring or enrichment call.

Matching is by ``job_id`` first (stable for ~95% of postings across runs) and
then, optionally, by a normalized company + title fingerprint, which catches
the rest when a site reissues its own id.

SQLite (stdlib, no server, no install): transactional writes, an indexed
fingerprint column, and WAL mode so the web server and a scheduled CLI run can
touch the file at the same time. Every call opens a short-lived connection, so
the module is safe from any thread. Inspect it any time with
``python -m sqlite3 localData/job_history.db``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from src import db

# The actual connection, schema and JOB_HISTORY_DB override live in src/db.py
# (shared with the answer bank). db.connect() reads this attribute, so tests
# can keep retargeting history.DB_PATH at a temp file.
DB_PATH = db.DB_PATH
# referral_pending: asking someone for a referral; referral_sent: application
# went in through that referral. A failed referral is deleted (forget), which
# is what puts the job back into the shortlist and future scrapes.
REFERRAL_STATUSES = ("referral_pending", "referral_sent")
# closed: the posting stopped accepting applications - detected during an
# apply session or marked by hand; dropped from future scrapes like applied.
VALID_STATUSES = ("applied", "skipped", "closed") + REFERRAL_STATUSES

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fingerprint(company: str, title: str) -> str:
    """Normalized ``company|title`` key, for when a site reissues its own job id."""
    parts = []
    for value in (company, title):
        text = _NON_ALNUM.sub(" ", (value or "").lower())
        parts.append(" ".join(text.split()))
    if not any(parts):
        return ""
    return "|".join(parts)


# How much of a description is kept and compared. The opening is where two
# different roles differ; the boilerplate is at the end.
DESCRIPTION_KEEP = 2000
# How alike two descriptions must be for a company + title match to count as
# the same posting. A repost, or one role posted in eleven cities, is near
# 1.0; Recrew AI's two "Backend Engineer" roles for two different clients
# (Oct 2026) were 0.045.
SAME_POSTING_RATIO = 0.6


def description_key(text: str | None) -> str:
    """The stored form of a description: lower case, one space, the opening."""
    return " ".join(str(text or "").lower().split())[:DESCRIPTION_KEEP]


def same_posting(entry: dict[str, Any] | None, description: str | None) -> bool:
    """Whether a company + title match is really this posting.

    A recruiter posts every client's role under its own name, so company and
    title alone marked a Bangalore job applied because a Gurugram one was.
    When both sides carry a description, they must be alike. When either
    does not (a row recorded before descriptions were kept, a posting with
    none), the match stands, as it always did."""
    if not entry:
        return False
    stored = entry.get("descriptions") or [""]
    mine = description_key(description)
    if not mine or any(not s for s in stored):
        return True
    for theirs in stored:
        matcher = SequenceMatcher(None, mine, theirs, autojunk=False)
        if matcher.quick_ratio() >= SAME_POSTING_RATIO and matcher.ratio() >= SAME_POSTING_RATIO:
            return True
    return False


def record(
    job: dict[str, Any], status: str, stamp: str = "", note: str = "", contact: str = ""
) -> dict[str, Any]:
    """Remember one job's outcome. Returns the stored entry."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise ValueError("job has no job_id")
    entry = {
        "job_id": job_id,
        "status": status,
        "company": job.get("company") or "",
        "title": job.get("title") or "",
        "location": job.get("location") or "",
        "source": job.get("source") or "",
        "apply_url": job.get("apply_url") or job.get("listing_url") or "",
        "fingerprint": fingerprint(job.get("company") or "", job.get("title") or ""),
        "stamp": stamp,
        "note": note,
        "contact": contact,
        "marked_at": datetime.now(timezone.utc).isoformat(),
        "description": description_key(job.get("description")),
    }
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO job_history"
                " (job_id, status, company, title, location, source, apply_url,"
                "  fingerprint, stamp, note, contact, marked_at, description)"
                " VALUES (:job_id, :status, :company, :title, :location, :source,"
                "  :apply_url, :fingerprint, :stamp, :note, :contact, :marked_at, :description)",
                entry,
            )
    finally:
        conn.close()
    return entry


def forget(job_id: str) -> bool:
    """Drop one job from the history (Unmark). Returns True if it was there."""
    conn = db.connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM job_history WHERE job_id = ?", (job_id,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def forget_similar(company: str, title: str) -> bool:
    """Drop the row a company + title fingerprint matched (Unmark on a row the
    table marked "similar"). That row carries another site's id for the same
    posting, so forgetting by this job's id found nothing and the button did
    nothing. Returns True if a row went."""
    fp = fingerprint(company, title)
    if not fp:
        return False
    conn = db.connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM job_history WHERE fingerprint = ?", (fp,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def lookup(job_id: str) -> dict[str, str] | None:
    """The stored entry for one job id, or None."""
    if not job_id:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status, contact, marked_at FROM job_history WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"status": row["status"], "contact": row["contact"] or "",
            "marked_at": row["marked_at"] or ""}


def snapshot(
    statuses: set[str] | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """One query: (entry by job_id, entry by fingerprint), optionally filtered.

    Each entry is ``{"status", "contact", "marked_at"}`` - the referrals view
    needs the contact and the asked-date, not just the status. The scrape
    filter and the UI table both fetch this once and then check each job
    against the in-memory dicts - no per-job queries.
    """
    sql = "SELECT job_id, fingerprint, status, contact, marked_at, description FROM job_history"
    params: tuple[str, ...] = ()
    if statuses is not None:
        if not statuses:
            return {}, {}
        sql += " WHERE status IN (%s)" % ",".join("?" for _ in statuses)
        params = tuple(sorted(statuses))
    conn = db.connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    entries = [
        (
            row["job_id"],
            row["fingerprint"],
            {
                "status": row["status"],
                "contact": row["contact"] or "",
                "marked_at": row["marked_at"] or "",
            },
        )
        for row in rows
    ]
    by_id = {job_id: entry for job_id, _, entry in entries}
    # Several rows can share a fingerprint (a recruiter's same-titled roles);
    # the entry carries every one's description, for same_posting().
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for row, (_, fp, entry) in zip(rows, entries):
        if not fp:
            continue
        held = by_fingerprint.get(fp)
        texts = (held["descriptions"] if held else []) + [row["description"] or ""]
        by_fingerprint[fp] = {**entry, "descriptions": texts}
    return by_id, by_fingerprint


def backfill_descriptions(find) -> int:
    """Fill in descriptions for rows recorded before they were kept.

    `find(stamp)` returns {job_id: description} for that run's jobs, or {}.
    Each run is read once. Returns how many rows gained one."""
    conn = db.connect()
    try:
        missing = conn.execute(
            "SELECT job_id, stamp FROM job_history"
            " WHERE (description IS NULL OR description = '') AND stamp != ''"
        ).fetchall()
        by_stamp: dict[str, list[str]] = {}
        for row in missing:
            by_stamp.setdefault(row["stamp"], []).append(row["job_id"])
        filled = 0
        with conn:
            for stamp, ids in by_stamp.items():
                texts = find(stamp) or {}
                for job_id in ids:
                    text = description_key(texts.get(job_id))
                    if text:
                        conn.execute("UPDATE job_history SET description = ? WHERE job_id = ?",
                                     (text, job_id))
                        filled += 1
        return filled
    finally:
        conn.close()
