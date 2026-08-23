from __future__ import annotations

from src.config import AppConfig, EnvSettings
from src.errors import StepError
from src.models import JobPosting
from src.scrape.apify_jobs import scrape_indeed_apify, scrape_linkedin_apify


def scrape_jobs(cfg: AppConfig, env: EnvSettings) -> list[JobPosting]:
    if not (env.apify_token or "").strip():
        raise StepError(
            "scrape",
            "APIFY_TOKEN is not set",
            what_happened="Scrape jobs failed. Later steps were not run.",
        )

    sources = [s.lower() for s in cfg.scrape.sources]
    collected: list[JobPosting] = []
    errors: dict[str, str] = {}

    if "indeed" in sources:
        try:
            collected.extend(scrape_indeed_apify(cfg.scrape, cfg.scrape.apify, env))
        except Exception as exc:
            errors["indeed"] = str(exc)
            if cfg.strict_sources:
                raise StepError(
                    "scrape",
                    str(exc),
                    detail=str(exc),
                    fallbacks="strict_sources=true",
                    what_happened="Scrape jobs failed (Indeed). Later steps were not run.",
                ) from exc

    if "linkedin" in sources:
        try:
            collected.extend(scrape_linkedin_apify(cfg.scrape, cfg.scrape.apify, env))
        except Exception as exc:
            errors["linkedin"] = str(exc)
            if cfg.strict_sources:
                raise StepError(
                    "scrape",
                    str(exc),
                    detail=str(exc),
                    fallbacks="strict_sources=true",
                    what_happened="Scrape jobs failed (LinkedIn). Later steps were not run.",
                ) from exc

    unique: dict[str, JobPosting] = {}
    for job in collected:
        unique[job.job_id] = job
    jobs = list(unique.values())

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
