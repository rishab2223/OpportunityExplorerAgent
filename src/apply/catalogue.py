"""A growing, per-site record of what application forms look like.

Every apply session reads a page and throws the reading away. Kept, those
readings are worth three things: fixtures for the tests, knowledge of which
widget a named field actually is (so a skills box need not be discovered
again on the second application to the same system), and eventually fewer
model calls on a form whose shape is already known.

Two rules make this safe to keep.

**Shape only.** A snapshot holds what the candidate typed: employers, dates,
salary figures, e-mail address. None of that is stored here. Every `value`
and `text` is dropped and only the structure is written, so the file can be
read, shared or committed without leaking anything.

**A hint, never an answer.** What is stored describes a page as it was, and
pages change. The catalogue may say "this site's skills box is a typeahead";
it may never stand in for reading the live page. Acting on a stale shape is
how you fill the wrong box.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.config import ROOT

CATALOGUE_PATH = ROOT / "localData" / "form_catalogue.json"
MAX_FIELDS_PER_FORM = 300
MAX_FORMS_PER_SITE = 40

# The parts of a snapshot that describe the form rather than the candidate.
SHAPE_KEYS = (
    "tag", "type", "role", "haspopup", "label", "section", "group", "name",
    "required", "multiple", "maxlength", "accept",
)


# Dropping `value` is not enough. A signed-in Workday page renders the
# account's e-mail address as a field LABEL, so the text that describes the
# form can itself be the candidate's data.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# The profile keys worth blanking wherever they turn up. Skills and education
# are left alone: they describe the form's context and are not identifying.
_IDENTITY_KEYS = (
    "full_name", "email", "phone", "address_line1", "postal_code",
    "date_of_birth", "linkedin", "github", "portfolio",
)


def _identity_values() -> list[str]:
    from src.apply import profile as _profile
    try:
        data = _profile.load_profile()
    except Exception:
        return []
    out = []
    for key in _IDENTITY_KEYS:
        text = str(data.get(key) or "").strip()
        if len(text) >= 5:
            out.append(text)
    full = str(data.get("full_name") or "").strip()
    out.extend(part for part in full.split() if len(part) >= 4)
    return sorted(set(out), key=len, reverse=True)


def redact(text: str, identity: list[str] | None = None) -> str:
    """Text with anything identifying taken out. Applied to every label and
    heading, not just to values."""
    out = str(text or "")
    for secret in (identity if identity is not None else _identity_values()):
        out = re.sub(re.escape(secret), "<redacted>", out, flags=re.IGNORECASE)
    out = _EMAIL_RE.sub("<email>", out)
    out = _PHONE_RE.sub("<number>", out)
    return out


def scrub(field: dict[str, Any], identity: list[str] | None = None) -> dict[str, Any]:
    """One field reduced to its shape. `value`, `text`, `checked`, `options`
    and the element ids are all dropped: they are either the candidate's own
    data or particular to one page load. What is kept is redacted as well,
    because a label can hold an e-mail address."""
    out: dict[str, Any] = {}
    for key in SHAPE_KEYS:
        value = field.get(key)
        if value in (None, "", False, 0):
            continue
        out[key] = redact(value, identity) if isinstance(value, str) else value
    return out


def shape_of(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A whole page reduced to shapes, in page order."""
    identity = _identity_values()
    shapes = (scrub(f, identity) for f in fields[:MAX_FIELDS_PER_FORM])
    return [s for s in shapes if s]


def form_key(fields: list[dict[str, Any]]) -> str:
    """Which form on a site this is: its sections and labels, not its data.
    Two visits to the same step produce the same key; the next step of the
    same wizard produces a different one."""
    parts = []
    identity = _identity_values()
    for field in fields[:60]:
        # Redacted like everything else: this string is a key in the stored
        # file, and a signed-in page can put an e-mail address in a label.
        label = redact(str(field.get("label") or field.get("name") or ""), identity).strip().lower()
        section = redact(str(field.get("section") or ""), identity).strip().lower()
        if label or section:
            parts.append(f"{section}|{label}")
    joined = "/".join(sorted(set(parts)))
    return re.sub(r"\s+", " ", joined)[:400] or "(empty)"


def load() -> dict[str, Any]:
    if not CATALOGUE_PATH.exists():
        return {}
    try:
        data = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record(site: str, url: str, fields: list[dict[str, Any]]) -> bool:
    """Keep this page's shape under `site`. False when there was nothing worth
    keeping, or the file could not be written - never raises, because a
    catalogue write must not be able to break an application."""
    if not site or not fields:
        return False
    try:
        data = load()
        forms = data.setdefault(site, {})
        if not isinstance(forms, dict):
            forms = data[site] = {}
        key = form_key(fields)
        entry = forms.get(key)
        seen = int((entry or {}).get("seen") or 0) + 1
        forms[key] = {
            "seen": seen,
            "path": urlparse(url or "").path[:120],
            "fields": shape_of(fields),
        }
        if len(forms) > MAX_FORMS_PER_SITE:
            for stale in sorted(forms, key=lambda k: forms[k].get("seen", 0))[
                    : len(forms) - MAX_FORMS_PER_SITE]:
                forms.pop(stale, None)
        CATALOGUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATALOGUE_PATH.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        return True
    except Exception:
        return False


def widget_hint(site: str, label: str) -> dict[str, Any] | None:
    """What this site's box with that label was last seen to be, or None.

    A hint: the caller still reads the live page and still decides. Used to
    skip a discovery round, never to fill a box sight unseen.
    """
    if not site or not label:
        return None
    wanted = str(label).strip().lower()
    for form in load().get(site, {}).values():
        for field in form.get("fields", []):
            if str(field.get("label") or "").strip().lower() == wanted:
                return field
    return None
