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


class ResumeConfig(BaseModel):
    primary: Literal["google_drive", "local"] = "local"
    use_google_drive: bool = False
    drive_file_id: str = ""
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
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    resume: ResumeConfig = Field(default_factory=ResumeConfig)
    scrape: ScrapeConfig = Field(default_factory=ScrapeConfig)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    google_application_credentials: str = ""
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
