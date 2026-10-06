from __future__ import annotations

import traceback

from src import experience, history, progress
from src.agent.nodes.resume import _fail
from src.apply import profile
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
    # A fingerprint match can hide a genuinely new posting, so each one is
    # named - once: Accenture's one role posted in eleven cities filled the
    # log with eleven identical lines (Oct 2026).
    similar: dict[tuple[str, str, str], int] = {}
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
                key = (job.company, job.title, status)
                similar[key] = similar.get(key, 0) + 1
            continue
        kept.append(job)
    for (company, title, status), count in similar.items():
        progress.log(
            f"[scrape] Skipping '{company} {title}'"
            + (f" ({count} postings)" if count > 1 else "")
            + f": same company and title as a job you marked {status}"
        )
    if dropped:
        summary = ", ".join(f"{count} {status}" for status, count in sorted(dropped.items()))
        progress.log(
            f"[scrape] Skipped {sum(dropped.values())} previously seen job(s): {summary}"
        )
    return kept


def _drop_over_experienced(jobs: list[JobPosting], cfg: AppConfig) -> list[JobPosting]:
    """Remove, or in review mode mark, jobs asking for more years than
    settings.yaml allows.

    Esko's "Software Engineer Specialist" asked for 8+ years and reached the
    shortlist at relevance 7, the gap listed and outscored. In drop mode the
    job goes before scoring, so it costs no call. In review mode it is kept
    and marked `held_back`: it is scored like any other, and node_filter
    decides whether it is strong enough to show in Held back. Off unless
    `experience.enabled`.
    """
    rule = cfg.experience
    if not rule.enabled:
        return jobs
    have = experience.candidate_years(profile.load_profile().get("total_experience_years"))
    if have is None:
        progress.log("[scrape] experience filter is on but total_experience_years is "
                     "empty in your profile - not filtering")
        return jobs
    limit = have + rule.tolerance_years
    kept: list[JobPosting] = []
    over = 0
    for job in jobs:
        wanted = experience.required_years(job.description or "")
        if wanted is None or wanted <= limit:
            kept.append(job)
            continue
        over += 1
        reason = f"asks for {wanted:g}+ years; your limit is {limit:g}"
        if rule.mode == "review":
            kept.append(job.model_copy(update={"held_back": reason}))
            continue
        progress.log(f"[scrape] Skipping '{job.company} {job.title}': {reason}")
    if over and rule.mode == "review":
        progress.log(f"[scrape] {over} job(s) over the experience limit: scored anyway, and "
                     f"shown in Held back if they score {rule.review_min_score} or more")
    elif over:
        progress.log(f"[scrape] Skipped {over} job(s) over the experience limit")
    return kept


def node_scrape(state: AgentState, cfg: AppConfig, env: EnvSettings) -> AgentState:
    try:
        jobs = scrape_jobs(cfg, env)
        jobs = _drop_seen(jobs, cfg)
        jobs = _drop_over_experienced(jobs, cfg)
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
