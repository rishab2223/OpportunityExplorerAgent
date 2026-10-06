from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent

# The job boards a run can scrape, in the order the run settings show them.
SOURCES: dict[str, str] = {"linkedin": "LinkedIn", "indeed": "Indeed"}
# Jobs fetched per source. Every one is scored, and every match is tailored,
# so the top of this range is an hour-long run.
SOURCE_JOBS_MIN = 1
SOURCE_JOBS_MAX = 500

# What the run settings offer. Free text in settings.yaml still works for a
# model not listed here; these are the ones known to work.
MODEL_CHOICES: dict[str, list[dict[str, str]]] = {
    "claude": [
        {"id": "claude-opus-5", "note": "recommended"},
        {"id": "claude-sonnet-5", "note": "faster, lighter on usage limits"},
        {"id": "claude-fable-5", "note": "strongest, heaviest on usage limits"},
    ],
    "openai": [
        {"id": "gpt-5.6-luna", "note": "cheaper, fast"},
        {"id": "gpt-5.6-sol", "note": "stronger, better structured LaTeX"},
        {"id": "gpt-5.6-terra", "note": ""},
    ],
}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


class OpenAIConfig(BaseModel):
    score_model: str = "gpt-5.6-luna"
    enrich_model: str = "gpt-5.6-luna"
    apply_model: str = "gpt-5.6-luna"
    # Parallel LLM calls per step. Scoring is short and cheap; enrichment is
    # long and bursts more tokens, so keep it lower to stay under rate limits.
    score_concurrency: int = Field(default=8, ge=1, le=32)
    enrich_concurrency: int = Field(default=4, ge=1, le=16)


class ClaudeConfig(BaseModel):
    # agent-sdk: bundled Claude Code binary, authenticated by your Claude login
    #            (Max/Pro) or CLAUDE_CODE_OAUTH_TOKEN. No API key.
    # api:       Anthropic Messages API with ANTHROPIC_API_KEY (pay-as-you-go).
    backend: Literal["agent-sdk", "api"] = "agent-sdk"
    enrich_model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Assisted apply: one structured decision per form step, so latency matters.
    apply_model: str = "claude-opus-5"
    apply_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"


class HistoryConfig(BaseModel):
    # Cross-run job history (localData/job_history.db): jobs marked applied are
    # dropped right after scrape on later runs, before they cost anything.
    skip_applied: bool = True
    skip_skipped: bool = False
    # Jobs in either referral state (pending or sent) stay out of later runs
    # until the referral is cleared as failed.
    skip_referral: bool = True
    # Jobs marked closed (no longer accepting applications) never come back.
    skip_closed: bool = True
    # Also match by normalized company+title when the site reissued its job id.
    match_similar: bool = True


class ExperienceConfig(BaseModel):
    # Declares the `experience:` block of settings.yaml, which holds the
    # rule. The candidate's years are a fact about them and live in the
    # profile (total_experience_years), the same value application forms
    # are filled from. Off unless settings.yaml turns it on.
    enabled: bool = False
    tolerance_years: float = Field(default=0, ge=0)
    # drop: removed before scoring, never seen. review: scored anyway, and
    # one scoring review_min_score or more is shown in the Held back tab
    # (at most review_max per run) instead of the shortlist. No default:
    # whether a strong match over the limit should ever reach the candidate
    # is theirs to say, so an enabled filter without a mode is an error.
    mode: Literal["drop", "review"] | None = None
    review_min_score: int | None = Field(default=None, ge=1, le=10)
    review_max: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def _stated(self) -> "ExperienceConfig":
        if self.enabled and self.mode is None:
            raise ValueError("experience.mode must be set in settings.yaml: drop or review")
        if self.mode == "review" and self.review_min_score is None:
            raise ValueError("experience.review_min_score must be set in settings.yaml "
                             "when mode is review (1-10)")
        return self


class ResumeConfig(BaseModel):
    local_path: str = ""


class ApifyActorsConfig(BaseModel):
    # LinkedIn: https://console.apify.com/actors/d1gs0RHIwEnsan7XX
    # Indeed:   https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ
    indeed_actor: str = "BIeK7ZcYUrdxDgOEQ"
    linkedin_actor: str = "d1gs0RHIwEnsan7XX"
    indeed_input: dict[str, Any] = Field(default_factory=dict)
    linkedin_input: dict[str, Any] = Field(default_factory=dict)


class ScrapeConfig(BaseModel):
    keywords: str = "software engineer"
    location: str = ""
    posted_within: Literal["24h", "3d", "7d"] = "3d"
    max_detail_jobs: int = Field(default=20, ge=SOURCE_JOBS_MIN, le=SOURCE_JOBS_MAX)
    # Per-source caps, by source name; a source not named here takes
    # max_detail_jobs. Set from the run settings popup.
    max_jobs: dict[str, int] = Field(default_factory=dict)
    exclude_keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: ["indeed", "linkedin"])
    apify: ApifyActorsConfig = Field(default_factory=ApifyActorsConfig)

    @model_validator(mode="after")
    def _caps_in_range(self) -> "ScrapeConfig":
        for name, cap in self.max_jobs.items():
            if not SOURCE_JOBS_MIN <= int(cap) <= SOURCE_JOBS_MAX:
                raise ValueError(f"{name}: jobs per source must be {SOURCE_JOBS_MIN}-{SOURCE_JOBS_MAX}")
        return self

    def cap(self, source: str) -> int:
        """How many jobs to fetch from one source."""
        return int(self.max_jobs.get(source.lower(), self.max_detail_jobs))


class AppConfig(BaseModel):
    min_score: int = Field(default=7, ge=1, le=10)
    strict_sources: bool = False
    compile_pdf: bool = True
    # Resumes compiled side by side; each compile is its own pdflatex process.
    pdf_concurrency: int = Field(default=6, ge=1, le=16)
    # run: interview prep is written with each tailored resume (about 60% of
    #      what the enrich call writes, so most of its time).
    # on_demand: only when you open a job and ask for it.
    interview_prep: Literal["run", "on_demand"] = "on_demand"
    # A job a previous run already scored or tailored, against the same
    # resume with the same model, takes that run's result instead of a new
    # call (llm_cache in job_history.db, 30 days).
    reuse_enrichment: bool = True
    # Which LLM writes the tailored resume + interview prep. Scoring is always OpenAI.
    enrich_provider: Literal["openai", "claude"] = "openai"
    # Which LLM drives the assisted-apply form filling.
    apply_provider: Literal["openai", "claude"] = "openai"
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    experience: ExperienceConfig = Field(default_factory=ExperienceConfig)
    resume: ResumeConfig = Field(default_factory=ResumeConfig)
    scrape: ScrapeConfig = Field(default_factory=ScrapeConfig)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    apify_token: str = ""


def load_yaml_config(path: Path | None = None) -> AppConfig:
    config_path = path or (ROOT / "config" / "settings.yaml")
    raw: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    return AppConfig.model_validate(raw)


def load_env() -> EnvSettings:
    return EnvSettings()
