"""The Applied page: what happened to each application after it went in (#40).

History knew only "applied", so 89 applications had no outcome recorded and
nothing could say which channel, score or kind of company got replies. A job
is on this page because job_history marks it applied; its stage, notes,
contact, follow-up date and timeline live in the `applications` and
`application_events` tables, and its messages in `outreach_messages`.

Stages are set by the candidate. Nothing here guesses a stage from silence:
"no response" is left for later, on purpose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src import db

STAGES = ("applied", "screening", "interview", "assessment", "offer", "accepted")
ENDS = ("rejected", "withdrawn")
ALL_STAGES = STAGES + ENDS
MESSAGE_KINDS = ("referral", "outreach", "follow_up", "thank_you")
# Days without a change before the page suggests a follow-up.
FOLLOW_UP_DAYS = 10
# After Mark sent: when to look again.
AFTER_SENT_DAYS = 7


class NotApplied(Exception):
    """The job is not marked applied, so it is not on the Applied page."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _days(since: datetime | None, now: datetime) -> int | None:
    return None if since is None else max(0, (now - since).days)


def _applied_row(conn, job_id: str):
    row = conn.execute(
        "SELECT job_id, company, title, location, source, apply_url, stamp, marked_at, contact, description"
        " FROM job_history WHERE job_id = ? AND status = 'applied'", (job_id,)).fetchone()
    if row is None:
        raise NotApplied(f"{job_id} is not marked applied")
    return row


def _ensure(conn, job_id: str) -> None:
    """The applications row, created at stage Applied on first touch."""
    applied = _applied_row(conn, job_id)
    conn.execute(
        "INSERT OR IGNORE INTO applications (job_id, stage, stage_at, updated_at) VALUES (?, 'applied', ?, ?)",
        (job_id, applied["marked_at"] or _now().isoformat(), _now().isoformat()))


def _event(conn, job_id: str, kind: str, text: str = "") -> None:
    conn.execute("INSERT INTO application_events (job_id, kind, text, at) VALUES (?, ?, ?, ?)",
                 (job_id, kind, text, _now().isoformat()))


def _scores() -> dict[str, dict[str, Any]]:
    """{job_id: {relevance, score_scale, ...}} from the run files, newest run
    first - the score lives with the run, not in the history."""
    from src.web import runs

    found: dict[str, dict[str, Any]] = {}
    for stamp in runs.list_stamps():
        try:
            rows = runs.raw_rows(stamp)
        except (runs.RunNotFound, OSError, ValueError):
            continue
        for row in rows:
            jid = row.get("job_id")
            if jid and jid not in found:
                found[jid] = {"relevance": row.get("relevance"), "score_scale": row.get("score_scale") or 10,
                              "description": row.get("description") or "",
                              "listing_url": row.get("listing_url") or ""}
    return found


def applications(now: datetime | None = None) -> list[dict[str, Any]]:
    """Every applied job with where it stands, newest application first."""
    now = now or _now()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT h.job_id, h.company, h.title, h.location, h.source, h.apply_url, h.stamp,"
            " h.marked_at, h.contact AS history_contact,"
            " a.stage, a.stage_at, a.notes, a.contact_name, a.contact_role, a.contact_link,"
            " a.contact_relation, a.follow_up_at, a.updated_at"
            " FROM job_history h LEFT JOIN applications a ON a.job_id = h.job_id"
            " WHERE h.status = 'applied'").fetchall()
        sent = conn.execute("SELECT job_id, kind, sent_at FROM outreach_messages").fetchall()
        last_event = dict(conn.execute(
            "SELECT job_id, MAX(at) FROM application_events GROUP BY job_id").fetchall())
    finally:
        conn.close()
    messages: dict[str, dict[str, str]] = {}
    for m in sent:
        messages.setdefault(m["job_id"], {})[m["kind"]] = m["sent_at"] or ""
    scores = _scores()
    out = []
    for r in rows:
        applied_at = _parse(r["marked_at"])
        stage = r["stage"] or "applied"
        stage_at = _parse(r["stage_at"]) or applied_at
        changed = max(filter(None, [stage_at, _parse(last_event.get(r["job_id"]))]), default=applied_at)
        follow_up_at = _parse(r["follow_up_at"])
        open_ = stage not in ENDS and stage != "accepted"
        if follow_up_at is not None:
            due = open_ and follow_up_at <= now
        else:
            due = open_ and (_days(changed, now) or 0) >= FOLLOW_UP_DAYS
        score = scores.get(r["job_id"], {})
        out.append({
            "job_id": r["job_id"], "company": r["company"] or "", "title": r["title"] or "",
            "location": r["location"] or "", "source": r["source"] or "", "apply_url": r["apply_url"] or "",
            "listing_url": score.get("listing_url", ""), "stamp": r["stamp"] or "",
            "applied_at": r["marked_at"] or "", "days_since_applied": _days(applied_at, now),
            "stage": stage, "stage_at": stage_at.isoformat() if stage_at else "",
            "last_update": changed.isoformat() if changed else "",
            "days_since_update": _days(changed, now),
            "follow_up_at": r["follow_up_at"] or "", "follow_up_due": bool(due),
            "notes": r["notes"] or "",
            "contact": {"name": r["contact_name"] or "", "role": r["contact_role"] or "",
                        "link": r["contact_link"] or "", "relation": r["contact_relation"] or ""},
            "messages_sent": messages.get(r["job_id"], {}),
            "relevance": score.get("relevance"), "score_scale": score.get("score_scale", 10),
        })
    out.sort(key=lambda a: a["applied_at"], reverse=True)
    return out


