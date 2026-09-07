from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from queue import Empty
from typing import Any, Iterator

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

# Set by the server's Ctrl+C handler (src.web.__main__). The SSE generators
# below block on their queues; without this they outlive uvicorn's graceful
# window and get cancelled, which prints an "Exception in ASGI application"
# traceback on every stop.
SHUTTING_DOWN = threading.Event()
_STOP = object()

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


def default_resume_pdf(cfg) -> tuple[Path | None, str, str]:
    """The untailored resume as a PDF. Returns (path, error, warning).

    Compiled from the source .tex, or served directly when the configured
    resume is already a PDF. When compiling is impossible (no LaTeX toolchain)
    or fails, an already-compiled PDF next to the .tex is used rather than
    leaving the user with no resume at all - with a warning, since it may
    predate recent edits to the source.
    """
    source = (cfg.resume.local_path or "").strip()
    if not source:
        return None, "resume.local_path is not set in settings.yaml", ""
    path = Path(source)
    if not path.is_absolute():
        from src.config import ROOT

        path = ROOT / path
    if not path.exists():
        return None, f"resume file not found: {path}", ""
    if path.suffix.lower() == ".pdf":
        return path, "", ""
    pdf_path, error = ensure_pdf(path)
    if pdf_path is not None:
        return pdf_path, "", ""
    fallback = path.with_suffix(".pdf")
    if fallback.exists():
        return fallback, "", f"using the previously compiled {fallback.name}: {error}"
    return None, error, ""


@app.get("/api/resume/default.pdf")
def api_default_resume() -> FileResponse:
    cfg = load_yaml_config()
    pdf_path, error, warning = default_resume_pdf(cfg)
    if pdf_path is None:
        raise HTTPException(status_code=422, detail=error)
    headers = {"X-Resume-Warning": warning} if warning else None
    return FileResponse(pdf_path, media_type="application/pdf", headers=headers)


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

    def resume_options() -> dict:
        """Built on first use, when the form actually asks for a resume."""
        tex_file = job.get("resume_tex_file") or ""
        tailored: dict = {"path": "", "error": "this run has no tailored .tex", "changelog": "",
                          "source": "", "pages": job.get("resume_pages") or 0}
        if tex_file:
            tex_path = Path(runs.run_dir(stamp)) / tex_file
            pdf_path, error = ensure_pdf(tex_path)
            tailored = {
                "path": str(pdf_path) if pdf_path else "",
                "error": error,
                "changelog": job.get("resume_edit_suggestions") or "",
                "source": tex_path.read_text(encoding="utf-8") if tex_path.exists() else "",
                "pages": job.get("resume_pages") or 0,
            }
        default_path, default_error, warning = default_resume_pdf(cfg)
        return {
            "tailored": tailored,
            "default": {
                "path": str(default_path) if default_path else "",
                "error": default_error or warning,
            },
        }

    try:
        resume_text, _, _, _ = load_resume(cfg)
    except StepError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    def on_finish(status: str) -> None:
        runs.save_decision(stamp, job_id, status=status)
        if status == "applied":
            history.record(job, "applied", stamp=stamp, note="assisted")
        elif status == "closed":
            # The posting stopped accepting applications; keep it out of
            # future scrapes without pretending anything was submitted.
            history.record(job, "closed", stamp=stamp, note="detected")

    # Mark running before the worker starts: a session that fails within
    # milliseconds would otherwise have its final status overwritten here.
    runs.save_decision(stamp, job_id, decision="yes", status="running")
    try:
        sess = apply_worker.start_apply(
            stamp, job, resume_text, cfg, env,
            on_finish=on_finish,
            resume_options=resume_options,
            out_dir=Path(runs.run_dir(stamp)),
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
    if sess.status != "waiting_for_user":
        # Anything queued while the worker is busy would be consumed as the
        # answer to its NEXT question - typed into a field and possibly saved
        # to the answer bank. Refuse it loudly instead.
        raise HTTPException(
            status_code=409,
            detail="the agent is busy right now - wait for its next question, then answer",
        )
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
        for index, event in enumerate(backlog):
            # A replayed question/choice that was already answered must not
            # reopen a modal or re-arm the chat: a reconnecting page would
            # otherwise send a stale reply as the answer to the LIVE question.
            if event.get("type") in ("question", "choice") and any(
                later.get("type") in ("answer", "done") for later in backlog[index + 1:]
            ):
                event = {**event, "type": "history"}
            yield _sse(event)
            if event.get("type") == "done":
                return
        while True:
            event = _next_event(queue)
            if event is _STOP:
                return
            if event is None:
                yield ": ping\n\n"
                continue
            yield _sse(event)
            if event.get("type") == "done":
                return
    finally:
        sess.unsubscribe(queue)


def _next_event(queue) -> Any:
    """The next queued event, None when it is time for a heartbeat, or _STOP
    once the server is shutting down - checked every second so an open
    stream ends inside uvicorn's graceful window instead of being cancelled."""
    deadline = time.monotonic() + HEARTBEAT_SECONDS
    while True:
        if SHUTTING_DOWN.is_set():
            return _STOP
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return queue.get(timeout=min(1.0, remaining))
        except Empty:
            continue


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
            event = _next_event(queue)
            if event is _STOP:
                return
            if event is None:
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
