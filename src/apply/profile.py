from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from src.config import ROOT

PROFILE_PATH = ROOT / "localData" / "apply_profile.json"

# Anything matching these never gets written to disk, however the LLM labels it.
SECRET_HINTS = (
    "otp",
    "one time",
    "one-time",
    "verification code",
    "passcode",
    "password",
    "pin",
    "cvv",
    "captcha",
    "security code",
)

TEMPLATE: dict[str, Any] = {
    "full_name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin": "",
    "github": "",
    "portfolio": "",
    "current_company": "",
    "current_title": "",
    "total_experience_years": "",
    "notice_period": "",
    "current_ctc": "",
    "expected_ctc": "",
    "work_authorization": "",
    "willing_to_relocate": "",
    "preferred_location": "",
    "learned": {},
}

_LOCK = threading.Lock()
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return dict(TEMPLATE)
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(TEMPLATE)
    if not isinstance(data, dict):
        return dict(TEMPLATE)
    data.setdefault("learned", {})
    return data


def ensure_profile_file() -> Path:
    if not PROFILE_PATH.exists():
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write(dict(TEMPLATE))
    return PROFILE_PATH


def fingerprint(label: str) -> str:
    text = _NON_ALNUM.sub(" ", (label or "").lower())
    return _SPACES.sub(" ", text).strip()


def is_secret(label: str) -> bool:
    text = (label or "").lower()
    return any(hint in text for hint in SECRET_HINTS)


def remember(label: str, answer: str) -> bool:
    """Persist a reusable question/answer. Returns False for secrets or blanks."""
    key = fingerprint(label)
    if not key or not answer.strip() or is_secret(label) or is_secret(answer):
        return False
    with _LOCK:
        data = load_profile()
        learned = data.setdefault("learned", {})
        learned[key] = answer.strip()
        _write(data)
    return True


def recall(label: str) -> str:
    return str(load_profile().get("learned", {}).get(fingerprint(label), ""))


def as_prompt_text(profile: dict[str, Any] | None = None) -> str:
    data = profile if profile is not None else load_profile()
    lines = []
    for key, value in data.items():
        if key == "learned" or not value:
            continue
        lines.append(f"{key}: {value}")
    learned = data.get("learned") or {}
    if learned:
        lines.append("previously answered questions:")
        for question, answer in learned.items():
            lines.append(f"  - {question}: {answer}")
    return "\n".join(lines) or "(profile is empty)"


def _write(data: dict[str, Any]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
