"""Where an apply session leaves its traces: dumps/ (gitignored).

    dumps/screenshots/<company>/         pictures sent to the model (shots.py)
    dumps/apply_dumps/<company>/<when>_<title>/
        log.jsonl                        every transcript line, as it happened
        summary.json                     how the session went, written at its end
        dom_<when>/                      each Dump: page, fields, data requests, screenshot

The transcript used to live only in memory and was gone at the next server
restart, so a session could not be read back after the fact (Oct 2026).

OEA_DUMPS_DIR moves the whole tree, which the checks do so a test run never
writes next to your real sessions.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from src.config import ROOT

_ENV_DIR = os.environ.get("OEA_DUMPS_DIR", "").strip()
DUMPS_DIR = Path(_ENV_DIR) if _ENV_DIR else ROOT / "dumps"
SHOTS_DIR = DUMPS_DIR / "screenshots"
APPLY_DIR = DUMPS_DIR / "apply_dumps"


def folder_name(text: str, fallback: str = "unknown-company", limit: int = 60) -> str:
    """Text made safe as one folder name."""
    name = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return name[:limit].strip("-") or fallback


def session_dir(company: str, title: str, base: Path | None = None) -> Path:
    """A new folder for one apply session: apply_dumps/<company>/<when>_<title>.
    Not created here; the session makes it when it first writes."""
    root = (base or APPLY_DIR) / folder_name(company)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{folder_name(title, 'job', 50)}"
    path = root / stem
    n = 2
    while path.exists():
        path = root / f"{stem}_{n}"
        n += 1
    return path
