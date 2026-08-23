from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from apify_client import ApifyClient

from src.config import ApifyActorsConfig, EnvSettings, ScrapeConfig
from src.models import JobPosting
from src.scrape.filters import excluded

WORKING_INDEED_INPUT: dict[str, Any] = {
    "country": "IN",
    "fromDays": "3",
    "keyword": (
        'title:("Software Engineer" or "Software Developer" or '
        '"Senior Software Engineer" or "Senior Software Developer" or "NodeJS")'
    ),
    "searchMode": "detailed",
}

WORKING_LINKEDIN_INPUT: dict[str, Any] = {
    "datePosted": "past24Hours",
    "experienceLevels": ["associate", "midSenior", "entryLevel"],
    "jobTypes": ["fullTime", "partTime"],
    "keywords": (
        "software engineer, nodejs, software developer, "
        "senior software engineer, senior software developer, lead engineer"
    ),
    "location": "India",
    "onlyNewJobs": False,
    "onlyWithSalary": False,
    "scrapeCompany": False,
    "scrapeDetails": True,
    "searchUrls": [
        "https://www.linkedin.com/jobs/search/?currentJobId=4457879095&distance=25.0&f_TPR=r86400&geoId=102713980&keywords=software%20engineer%2C%20nodejs&origin=JOBS_HOME_KEYWORD_HISTORY",
    ],
    "sortBy": "recent",
    "splitByCountry": "India",
}

INDEED_FROM_DAYS = {"24h": "1", "3d": "3", "7d": "7"}

COUNTRY_CODES = {
    "india": "IN",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "germany": "DE",
    "canada": "CA",
    "australia": "AU",
    "ireland": "IE",
    "netherlands": "NL",
    "france": "FR",
    "singapore": "SG",
    "uae": "AE",
    "united arab emirates": "AE",
}


