from __future__ import annotations

import json
from pathlib import Path
from queue import Empty
from typing import Iterator

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src import history, progress
from src.config import load_env, load_yaml_config
from src.errors import StepError
from src.pdf_compile import ensure_pdf
from src.resume.loader import load_resume
from src.web import runner, runs

HEARTBEAT_SECONDS = 15

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Opportunity Explorer")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/runs")
def api_runs() -> dict:
    return {"stamps": runs.list_stamps(), **runner.status()}


@app.post("/api/runs", status_code=202)
def api_start_run(payload: dict = Body(default={})) -> dict:
    try:
        stamp = runner.start(
            resume_path=str(payload.get("resume_path") or ""),
            max_detail_jobs=payload.get("max_detail_jobs"),
        )
    except runner.RunBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"stamp": stamp}


@app.get("/api/runs/status")
def api_run_status() -> dict:
    return runner.status()


@app.get("/api/runs/latest")
def api_latest_run() -> dict:
    stamp = runs.latest_stamp()
    if not stamp:
        raise HTTPException(status_code=404, detail="no runs found under outputs/")
    return runs.load_run(stamp)


@app.get("/api/runs/{stamp}")
def api_run(stamp: str) -> dict:
    try:
        return runs.load_run(stamp)
    except runs.RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{stamp}/logs")
def api_logs(stamp: str) -> StreamingResponse:
    return StreamingResponse(
        _log_stream(stamp),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{stamp}/jobs")
def api_jobs(stamp: str) -> dict:
    try:
        return {"stamp": stamp, "jobs": runs.load_jobs(stamp)}
    except runs.RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{stamp}/jobs/{job_id}/resume.pdf")
def api_resume_pdf(stamp: str, job_id: str) -> FileResponse:
    job = _job_or_404(stamp, job_id)
    tex_file = job.get("resume_tex_file") or ""
    if not tex_file:
        raise HTTPException(status_code=404, detail="this run has no tailored .tex for that job")
    tex_path = Path(runs.run_dir(stamp)) / tex_file
    pdf_path, error = ensure_pdf(tex_path)
    if pdf_path is None:
        raise HTTPException(status_code=422, detail=error)
    return FileResponse(pdf_path, media_type="application/pdf")


@app.post("/api/runs/{stamp}/jobs/{job_id}/decision")
def api_decision(stamp: str, job_id: str, payload: dict = Body(...)) -> dict:
    decision = str(payload.get("decision") or "").lower()
    if decision not in runs.VALID_DECISIONS:
        raise HTTPException(status_code=400, detail="decision must be 'yes' or 'no'")
    job = _job_or_404(stamp, job_id)
    status = "skipped" if decision == "no" else "pending"
    if decision == "no":
        # Recorded for reference; skipped jobs still return next run unless
        # history.skip_skipped is turned on.
        history.record(job, "skipped", stamp=stamp)
    return runs.save_decision(stamp, job_id, decision=decision, status=status)


@app.post("/api/runs/{stamp}/jobs/{job_id}/history")
def api_history(stamp: str, job_id: str, payload: dict = Body(...)) -> dict:
    """Mark a job applied/skipped/referral across runs, or clear it with an empty status."""
    status = str(payload.get("status") or "")
    contact = str(payload.get("contact") or "").strip()
    job = _job_or_404(stamp, job_id)
    if not status:
        removed = history.forget(job_id)
        return {"job_id": job_id, "history_status": "", "removed": removed}
    if status not in history.VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {history.VALID_STATUSES} or empty to clear",
        )
    entry = history.record(job, status, stamp=stamp, note="manual", contact=contact)
    return {
        "job_id": job_id,
        "history_status": entry["status"],
        "history_contact": entry["contact"],
    }


