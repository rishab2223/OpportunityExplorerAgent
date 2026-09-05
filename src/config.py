from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


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
    max_detail_jobs: int = 20
    exclude_keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: ["indeed", "linkedin"])
    apify: ApifyActorsConfig = Field(default_factory=ApifyActorsConfig)


class AppConfig(BaseModel):
    min_score: int = 7
    strict_sources: bool = False
    compile_pdf: bool = True
    # Which LLM writes the tailored resume + interview prep. Scoring is always OpenAI.
    enrich_provider: Literal["openai", "claude"] = "openai"
    # Which LLM drives the assisted-apply form filling.
    apply_provider: Literal["openai", "claude"] = "openai"
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
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