def _pick(item: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        cur: Any = item
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok or cur is None or cur == "":
            continue
        if isinstance(cur, (dict, list)):
            continue
        return str(cur).strip()
    return default


def _join_list(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        cur: Any = item
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok or cur is None:
            continue
        if isinstance(cur, list):
            parts = [str(x).strip() for x in cur if x not in (None, "")]
            if parts:
                return ", ".join(parts)
        elif str(cur).strip():
            return str(cur).strip()
    return ""


def _indeed_salary(item: dict[str, Any]) -> str:
    text = _pick(item, "salary.text")
    if text:
        return text
    salary = item.get("salary")
    if not isinstance(salary, dict):
        return ""
    lo = salary.get("min")
    hi = salary.get("max")
    exact = salary.get("exact")
    currency = salary.get("currency") or ""
    period = salary.get("period") or ""
    if exact not in (None, ""):
        amount = str(exact)
    elif lo not in (None, "") or hi not in (None, ""):
        amount = f"{lo or ''} - {hi or ''}".strip(" -")
    else:
        return ""
    return " ".join(p for p in (amount, currency, period) if p).strip()


def _job_id(source: str, *parts: str) -> str:
    raw = "|".join(p for p in parts if p)
    if not raw:
        raw = "unknown"
    return f"{source}:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _country(location: str) -> str:
    loc = (location or "").strip()
    if len(loc) == 2 and loc.isalpha():
        return loc.upper()
    last = loc.split(",")[-1].strip().lower() if loc else ""
    if last in COUNTRY_CODES:
        return COUNTRY_CODES[last]
    if loc.lower() in COUNTRY_CODES:
        return COUNTRY_CODES[loc.lower()]
    return "US"


def _run_field(run: object, camel: str, snake: str) -> Any:
    if run is None:
        return None
    if isinstance(run, dict):
        return run.get(camel) or run.get(snake)
    return getattr(run, snake, None) or getattr(run, camel, None)


def _run_actor(token: str, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
    if not token.strip():
        raise RuntimeError("APIFY_TOKEN is not set")
    if not actor_id.strip():
        raise RuntimeError("Apify actor id is empty")
    client = ApifyClient(token.strip())
    run = client.actor(actor_id.strip()).call(run_input=run_input)
    dataset_id = _run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise RuntimeError(f"Apify actor {actor_id} returned no dataset")
    items = list(client.dataset(str(dataset_id)).iterate_items())
    return [i for i in items if isinstance(i, dict)]


def _collect(
    items: list[dict[str, Any]],
    mapper: Callable[[dict[str, Any]], JobPosting],
    scrape: ScrapeConfig,
) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for item in items:
        posting = mapper(item)
        blob = f"{posting.title} {posting.company} {posting.description}"
        if excluded(blob, scrape.exclude_keywords):
            continue
        jobs.append(posting)
        if len(jobs) >= scrape.max_detail_jobs:
            break
    return jobs


def scrape_indeed_apify(
    scrape: ScrapeConfig,
    apify: ApifyActorsConfig,
    env: EnvSettings,
) -> list[JobPosting]:
    run_input: dict[str, Any] = dict(WORKING_INDEED_INPUT)
    if scrape.location:
        run_input["country"] = _country(scrape.location)
    if scrape.keywords:
        run_input["keyword"] = scrape.keywords
    run_input.update(apify.indeed_input)
    run_input["fromDays"] = INDEED_FROM_DAYS.get(scrape.posted_within, "3")
    run_input["maxItems"] = scrape.max_detail_jobs
    items = _run_actor(env.apify_token, apify.indeed_actor, run_input)
    return _collect(items, _map_indeed, scrape)


def scrape_linkedin_apify(
    scrape: ScrapeConfig,
    apify: ApifyActorsConfig,
    env: EnvSettings,
) -> list[JobPosting]:
    run_input: dict[str, Any] = dict(WORKING_LINKEDIN_INPUT)
    run_input.update(apify.linkedin_input)
    run_input["datePosted"] = apify.linkedin_input.get("datePosted") or "past24Hours"
    run_input["maxResults"] = scrape.max_detail_jobs
    items = _run_actor(env.apify_token, apify.linkedin_actor, run_input)
    return _collect(items, _map_linkedin, scrape)


def _map_indeed(item: dict[str, Any]) -> JobPosting:
    listing = _pick(item, "urls.indeed", "url", "jobUrl", "link")
    apply_url = (
        _pick(item, "apply.url", "urls.apply", "urls.external", "applyUrl") or listing
    )
    job_key = _pick(item, "id", "jobkey", "jk") or listing
    workplace = _pick(
        item,
        "workArrangement.locationType",
        "workArrangement.remoteWorkType",
        "workplaceType",
    )
    arrangement = item.get("workArrangement")
    if not workplace and isinstance(arrangement, dict) and arrangement.get("isRemote"):
        workplace = "remote"
    return JobPosting(
        source="indeed",
        job_id=_job_id("indeed", job_key, listing),
        title=_pick(item, "title.text", "title.normalized", "title", "positionName"),
        company=_pick(item, "company.name", "company.sourceName", "company", "companyName"),
        location=_pick(
            item,
            "location.formatted",
            "location.formattedShort",
            "location.fullAddress",
            "location",
        ),
        workplace_type=workplace,
        listing_url=listing,
        apply_url=apply_url,
        posted_at=_pick(item, "dates.posted", "dates.onIndeed", "postedAt", "date"),
        salary=_indeed_salary(item),
        employment_type=_join_list(item, "classification.jobType", "jobType"),
        description=_pick(item, "description.text", "description", "snippet"),
    )


def _linkedin_salary(item: dict[str, Any]) -> str:
    text = _pick(item, "salary", "salaryText")
    if text:
        return text
    lo = _pick(item, "salaryMin")
    hi = _pick(item, "salaryMax")
    currency = _pick(item, "salaryCurrency")
    period = _pick(item, "salaryPeriod")
    if not lo and not hi:
        return ""
    amount = f"{lo} - {hi}".strip(" -")
    return " ".join(p for p in (amount, currency, period) if p)


def _map_linkedin(item: dict[str, Any]) -> JobPosting:
    listing = _pick(item, "jobUrl", "url", "link", "linkedinUrl")
    apply_url = _pick(item, "applyUrl", "apply_url") or listing
    job_key = _pick(item, "jobId", "id") or listing
    return JobPosting(
        source="linkedin",
        job_id=_job_id("linkedin", job_key, listing),
        title=_pick(item, "title", "jobTitle"),
        company=_pick(item, "companyName", "company", "company.name"),
        location=_pick(item, "location", "formattedLocation"),
        workplace_type=_pick(item, "workplaceType", "workType"),
        listing_url=listing,
        apply_url=apply_url,
        recruiter_name=_pick(item, "recruiterName", "posterName", "jobPoster.name"),
        recruiter_title=_pick(item, "recruiterTitle", "jobPoster.title"),
        recruiter_profile_url=_pick(
            item,
            "recruiterProfileUrl",
            "jobPoster.url",
            "posterUrl",
        ),
        posted_at=_pick(item, "postedAt", "postedTimeAgo", "listedAt", "publishedAt"),
        salary=_linkedin_salary(item),
        employment_type=_pick(item, "employmentType", "jobType"),
        description=_pick(item, "description", "descriptionText", "jobDescription"),
    )