@app.post("/api/apply/start")
def api_apply_start(payload: dict = Body(...)) -> dict:
    from src.apply import profile as apply_profile
    from src.apply import session as apply_session
    from src.apply import worker as apply_worker

    stamp = str(payload.get("stamp") or "")
    job_id = str(payload.get("job_id") or "")
    job = _job_or_404(stamp, job_id)
    if not (job.get("apply_url") or job.get("listing_url")):
        raise HTTPException(status_code=422, detail="this job has no apply or listing URL")

    cfg = load_yaml_config()
    env = load_env()
    # First apply ever: create the profile template so the user has a file to fill.
    apply_profile.ensure_profile_file()
    pdf_path = _tailored_pdf(stamp, job)
    try:
        resume_text, _, _, _ = load_resume(cfg)
    except StepError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    def on_finish(status: str) -> None:
        runs.save_decision(stamp, job_id, status=status)
        if status == "applied":
            history.record(job, "applied", stamp=stamp, note="assisted")

    # Mark running before the worker starts: a session that fails within
    # milliseconds would otherwise have its final status overwritten here.
    runs.save_decision(stamp, job_id, decision="yes", status="running")
    try:
        sess = apply_worker.start_apply(
            stamp, job, resume_text, pdf_path, cfg, env, on_finish=on_finish
        )
    except apply_session.SessionBusy as exc:
        runs.save_decision(stamp, job_id, status="pending")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sess.snapshot()


@app.get("/api/apply/status")
def api_apply_status() -> dict:
    from src.apply import session as apply_session

    sess = apply_session.current()
    return sess.snapshot() if sess else {"status": "idle"}


@app.get("/api/apply/{session_id}/events")
def api_apply_events(session_id: str) -> StreamingResponse:
    return StreamingResponse(
        _apply_stream(session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/apply/{session_id}/chat")
def api_apply_chat(session_id: str, payload: dict = Body(...)) -> dict:
    sess = _session_or_404(session_id)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    sess.answer(text)
    return sess.snapshot()


@app.post("/api/apply/{session_id}/abort")
def api_apply_abort(session_id: str) -> dict:
    sess = _session_or_404(session_id)
    sess.abort()
    return sess.snapshot()


def _session_or_404(session_id: str):
    from src.apply import session as apply_session

    sess = apply_session.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="no such apply session")
    return sess


def _apply_stream(session_id: str) -> Iterator[str]:
    from src.apply import session as apply_session

    sess = apply_session.get(session_id)
    if sess is None:
        yield _sse({"type": "error", "text": "no such apply session"})
        return
    queue, backlog = sess.subscribe()
    try:
        for event in backlog:
            yield _sse(event)
            if event.get("type") == "done":
                return
        while True:
            try:
                event = queue.get(timeout=HEARTBEAT_SECONDS)
            except Empty:
                yield ": ping\n\n"
                continue
            yield _sse(event)
            if event.get("type") == "done":
                return
    finally:
        sess.unsubscribe(queue)


def _tailored_pdf(stamp: str, job: dict) -> str:
    """Best effort: apply can still run without a PDF, uploads just need the user."""
    tex_file = job.get("resume_tex_file") or ""
    if not tex_file:
        return ""
    pdf_path, _ = ensure_pdf(Path(runs.run_dir(stamp)) / tex_file)
    return str(pdf_path) if pdf_path else ""


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _log_stream(stamp: str) -> Iterator[str]:
    """Live events for the active run; a replay of run.log for a finished one."""
    if progress.current_stamp() != stamp:
        try:
            lines = runs.read_log(stamp)
        except runs.RunNotFound as exc:
            yield _sse({"type": "error", "text": str(exc)})
            return
        for line in lines:
            yield _sse({"type": "log", "text": line})
        yield _sse({"type": "done", "text": "replay"})
        return

    queue, backlog = progress.subscribe()
    try:
        for line in backlog:
            yield _sse({"type": "log", "text": line})
        while True:
            try:
                event = queue.get(timeout=HEARTBEAT_SECONDS)
            except Empty:
                yield ": ping\n\n"
                continue
            yield _sse(event)
            if event.get("type") == "done":
                return
    finally:
        progress.unsubscribe(queue)


def _job_or_404(stamp: str, job_id: str) -> dict:
    try:
        return runs.find_job(stamp, job_id)
    except (runs.RunNotFound, runs.JobNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
