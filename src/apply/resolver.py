"""Deterministic form filling: profile and answer bank before any LLM call.

Most application fields are the same every time (name, email, phone, links,
notice period, CTC). resolve() maps a snapshot field onto a value using the
autocomplete attribute, the input name, or a whole-word label pattern - in
that order of trust - then the answer bank's topic match. Only what stays
unresolved is worth a model call.

Deliberately out of scope, for correctness:
- checkboxes, radios and buttons (picking an option in a group needs the
  group's question; that path goes through the bank + ask gate instead)
- fields that already hold a value
- sensitive topics (work authorization, EEO, ...): never guessed from the
  profile map; they resolve only from an explicit answer-bank entry, which is
  created after the user answered once and confirmed.
"""

from __future__ import annotations

import re
from typing import Any

from src import answers
from src.apply import cover_letter, profile

# (profile key, autocomplete values, name pattern, label pattern)
# name/label patterns are whole-word; substring matching is exactly how a
# "Manager email" field would swallow the candidate's email.
_RULES: list[tuple[str, tuple[str, ...], re.Pattern[str], re.Pattern[str]]] = [
    ("full_name", ("name",),
     re.compile(r"^(full[_ ]?name|name|applicant[_ ]?name|candidate[_ ]?name)$"),
     re.compile(r"^(full |your )?name$")),
    # first/last are derived from full_name in resolve(); split fields are
    # everywhere (Synmatch, Greenhouse, ...).
    ("first_name", ("given-name",),
     re.compile(r"^(first[_ ]?name|fname|given[_ ]?name)$"),
     re.compile(r"^(first|given) name$")),
    ("last_name", ("family-name",),
     re.compile(r"^(last[_ ]?name|lname|surname|family[_ ]?name)$"),
     re.compile(r"^(last|family) name$|^surname$")),
    ("email", ("email",),
     re.compile(r"^(e?[-_]?mail|email[_ ]?address)$"),
     re.compile(r"^(work |primary |your )?e ?mail( address)?$")),
    ("phone", ("tel", "tel-national"),
     re.compile(r"^(phone|mobile|phone[_ ]?number|mobile[_ ]?number|contact[_ ]?number)$"),
     re.compile(r"^(mobile|phone|mobile phone)( number)?$|^contact number$")),
    ("location", ("address-level2", "country-name"),
     re.compile(r"^(location|city|current[_ ]?location)$"),
     re.compile(r"^(current |present )?(location|city)( of residence)?$")),
    ("linkedin", (),
     re.compile(r"^linked[_ ]?in([_ ]?(url|profile))?$"),
     re.compile(r"\blinkedin\b")),
    ("github", (),
     re.compile(r"^github([_ ]?(url|profile))?$"),
     re.compile(r"\bgithub\b")),
    ("portfolio", ("url",),
     re.compile(r"^(portfolio|website|personal[_ ]?site)$"),
     re.compile(r"\b(portfolio|personal (web)?site)\b")),
    ("current_company", ("organization",),
     re.compile(r"^(company|current[_ ]?company|employer|organization)$"),
     re.compile(r"\b(current |present )(company|employer)\b")),
    ("current_title", ("organization-title",),
     re.compile(r"^(title|job[_ ]?title|current[_ ]?title|designation)$"),
     re.compile(r"\b(current |present )(title|designation|role)\b")),
    ("total_experience_years", (),
     re.compile(r"^(experience|total[_ ]?experience|years[_ ]?of[_ ]?experience)$"),
     re.compile(r"\b(years? of|total) experience\b")),
    ("notice_period", (),
     re.compile(r"^notice([_ ]?period)?$"),
     re.compile(r"\bnotice period\b")),
    ("current_ctc", (),
     re.compile(r"^(current[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(current|present) (ctc|salary|compensation|pay)\b")),
    ("expected_ctc", (),
     re.compile(r"^(expected[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(expected|desired) (ctc|salary|compensation|pay)\b")),
    ("willing_to_relocate", (),
     re.compile(r"^(relocate|willing[_ ]?to[_ ]?relocate)$"),
     re.compile(r"\bwilling to relocate\b")),
]

_FILLABLE_TYPES = ("", "text", "email", "tel", "url", "number", "search")


def resolve(field: dict[str, Any], job: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """(value, source) for a field the script can fill without the model, else None.

    source is 'profile', 'saved', or 'resume' (file inputs), for the log line.
    """
    tag = field.get("tag") or ""
    field_type = (field.get("type") or "").lower()

    # Cover letters are owned by their own handler (draft, edit, then attach);
    # without this a "Upload cover letter" input would receive the resume.
    if cover_letter.is_cover_letter(field):
        return None
    if field_type == "file":
        # A resume/CV upload; the worker picks which PDF and supplies the path.
        return "", "resume"
    if tag not in ("input", "textarea", "select"):
        return None
    if field_type in ("checkbox", "radio", "submit", "button", "reset", "image", "password"):
        return None
    if (field.get("value") or "").strip():
        return None  # never overwrite what is already there

    label = field.get("label") or ""
    name = (field.get("name") or "").strip().lower()
    autocomplete = (field.get("autocomplete") or "").strip().lower()
    norm_label = profile.fingerprint(label)
    if profile.is_secret(f"{label} {name}"):
        return None

    data = profile.load_profile()
    for key, ac_values, name_re, label_re in _RULES:
        value = str(data.get(key) or "").strip()
        if not value and key in ("first_name", "last_name"):
            parts = str(data.get("full_name") or "").split()
            if key == "first_name" and parts:
                value = parts[0]
            elif key == "last_name" and len(parts) > 1:
                value = " ".join(parts[1:])
        if not value:
            continue
        matched = (
            (autocomplete and autocomplete in ac_values)
            or (name and name_re.fullmatch(name) is not None)
            or (norm_label and label_re.search(norm_label) is not None)
        )
        if not matched:
            continue
        if tag == "select":
            option = match_option(value, field.get("options") or [])
            return (option, "profile") if option else None
        if tag == "input" and field_type not in _FILLABLE_TYPES:
            return None
        return value, "profile"

    # Answer bank: neutral topics only. Sensitive entries are used by the ask
    # gate (with loud logging), never silently by the sweep.
    entry = answers.recall(label, field.get("group") or "")
    if entry and entry["kind"] == "neutral":
        value = entry["answer"]
        if tag == "select":
            option = match_option(value, field.get("options") or [])
            return (option, "saved") if option else None
        if tag == "input" and field_type not in _FILLABLE_TYPES:
            return None
        return value, "saved"
    return None


def match_option(value: str, options: list[str]) -> str:
    """Pick the <select> option for a value in code: exact, case-insensitive,
    then whole-word containment - never a bare substring guess."""
    if not value:
        return ""
    for option in options:
        if option == value:
            return option
    lowered = value.strip().lower()
    for option in options:
        if option.strip().lower() == lowered:
            return option
    pattern = re.compile(rf"\b{re.escape(lowered)}\b")
    hits = [o for o in options if pattern.search(o.lower())]
    return hits[0] if len(hits) == 1 else ""
