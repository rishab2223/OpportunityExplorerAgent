from __future__ import annotations

import traceback

from src import history, progress
from src.agent.nodes.resume import _fail
from src.agent.state import AgentState
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import JobPosting
from src.scrape import scrape_jobs


def _drop_seen(jobs: list[JobPosting], cfg: AppConfig) -> list[JobPosting]:
    """Remove jobs already dealt with in an earlier run.

    Runs right after scrape so a known job costs neither a score nor an enrich
    call. One history query per run; per-job checks are in-memory.
    """
    skip_statuses: set[str] = set()
    if cfg.history.skip_applied:
        skip_statuses.add("applied")
    if cfg.history.skip_skipped:
        skip_statuses.add("skipped")
    if cfg.history.skip_referral:
        skip_statuses.update(history.REFERRAL_STATUSES)
    if cfg.history.skip_closed:
        skip_statuses.add("closed")
    if not skip_statuses:
        return jobs
    by_id, by_fingerprint = history.snapshot(skip_statuses)
    if not by_id and not by_fingerprint:
        return jobs

    kept: list[JobPosting] = []
    dropped: dict[str, int] = {}
    for job in jobs:
        entry = by_id.get(job.job_id)
        status = entry["status"] if entry else ""
        how = "id" if status else ""
        if not status and cfg.history.match_similar:
            entry = by_fingerprint.get(history.fingerprint(job.company, job.title))
            status = entry["status"] if entry else ""
            how = "similar" if status else ""
        if status:
            dropped[status] = dropped.get(status, 0) + 1
            if how == "similar":
                # A fingerprint match can hide a genuinely new posting, so name it.
                progress.log(
                    f"[scrape] Skipping '{job.company} {job.title}': same company "
                    f"and title as a job you marked {status}"
                )
            continue
        kept.append(job)
    if dropped:
        summary = ", ".join(f"{count} {status}" for status, count in sorted(dropped.items()))
        progress.log(
            f"[scrape] Skipped {sum(dropped.values())} previously seen job(s): {summary}"
        )
    return kept


def node_scrape(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    try:
        jobs = scrape_jobs(cfg, env)
        jobs = _drop_seen(jobs, cfg)
        state["raw_jobs"] = [j.model_dump() for j in jobs]
        progress.log(f"[scrape] Collected {len(jobs)} job(s)")
    except StepError as exc:
        return _fail(state, exc)
    except Exception as exc:
        return _fail(
            state,
            StepError(
                "scrape",
                str(exc),
                detail=traceback.format_exc()[-2000:],
                what_happened="Scrape jobs failed. Later steps were not run.",
            ),
        )
    return state
