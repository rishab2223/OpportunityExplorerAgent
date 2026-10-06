from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from src import llm_cache, progress
from src.agent.parallel import run_pool
from src.agent.filenames import unique_tex_name
from src.agent.state import AgentState
from src.config import ROOT, AppConfig
from src.errors import STEP_LABELS
from src.models import MatchRecord
from src.resume import fit_policy
from src.resume.one_page import TrimResult, fit_to_one_page

OUTPUT_DIR = ROOT / "outputs"
# Inside a run folder: the tailored resumes as written, before the fit
# policy took anything out of them.
FULL_DIR = "full"


def _stamp(run_timestamp: str) -> str:
    raw = run_timestamp or datetime.now(timezone.utc).isoformat()
    return re.sub(r"[^0-9T]", "", raw.replace("+00:00", "Z"))[:15]


def stamp_for(run_timestamp: str) -> str:
    return _stamp(run_timestamp)


def run_dir_for(run_timestamp: str) -> Path:
    return OUTPUT_DIR / _stamp(run_timestamp)


def _compile_pdfs(tex_jobs: list[tuple[MatchRecord, Path, str]], workers: int = 1,
                  enrich_keys: dict[str, str] | None = None) -> int:
    """Best effort: a PDF failure is recorded on the record and never fails the run.

    `tex_jobs` carries each resume's source as written, so a copy can be kept
    when the fit policy shortens it. Side by side: each compile is its own
    pdflatex process writing its own files, and one at a time took ten
    minutes for 117 resumes."""
    total = len(tex_jobs)
    compiled = 0
    trimmed = 0
    workers = max(1, min(workers, total))
    progress.log(f"[pdf] Compiling {total} resume(s) with {workers} worker(s)…")

    # One policy for the whole run, read once: the candidate's (Resume fit
    # tab) or the old fixed order.
    steps = fit_policy.load().steps

    def fit(job: tuple[MatchRecord, Path, str], _tag: str) -> TrimResult:
        # Compiles, measures, and applies the policy only if the PDF runs
        # past one page. A crash here is this resume's failure, not the run's.
        try:
            return fit_to_one_page(job[1], steps=steps)
        except Exception as exc:
            return TrimResult(note=f"could not fit: {str(exc)[:300]}")

    def finished(index: int, result: TrimResult) -> None:
        nonlocal compiled, trimmed
        rec, tex_path, body = tex_jobs[index]
        i = index + 1
        label = f"{rec.company}  {rec.title}".strip() or rec.job_id
        progress.log(f"[pdf] {i}/{total}  {label}")
        if result.cuts:
            trimmed += 1
            progress.log(f"[pdf] {i}/{total}  to fit one page: {'; '.join(result.cuts)}")
            # What the resume was before anything was taken out of it, so
            # the Resume fit tab can show the policy at work on a real overflow.
            full = tex_path.parent / FULL_DIR / tex_path.name
            try:
                full.parent.mkdir(exist_ok=True)
                full.write_text(body, encoding="utf-8")
            except OSError:
                pass
        if result.note:
            progress.log(f"[pdf] {i}/{total}  {result.note}")
        pdf_path = tex_path.with_suffix(".pdf")
        if pdf_path.exists():
            rec.resume_pdf_file = pdf_path.name
            rec.resume_pdf_path = str(pdf_path)
            rec.resume_pages = result.pages
            compiled += 1
            if result.pages > 1:
                rec.resume_pdf_error = result.note or f"{result.pages} pages"
            elif result.pages == 0:
                # The PDF exists but could not be read back: an unreadable
                # file is not a one-page resume, and the row must say so.
                rec.resume_pdf_error = result.note or "could not read the compiled PDF"
        else:
            rec.resume_pdf_error = result.note or "compile produced no PDF"
            progress.log(f"[pdf] {i}/{total}  failed: {rec.resume_pdf_error}")
        if rec.resume_pdf_error and enrich_keys and rec.job_id in enrich_keys:
            # A tailoring that would not fit the page is not worth repeating
            # tomorrow: the next run makes a fresh attempt.
            llm_cache.forget("enrich", enrich_keys[rec.job_id])

    run_pool(tex_jobs, fit, workers, finished)
    progress.log(f"[pdf] Compiled {compiled} of {total}" + (f", trimmed {trimmed}" if trimmed else ""))
    return compiled


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def node_dump(state: AgentState, cfg: AppConfig | None = None) -> AgentState:
    ts = state.get("run_timestamp") or datetime.now(timezone.utc).isoformat()
    stamp = _stamp(ts)
    failed_step = state.get("failed_step") or ""
    failed = bool(failed_step)
    raw_matches = state.get("matches") or []
    run_dir = OUTPUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    state["run_dir"] = str(run_dir)
    compile_pdf = True if cfg is None else bool(cfg.compile_pdf)

    shortlisted_path = ""
    tex_count = 0
    pdf_count = 0
    if not failed:
        now = datetime.now(timezone.utc).isoformat()
        records = []
        used_names: set[str] = set()
        tex_jobs: list[tuple[MatchRecord, Path, str]] = []
        for item in raw_matches:
            rec = MatchRecord.model_validate(item)
            rec.written_at = now
            body = (rec.resume_latex or "").strip()
            rec.resume_latex = ""
            if body:
                name = unique_tex_name(rec.company, rec.title, rec.job_id, used_names)
                tex_path = run_dir / name
                tex_path.write_text(body, encoding="utf-8")
                rec.resume_tex_file = name
                tex_count += 1
                tex_jobs.append((rec, tex_path, body))
            records.append(rec)

        if tex_jobs and compile_pdf:
            pdf_started = time.monotonic()
            pdf_count = _compile_pdfs(tex_jobs, cfg.pdf_concurrency if cfg is not None else 1,
                                      state.get("enrich_keys") or {})
            timings = dict(state.get("timings") or {})
            timings["pdf"] = round(time.monotonic() - pdf_started, 1)
            state["timings"] = timings
        elif tex_jobs:
            for rec, _, _ in tex_jobs:
                rec.resume_pdf_error = "compile_pdf disabled in config"

        records = [rec.model_dump() for rec in records]
        state["matches"] = records
        shortlisted_path = _write_json(run_dir / "shortlisted.json", records)
        _write_json(OUTPUT_DIR / "shortlisted.json", records)
        # Never enriched, so there is no resume to write: Apply from Held
        # back uses the base resume.
        held = [MatchRecord.model_validate({**item, "written_at": now}).model_dump()
                for item in state.get("held_back") or []]
        if held:
            _write_json(run_dir / "held_back.json", held)

    state["resume_tex_count"] = tex_count
    state["resume_pdf_count"] = pdf_count
    run_payload = {
        "status": "failed" if failed else "ok",
        "run_timestamp": ts,
        "run_dir": str(run_dir),
        "failed_step": failed_step,
        "failed_step_label": STEP_LABELS.get(failed_step, failed_step) if failed else "",
        "what_happened": state.get("what_happened") or "",
        "error_message": state.get("error_message") or "",
        "error_detail": state.get("error_detail") or "",
        "fallbacks_tried": state.get("fallbacks_tried") or "",
        "resume_source": state.get("resume_source") or "",
        "resume_source_detail": state.get("resume_source_detail") or "",
        "raw_job_count": len(state.get("raw_jobs") or []),
        "scored_count": len(state.get("scored") or []),
        "match_count": len(state.get("matches") or []),
        "held_back_count": 0 if failed else len(state.get("held_back") or []),
        "resume_tex_count": tex_count,
        "resume_pdf_count": pdf_count,
        "shortlisted_path": shortlisted_path,
        # Seconds per step (scrape, score, enrich, pdf...): where the time went.
        "timings": state.get("timings") or {},
    }
    run_path = _write_json(run_dir / "run.json", run_payload)
    _write_json(OUTPUT_DIR / "run.json", run_payload)

    state["run_output_path"] = run_path
    state["shortlisted_path"] = shortlisted_path
    return state
