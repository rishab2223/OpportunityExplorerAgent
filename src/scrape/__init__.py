from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Callable

from src import progress
from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import JobPosting
from src.scrape.apify_jobs import scrape_indeed_apify, scrape_linkedin_apify

LOOKBACK_DAYS = {"24h": 1, "3d": 3, "7d": 7}


def scrape_jobs(cfg: AppConfig, env: EnvSettings) -> list[JobPosting]:
    if not (env.apify_token or "").strip():
        raise StepError(
            "scrape",
            "APIFY_TOKEN is not set",
            what_happened="Scrape jobs failed. Later steps were not run.",
        )

    sources = [s.lower() for s in cfg.scrape.sources]
    runners: dict[str, Callable[[], list[JobPosting]]] = {}
    if "indeed" in sources:
        runners["indeed"] = lambda: scrape_indeed_apify(cfg.scrape, cfg.scrape.apify, env)
    if "linkedin" in sources:
        runners["linkedin"] = lambda: scrape_linkedin_apify(cfg.scrape, cfg.scrape.apify, env)

    collected: list[JobPosting] = []
    errors: dict[str, str] = {}
    # Each source is an independent Apify actor run, so run them side by side.
    with ThreadPoolExecutor(max_workers=max(1, len(runners)), thread_name_prefix="scrape") as pool:
        futures: dict[str, Future] = {name: pool.submit(fn) for name, fn in runners.items()}
        for name, future in futures.items():
            try:
                jobs = future.result()
                progress.log(f"[scrape] {name}: {len(jobs)} job(s)")
                collected.extend(jobs)
            except Exception as exc:
                errors[name] = str(exc)
                progress.log(f"[scrape] {name} failed: {exc}")
                if cfg.strict_sources:
                    raise StepError(
                        "scrape",
                        str(exc),
                        detail=str(exc),
                        fallbacks="strict_sources=true",
                        what_happened=(
                            f"Scrape jobs failed ({name.title()}). Later steps were not run."
                        ),
                    ) from exc

    unique: dict[str, JobPosting] = {}
    for job in collected:
        unique[job.job_id] = job
    jobs = list(unique.values())

    days = LOOKBACK_DAYS.get(cfg.scrape.posted_within)
    if days:
        today = datetime.now(timezone.utc).date()
        fresh = [j for j in jobs if _within_lookback(j.posted_at, days, today)]
        if len(fresh) != len(jobs):
            progress.log(
                f"[scrape] Dropped {len(jobs) - len(fresh)} job(s) posted more than "
                f"{days} day(s) ago"
            )
        jobs = fresh

    if not jobs and errors:
        joined = "; ".join(f"{k}: {v}" for k, v in errors.items())
        raise StepError(
            "scrape",
            joined,
            detail=joined,
            fallbacks=joined,
            what_happened=(
                "Scrape jobs failed: zero jobs after source errors. Later steps were not run."
            ),
        )
    return jobs


def _within_lookback(posted_at: str, days: int, today: date) -> bool:
    """Keep a job unless its posted date is parseable and older than the lookback."""
    posted = _parse_date(posted_at)
    if posted is None:
        return True
    return (today - posted).days <= days


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
