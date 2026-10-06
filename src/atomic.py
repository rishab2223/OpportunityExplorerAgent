"""Write a small file so a reader never sees half of it.

The fit policy, the fit draft and the run preferences are each read by one
request while another writes them; a plain write_text can be caught
mid-way and read back as "no file", which for the policy meant a run fell
back to the default rule and for the draft meant a wiped conversation.
"""
from __future__ import annotations

import os
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
