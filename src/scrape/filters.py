from __future__ import annotations

def excluded(text: str, keywords: list[str]) -> bool:
    blob = (text or "").lower()
    return any(k.lower() in blob for k in keywords if k)
