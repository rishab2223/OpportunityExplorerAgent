"""The run settings popup: what it offers, and how a choice becomes a config.

settings.yaml stays the source of the defaults. A run started from the web
page can override part of it for that run only, and the candidate can keep
their choices as the defaults for the next run started here
(localData/run_preferences.json). Only what differs from settings.yaml is
kept there, so an edit to the yaml still shows through for everything else.
The CLI never reads that file.

Only the keys in ALLOWED can be overridden: an API key, an actor id or a file
outside the project is not something a form should be able to change.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import ValidationError
from pydantic.fields import FieldInfo

from src import atomic
from src.config import (
    EFFORTS,
    MODEL_CHOICES,
    ROOT,
    SOURCE_JOBS_MAX,
    SOURCE_JOBS_MIN,
    SOURCES,
    AppConfig,
    ExperienceConfig,
    OpenAIConfig,
    ScrapeConfig,
    load_yaml_config,
)
from src.web import runs

PREFERENCES_PATH = ROOT / "localData" / "run_preferences.json"

# Dotted paths a run may override. A dict value below one of these (the
# per-source caps) is taken whole.
ALLOWED = frozenset({
    "min_score", "compile_pdf", "pdf_concurrency", "enrich_provider",
    "interview_prep", "reuse_enrichment",
    "history.skip_applied", "history.skip_skipped", "history.skip_referral",
    "history.skip_closed",
    "experience.enabled", "experience.tolerance_years", "experience.mode",
    "experience.review_min_score", "experience.review_max",
    "openai.score_model", "openai.enrich_model", "openai.score_concurrency",
    "openai.enrich_concurrency",
    "claude.enrich_model", "claude.effort",
    "resume.local_path",
    "scrape.sources", "scrape.max_jobs", "scrape.posted_within",
    "scrape.exclude_keywords",
})


class BadOptions(ValueError):
    pass


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if path in ALLOWED:
            out[path] = value
        elif isinstance(value, dict):
            out.update(_flatten(value, path + "."))
        else:
            raise BadOptions(f"'{path}' cannot be set for a run")
    return out


def _set(tree: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    for key in parents:
        tree = tree.setdefault(key, {})
    tree[leaf] = value


def _readable(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err.get("loc") or ()) or "settings"
        parts.append(f"{where}: {err.get('msg')}")
    return "; ".join(parts)


def apply(cfg: AppConfig, overrides: dict[str, Any] | None) -> AppConfig:
    """A new config: `cfg` with the overrides on top, validated as a whole
    (so a cap of 9000 or an unknown effort is refused, not run)."""
    if not overrides:
        return cfg
    if not isinstance(overrides, dict):
        raise BadOptions("run settings must be an object")
    flat = _flatten(overrides)
    merged = copy.deepcopy(cfg.model_dump())
    for path, value in flat.items():
        _set(merged, path, value)
    scrape = merged.get("scrape") or {}
    if "scrape.sources" in flat:
        # Only a choice made here is held to the list: a yaml naming a source
        # this build does not scrape is ignored by the scraper, not refused.
        sources = [str(s).lower() for s in scrape.get("sources") or []]
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            raise BadOptions(f"unknown job source: {', '.join(unknown)}")
        if not sources:
            raise BadOptions("pick at least one job source")
        scrape["sources"] = sources
    if "scrape.max_jobs" in flat:
        caps = scrape.get("max_jobs")
        if not isinstance(caps, dict):
            raise BadOptions("scrape.max_jobs must map each source to a number")
        scrape["max_jobs"] = {str(k).lower(): v for k, v in caps.items() if str(k).lower() in SOURCES}
    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise BadOptions(_readable(exc)) from None


def load_preferences() -> dict[str, Any]:
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _differences(chosen: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The part of `chosen` that is not already what `base` says."""
    out: dict[str, Any] = {}
    for key, value in chosen.items():
        have = base.get(key)
        if isinstance(value, dict) and isinstance(have, dict) and key != "max_jobs":
            inner = _differences(value, have)
            if inner:
                out[key] = inner
        elif value != have:
            out[key] = value
    return out


def save_preferences(overrides: dict[str, Any], base: AppConfig | None = None) -> dict[str, Any]:
    """Keep what differs from settings.yaml as the next run's defaults - only
    once the whole thing validates. Returns what was written ({} clears)."""
    base = base or load_yaml_config()
    apply(base, overrides)
    kept = _differences(overrides, values(base))
    if kept:
        atomic.write_text(PREFERENCES_PATH, json.dumps(kept, indent=2))
    else:
        clear_preferences()
    return kept


def clear_preferences() -> None:
    try:
        PREFERENCES_PATH.unlink()
    except FileNotFoundError:
        pass


def effective_config(base: AppConfig | None = None) -> AppConfig:
    """settings.yaml with the saved preferences on top. A preference file that
    no longer validates (settings.yaml changed under it) is ignored rather
    than blocking every run."""
    base = base or load_yaml_config()
    try:
        return apply(base, load_preferences())
    except BadOptions:
        return base


