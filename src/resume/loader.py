from __future__ import annotations

from pathlib import Path

from src.config import ROOT, AppConfig
from src.errors import StepError
from src.resume.extract import extract_text_from_path, latex_to_plain_text


def load_resume(cfg: AppConfig) -> tuple[str, str, str, str]:
    """Return (plain_text, source, source_detail, latex_source)."""
    raw = (cfg.resume.local_path or "").strip()
    if not raw:
        raise StepError(
            "load_resume",
            "No resume source configured",
            what_happened="No resume source configured. Later steps were not run.",
        )
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    try:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.suffix.lower() == ".tex":
            latex = path.read_text(encoding="utf-8")
            text = latex_to_plain_text(latex).strip()
            if not text:
                raise ValueError(f"Local resume extracted empty text: {path}")
            return text, "local", str(path), latex
        text = extract_text_from_path(path).strip()
        if not text:
            raise ValueError(f"Local resume extracted empty text: {path}")
        return text, "local", str(path), ""
    except StepError:
        raise
    except Exception as exc:
        raise StepError(
            "load_resume",
            str(exc),
            detail=str(exc),
            what_happened=(
                "Unable to read the local resume file. Later steps were not run."
            ),
        ) from exc
