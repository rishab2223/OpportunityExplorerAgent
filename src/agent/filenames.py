from __future__ import annotations

import re

_ILLEGAL = re.compile(r'[\\/:*?"<>|]+')
_SPACE = re.compile(r"\s+")
_UNDERSCORES = re.compile(r"_+")


def sanitize_filename_part(value: str) -> str:
    text = _ILLEGAL.sub("_", (value or "").strip())
    text = _SPACE.sub("_", text)
    text = _UNDERSCORES.sub("_", text).strip("._")
    return text or "unknown"


def unique_tex_name(company: str, title: str, job_id: str, used: set[str]) -> str:
    base = f"{sanitize_filename_part(company)}_{sanitize_filename_part(title)}"
    name = f"{base}.tex"
    if name not in used:
        used.add(name)
        return name
    suffix = sanitize_filename_part(job_id)
    name = f"{base}_{suffix}.tex"
    if name not in used:
        used.add(name)
        return name
    n = 2
    while True:
        name = f"{base}_{suffix}_{n}.tex"
        if name not in used:
            used.add(name)
            return name
        n += 1
