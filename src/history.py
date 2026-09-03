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

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import ROOT

# JOB_HISTORY_DB redirects the whole module to another file. Set it when
# testing against a live server so record/forget can never touch your real
# applied/referral history:  JOB_HISTORY_DB=localData/scratch_history.db
_ENV_DB = os.environ.get("JOB_HISTORY_DB", "").strip()
DB_PATH = Path(_ENV_DB) if _ENV_DB else ROOT / "localData" / "job_history.db"
# referral_pending: asking someone for a referral; referral_sent: application
# went in through that referral. A failed referral is deleted (forget), which
# is what puts the job back into the shortlist and future scrapes.
REFERRAL_STATUSES = ("referral_pending", "referral_sent")
VALID_STATUSES = ("applied", "skipped") + REFERRAL_STATUSES

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_history (
  job_id      TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  company     TEXT,
  title       TEXT,
  location    TEXT,
  source      TEXT,
  apply_url   TEXT,
  fingerprint TEXT,
  stamp       TEXT,
  note        TEXT,
  contact     TEXT,
  marked_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_fingerprint ON job_history(fingerprint);
"""


def fingerprint(company: str, title: str) -> str:
    """Normalized ``company|title`` key, for when a site reissues its own job id."""
    parts = []
    for value in (company, title):
        text = _NON_ALNUM.sub(" ", (value or "").lower())
        parts.append(" ".join(text.split()))
    if not any(parts):
        return ""
    return "|".join(parts)


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
    }
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO job_history"
                " (job_id, status, company, title, location, source, apply_url,"
                "  fingerprint, stamp, note, contact, marked_at)"
                " VALUES (:job_id, :status, :company, :title, :location, :source,"
                "  :apply_url, :fingerprint, :stamp, :note, :contact, :marked_at)",
                entry,
            )
    finally:
        conn.close()
    return entry


def forget(job_id: str) -> bool:
    """Drop one job from the history (Unmark). Returns True if it was there."""
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM job_history WHERE job_id = ?", (job_id,))
        return cursor.rowcount > 0
    finally:
        conn.close()


def snapshot(
    statuses: set[str] | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """One query: (entry by job_id, entry by fingerprint), optionally filtered.

    Each entry is ``{"status", "contact", "marked_at"}`` - the referrals view
    needs the contact and the asked-date, not just the status. The scrape
    filter and the UI table both fetch this once and then check each job
    against the in-memory dicts - no per-job queries.
    """
    sql = "SELECT job_id, fingerprint, status, contact, marked_at FROM job_history"
    params: tuple[str, ...] = ()
    if statuses is not None:
        if not statuses:
            return {}, {}
        sql += " WHERE status IN (%s)" % ",".join("?" for _ in statuses)
        params = tuple(sorted(statuses))
    conn = _connect()
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
    by_fingerprint = {fp: entry for _, fp, entry in entries if fp}
    return by_id, by_fingerprint


_INIT_LOCK = threading.Lock()
_initialized: set[str] = set()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        key = str(DB_PATH)
        if key not in _initialized:
            # Serialize first-time setup: two threads racing to create the
            # schema on a fresh file would collide on the schema transaction.
            with _INIT_LOCK:
                if key not in _initialized:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                    # Databases created before the referral feature lack the
                    # contact column; upgrade them in place.
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_history)")}
                    if "contact" not in cols:
                        conn.execute("ALTER TABLE job_history ADD COLUMN contact TEXT")
                    _initialized.add(key)
        return conn
    except BaseException:
        conn.close()
        raise
