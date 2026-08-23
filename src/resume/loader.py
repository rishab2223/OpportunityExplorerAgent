from __future__ import annotations

from pathlib import Path

from src.config import ROOT, AppConfig, EnvSettings
from src.errors import StepError
from src.google_io import download_drive_file, parse_drive_file_id
from src.resume.extract import extract_text_from_bytes, extract_text_from_path


def _drive_enabled(cfg: AppConfig) -> bool:
    return bool(cfg.resume.use_google_drive and (cfg.resume.drive_file_id or "").strip())


def _local_enabled(cfg: AppConfig) -> bool:
    return bool((cfg.resume.local_path or "").strip())


def load_resume(cfg: AppConfig, env: EnvSettings) -> tuple[str, str, str]:
    """Return (text, source, source_detail)."""
    if not _drive_enabled(cfg) and not _local_enabled(cfg):
        raise StepError(
            "load_resume",
            "No resume source configured",
            what_happened="No resume source configured. Later steps were not run.",
        )

    primary = cfg.resume.primary
    if primary == "google_drive":
        return _try_drive_then_local(cfg, env)
    return _try_local_then_drive(cfg, env)


def _try_drive(cfg: AppConfig, env: EnvSettings) -> tuple[str, str, str]:
    file_id = parse_drive_file_id(cfg.resume.drive_file_id)
    data, name = download_drive_file(env.google_application_credentials, file_id)
    text = extract_text_from_bytes(data, name).strip()
    if not text:
        raise ValueError("Google Drive resume extracted empty text")
    return text, "google_drive", file_id


def _try_local(cfg: AppConfig) -> tuple[str, str, str]:
    path = Path(cfg.resume.local_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    text = extract_text_from_path(path).strip()
    if not text:
        raise ValueError(f"Local resume extracted empty text: {path}")
    return text, "local", str(path)


def _try_drive_then_local(cfg: AppConfig, env: EnvSettings) -> tuple[str, str, str]:
    if not _drive_enabled(cfg):
        if _local_enabled(cfg):
            return _try_local(cfg)
        raise StepError(
            "load_resume",
            "No resume source configured",
            what_happened="No resume source configured. Later steps were not run.",
        )
    drive_error: Exception | None = None
    try:
        return _try_drive(cfg, env)
    except Exception as exc:
        drive_error = exc
    if _local_enabled(cfg):
        try:
            return _try_local(cfg)
        except Exception as local_exc:
            raise StepError(
                "load_resume",
                str(local_exc),
                detail=f"Drive error: {drive_error}\nLocal error: {local_exc}",
                fallbacks=f"Google Drive failed: {drive_error}; local path failed: {local_exc}",
                what_happened=(
                    "Could not load your resume from Google Drive or the local path. "
                    "Later steps were not run."
                ),
            ) from local_exc
    raise StepError(
        "load_resume",
        str(drive_error),
        detail=str(drive_error),
        fallbacks="Google Drive failed; local path is not set.",
        what_happened=(
            "Unable to read from Google Drive and local path is unavailable. "
            "Later steps were not run."
        ),
    ) from drive_error


def _try_local_then_drive(cfg: AppConfig, env: EnvSettings) -> tuple[str, str, str]:
    if not _local_enabled(cfg):
        if _drive_enabled(cfg):
            return _try_drive_then_local(cfg, env)
        raise StepError(
            "load_resume",
            "No resume source configured",
            what_happened="No resume source configured. Later steps were not run.",
        )
    local_error: Exception | None = None
    try:
        return _try_local(cfg)
    except Exception as exc:
        local_error = exc
    if _drive_enabled(cfg):
        try:
            return _try_drive(cfg, env)
        except Exception as drive_exc:
            raise StepError(
                "load_resume",
                str(drive_exc),
                detail=f"Local error: {local_error}\nDrive error: {drive_exc}",
                fallbacks=f"Local path failed: {local_error}; Google Drive failed: {drive_exc}",
                what_happened=(
                    "Could not load your resume from the local path or Google Drive. "
                    "Later steps were not run."
                ),
            ) from drive_exc
    raise StepError(
        "load_resume",
        str(local_error),
        detail=str(local_error),
        fallbacks="Local path failed; Google Drive is not set.",
        what_happened=(
            "Unable to read from local path and Google Drive is unavailable. "
            "Later steps were not run."
        ),
    ) from local_error