def summary(apps: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers above the board: how many at each stage, and how many
    moved past Applied at all (a reply of any kind)."""
    counts = {stage: 0 for stage in ALL_STAGES}
    for a in apps:
        counts[a["stage"]] = counts.get(a["stage"], 0) + 1
    replied = sum(1 for a in apps if a["stage"] not in ("applied", "withdrawn"))
    return {"counts": counts, "total": len(apps), "replied": replied,
            "follow_ups_due": sum(1 for a in apps if a["follow_up_due"])}


def set_stage(job_id: str, stage: str, note: str = "") -> None:
    if stage not in ALL_STAGES:
        raise ValueError(f"stage must be one of {ALL_STAGES}")
    conn = db.connect()
    try:
        with conn:
            _ensure(conn, job_id)
            now = _now().isoformat()
            conn.execute("UPDATE applications SET stage = ?, stage_at = ?, follow_up_at = NULL,"
                         " updated_at = ? WHERE job_id = ?", (stage, now, now, job_id))
            _event(conn, job_id, "stage", stage + (f": {note}" if note else ""))
    finally:
        conn.close()


def set_notes(job_id: str, notes: str) -> None:
    conn = db.connect()
    try:
        with conn:
            _ensure(conn, job_id)
            conn.execute("UPDATE applications SET notes = ?, updated_at = ? WHERE job_id = ?",
                         (notes, _now().isoformat(), job_id))
    finally:
        conn.close()


def set_contact(job_id: str, name: str = "", role: str = "", link: str = "", relation: str = "") -> None:
    conn = db.connect()
    try:
        with conn:
            _ensure(conn, job_id)
            conn.execute("UPDATE applications SET contact_name = ?, contact_role = ?, contact_link = ?,"
                         " contact_relation = ?, updated_at = ? WHERE job_id = ?",
                         (name.strip(), role.strip(), link.strip(), relation.strip(), _now().isoformat(), job_id))
    finally:
        conn.close()


def detail(job_id: str) -> dict[str, Any]:
    """One application with its timeline, messages and posting text."""
    found = next((a for a in applications() if a["job_id"] == job_id), None)
    if found is None:
        raise NotApplied(f"{job_id} is not marked applied")
    conn = db.connect()
    try:
        events = conn.execute("SELECT kind, text, at FROM application_events WHERE job_id = ? ORDER BY id",
                              (job_id,)).fetchall()
        messages = conn.execute("SELECT kind, text, sent_at, updated_at FROM outreach_messages WHERE job_id = ?",
                                (job_id,)).fetchall()
        stored = conn.execute("SELECT description FROM job_history WHERE job_id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    found["timeline"] = ([{"kind": "applied", "text": f"Applied via {found['source'] or 'the site'}",
                           "at": found["applied_at"]}]
                         + [dict(e) for e in events])
    found["messages"] = {m["kind"]: dict(m) for m in messages}
    found["description"] = _scores().get(job_id, {}).get("description") or (stored["description"] if stored else "")
    return found


def get_message(job_id: str, kind: str) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT kind, text, sent_at, updated_at FROM outreach_messages"
                           " WHERE job_id = ? AND kind = ?", (job_id, kind)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def save_message(job_id: str, kind: str, text: str) -> None:
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {MESSAGE_KINDS}")
    conn = db.connect()
    try:
        with conn:
            _applied_row(conn, job_id)
            conn.execute(
                "INSERT INTO outreach_messages (job_id, kind, text, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_id, kind) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
                (job_id, kind, text, _now().isoformat()))
    finally:
        conn.close()


def mark_sent(job_id: str, kind: str) -> str:
    """The candidate sent this message themselves. Returns the follow-up date."""
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {MESSAGE_KINDS}")
    now = _now()
    follow = (now + timedelta(days=AFTER_SENT_DAYS)).isoformat()
    conn = db.connect()
    try:
        with conn:
            _ensure(conn, job_id)
            conn.execute(
                "INSERT INTO outreach_messages (job_id, kind, text, sent_at, updated_at) VALUES (?, ?, '', ?, ?) "
                "ON CONFLICT(job_id, kind) DO UPDATE SET sent_at = excluded.sent_at",
                (job_id, kind, now.isoformat(), now.isoformat()))
            conn.execute("UPDATE applications SET follow_up_at = ?, updated_at = ? WHERE job_id = ?",
                         (follow, now.isoformat(), job_id))
            _event(conn, job_id, "sent", kind.replace("_", "-") + " message sent")
    finally:
        conn.close()
    return follow