def values(cfg: AppConfig) -> dict[str, Any]:
    """What the popup shows, as the same nested shape it sends back."""
    return {
        "min_score": cfg.min_score,
        "compile_pdf": cfg.compile_pdf,
        "pdf_concurrency": cfg.pdf_concurrency,
        "enrich_provider": cfg.enrich_provider,
        "interview_prep": cfg.interview_prep,
        "reuse_enrichment": cfg.reuse_enrichment,
        "history": {k: getattr(cfg.history, k) for k in
                    ("skip_applied", "skip_skipped", "skip_referral", "skip_closed")},
        "experience": {
            "enabled": cfg.experience.enabled,
            "tolerance_years": cfg.experience.tolerance_years,
            "mode": cfg.experience.mode,
            "review_min_score": cfg.experience.review_min_score,
            "review_max": cfg.experience.review_max,
        },
        "openai": {k: getattr(cfg.openai, k) for k in
                   ("score_model", "enrich_model", "score_concurrency", "enrich_concurrency")},
        "claude": {"enrich_model": cfg.claude.enrich_model, "effort": cfg.claude.effort},
        "resume": {"local_path": cfg.resume.local_path},
        "scrape": {
            "sources": [s for s in SOURCES if s in [x.lower() for x in cfg.scrape.sources]],
            "max_jobs": {s: cfg.scrape.cap(s) for s in SOURCES},
            "posted_within": cfg.scrape.posted_within,
            "exclude_keywords": list(cfg.scrape.exclude_keywords),
        },
    }


def _bounds(field: FieldInfo) -> list[float]:
    low = high = None
    for item in field.metadata:
        low = getattr(item, "ge", low)
        high = getattr(item, "le", high)
    return [low, high]


def choices() -> dict[str, Any]:
    """Every list and limit the popup offers, read from the config models so
    the form can never drift from what the server accepts."""
    return {
        "sources": [{"id": k, "label": v} for k, v in SOURCES.items()],
        "jobs_min": SOURCE_JOBS_MIN,
        "jobs_max": SOURCE_JOBS_MAX,
        "models": MODEL_CHOICES,
        "efforts": EFFORTS,
        "posted_within": list(ScrapeConfig.model_fields["posted_within"].annotation.__args__),
        "min_score": _bounds(AppConfig.model_fields["min_score"]),
        "score_concurrency": _bounds(OpenAIConfig.model_fields["score_concurrency"]),
        "enrich_concurrency": _bounds(OpenAIConfig.model_fields["enrich_concurrency"]),
        "pdf_concurrency": _bounds(AppConfig.model_fields["pdf_concurrency"]),
        "review_min_score": _bounds(ExperienceConfig.model_fields["review_min_score"]),
        "review_max": _bounds(ExperienceConfig.model_fields["review_max"]),
        "tolerance_years": _bounds(ExperienceConfig.model_fields["tolerance_years"]),
    }


def describe() -> dict[str, Any]:
    """Everything the popup needs: current values, the yaml's own values (for
    Reset), the choices and their limits, and the last run's numbers."""
    base = load_yaml_config()
    saved = load_preferences()
    return {
        "values": values(effective_config(base)),
        "defaults": values(base),
        "saved": bool(saved),
        "choices": choices(),
        "last_run": last_run_summary(),
    }


def summary_line(cfg: AppConfig) -> str:
    """One log line naming what this run will do."""
    caps = ", ".join(f"{SOURCES.get(s, s)} {cfg.scrape.cap(s)}" for s in cfg.scrape.sources)
    model = (cfg.claude.enrich_model + f" ({cfg.claude.effort})"
             if cfg.enrich_provider == "claude" else cfg.openai.enrich_model)
    return (f"[run] settings: {caps} jobs, posted within {cfg.scrape.posted_within}, "
            f"min score {cfg.min_score}, scoring {cfg.openai.score_model} x{cfg.openai.score_concurrency}, "
            f"tailoring {model} x{cfg.openai.enrich_concurrency}, interview prep "
            f"{'during the run' if cfg.interview_prep == 'run' else 'on demand'}"
            f"{', reusing earlier results' if cfg.reuse_enrichment else ''}")


def last_run_summary() -> dict[str, Any]:
    """The newest finished run's counts and stage times, for the popup to set
    expectations ("117 tailored in 46 min")."""
    for stamp in runs.list_stamps()[:5]:
        try:
            data = runs.load_run(stamp)
        except (runs.RunNotFound, OSError, ValueError):
            continue
        if data.get("status") != "ok":
            continue
        return {
            "scraped": data.get("raw_job_count", 0),
            "matched": data.get("match_count", 0),
            "timings": data.get("timings") or {},
        }
    return {}


def config_for_run(stamp: str) -> AppConfig:
    """The settings a web run was started with (run_settings.json), for work
    done on its jobs afterwards - interview prep written against the resume
    that run tailored from, not whatever settings.yaml says today."""
    base = load_yaml_config()
    try:
        saved = json.loads((runs.run_dir(stamp) / "run_settings.json").read_text(encoding="utf-8"))
        return apply(base, saved)
    except (runs.RunNotFound, OSError, ValueError, BadOptions):
        return effective_config(base)
