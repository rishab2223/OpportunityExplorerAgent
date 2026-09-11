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
import unicodedata
from typing import Any

from src import answers
from src.apply import cover_letter, profile

# Word boundaries plus underscores/hyphens, so "cv_file" and "resume-upload"
# match while "recover" and "cvs" do not.
RESUME_FIELD_RE = re.compile(
    r"(?:^|[^a-z])(resume|cv|curriculum[\s_-]*vitae)(?:[^a-z]|$)", re.IGNORECASE
)


def wants_resume(field: dict[str, Any]) -> bool:
    """A file input that is for a resume/CV: named like one, or unlabelled
    (the common single-upload form). Portfolio/certificate uploads are not."""
    haystack = " ".join(
        str(field.get(k) or "") for k in ("label", "name", "elid", "group", "text")
    )
    return bool(RESUME_FIELD_RE.search(haystack)) or not haystack.strip()

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
     # "Given Name(s)" fingerprints to "given name s" (Workday).
     re.compile(r"^(first|given) name( s)?$")),
    ("last_name", ("family-name",),
     re.compile(r"^(last[_ ]?name|lname|surname|family[_ ]?name)$"),
     re.compile(r"^(last|family) name$|^surname$")),
    ("email", ("email",),
     re.compile(r"^(e?[-_]?mail|email[_ ]?address)$"),
     re.compile(r"^(work |primary |your )?e ?mail( address)?$")),
    ("phone", ("tel", "tel-national"),
     re.compile(r"^(phone|mobile|phone[_ ]?number|mobile[_ ]?number|contact[_ ]?number)$"),
     re.compile(r"^(mobile|phone|mobile phone)( number)?$|^contact number$")),
    # Derived from the location's last part in resolve(). Typing "India" also
    # resolves phone-code widgets ("India (+91)") - the one rule covers both.
    # Before country: "Country Phone Code" (Workday) wants "+91", not "India".
    # The value is derived in resolve() from phone_country_code / the phone.
    ("phone_country_code", (),
     re.compile(r"^(phone[_ ]?country[_ ]?code|country[_ ]?phone[_ ]?code|dial(ing)?[_ ]?code|country[_ ]?code)$"),
     re.compile(r"^(phone |mobile )?country (phone )?code$|^country phone code$|^dial(ing)? code$|^phone code$")),
    ("country", ("country-name", "country"),
     re.compile(r"^(country|country[_ ]?of[_ ]?residence|phone[_ ]?country)$"),
     re.compile(r"^country( of residence)?$|^country ?/ ?region( code)?$")),
    ("location", ("address-level2",),
     re.compile(r"^(location|city|current[_ ]?location)$"),
     # "Location (city)" fingerprints to "location city" (LinkedIn Easy Apply).
     re.compile(r"^(current |present )?(location|city)( city| of residence)?$")),
    ("state", ("address-level1",),
     re.compile(r"^(state|province|region|state[_ ]?province)$"),
     re.compile(r"^state( ?/ ?(province|region|territory))?$|^province$")),
    ("address_line1", ("address-line1", "street-address"),
     re.compile(r"^(address|address[_ ]?line[_ ]?1|street|street[_ ]?address)$"),
     re.compile(r"^(street )?address( line ?1)?$")),
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
# Sections that repeat per resume entry (Workday "My Experience"): the
# profile's location/company/title would be wrong for a past job, so these
# are the model's to fill from the resume.
REPEATING_SECTION_RE = re.compile(
    r"\b(work|professional|employment)\s+(experience|history)\b|\beducation\b"
    r"|\blanguages?\b|\bcertifications?\b|\bprevious employment\b"
    r"|\bwebsites?\b|\bsocial (network|media) (urls?|links?)\b|\bonline profiles?\b"
    r"|\bportfolio\b|\bweb ?links?\b",
    re.IGNORECASE,
)
# Sections the PROFILE can fill entry by entry, no model needed: the k-th
# "Language" select gets the k-th profile language, the k-th "URL" the k-th
# link. (Work Experience and Education come from the resume via the model.)
LANGUAGES_SECTION_RE = re.compile(r"\blanguages?\b", re.IGNORECASE)
WEBSITES_SECTION_RE = re.compile(
    r"\bwebsites?\b|\bsocial (network|media) (urls?|links?)\b|\bonline profiles?\b"
    r"|\bportfolio\b|\bweb ?links?\b",   # Workday: "Portfolio (Optional) - Add any relevant websites"
    re.IGNORECASE,
)
_LEVEL_LABEL_RE = re.compile(r"\b(overall|proficiency|level|fluency|reading|writing|speaking)\b", re.IGNORECASE)
_URL_LABEL_RE = re.compile(r"\b(url|website|link|address)\b", re.IGNORECASE)
_NAMED_SITE_RE = re.compile(r"\b(linkedin|github|twitter|x\.com|facebook|instagram|stack ?overflow)\b", re.IGNORECASE)


def named_link_field(field: dict[str, Any]) -> bool:
    """A box that names the site it wants ("Please enter your LinkedIn URL").
    Workday shows it under "Social Network URLs", a heading the Websites rule
    matches - but it is that site's box, not the k-th entry of a list."""
    label = str(field.get("label") or "")
    return bool(_URL_LABEL_RE.search(label) and _NAMED_SITE_RE.search(label))


def in_repeating_section(field: dict[str, Any]) -> bool:
    if named_link_field(field):
        return False
    return bool(REPEATING_SECTION_RE.search(str(field.get("section") or "")))


def profile_languages(data: dict[str, Any]) -> list[tuple[str, str]]:
    """'English - Intermediate; Hindi - Fluent' -> [("English", "Intermediate"),
    ("Hindi", "Fluent")]. Accepts ':', '(' and ',' as separators too."""
    out = []
    for part in re.split(r"[;,\n]+", str(data.get("languages") or "")):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^\s*([^-:(]+?)\s*(?:[-:(]\s*([^)]+?)\s*\)?)?\s*$", part)
        if m:
            out.append((m.group(1).strip(), (m.group(2) or "").strip()))
    return out


def profile_links(data: dict[str, Any]) -> list[str]:
    return [str(data.get(k) or "").strip() for k in ("linkedin", "github", "portfolio") if str(data.get(k) or "").strip()]


def entry_value(field: dict[str, Any], data: dict[str, Any]) -> str | None:
    """The profile value for the k-th field of its kind inside a Languages or
    Websites section (k = field['ordinal'], set by the sweep in page order),
    or None when this is not such a field."""
    section = str(field.get("section") or "")
    label = str(field.get("label") or "")
    ordinal = int(field.get("ordinal") or 0)
    if LANGUAGES_SECTION_RE.search(section):
        langs = profile_languages(data)
        if ordinal >= len(langs):
            return None
        name, level = langs[ordinal]
        if re.match(r"^\s*languages?\b", label, re.IGNORECASE):
            return name
        if _LEVEL_LABEL_RE.search(label):
            return level or None
        return None
    if WEBSITES_SECTION_RE.search(section) and generic_url_field(field):
        # A link the page already asks for by name (its own "LinkedIn URL"
        # box) is not repeated under Websites/Portfolio: GitHub goes there.
        taken = {t.strip().lower().rstrip("/") for t in (field.get("taken_links") or [])}
        links = [l for l in profile_links(data) if l.strip().lower().rstrip("/") not in taken]
        return links[ordinal] if ordinal < len(links) else None
    return None


def generic_url_field(field: dict[str, Any]) -> bool:
    """A Websites/Portfolio entry's URL box - not a box that names one site
    ("Please enter your LinkedIn URL")."""
    label = str(field.get("label") or "")
    return bool(_URL_LABEL_RE.search(label)) and not named_link_field(field)


_DIAL_CODE_RE = re.compile(r"\+\d{1,3}\b")
# A dropdown showing "Select an option" holds nothing; treating that as a
# value left LinkedIn's Country select untouched.
_PLACEHOLDER_RE = re.compile(
    r"^\W*(select|choose|pick|please select|please choose|none selected|-+)\b|^\W*$",
    re.IGNORECASE,
)


def is_listbox_button(field: dict[str, Any]) -> bool:
    """A dropdown rendered as a BUTTON that opens a listbox (Workday). Its
    shown choice is its text, not a value attribute."""
    return field.get("tag") == "button" and (field.get("haspopup") or "").lower() == "listbox"


def is_blank(field: dict[str, Any]) -> bool:
    """No real value yet: empty, or a dropdown still on its placeholder."""
    if is_listbox_button(field):
        shown = (field.get("text") or "").strip()
        return not shown or bool(_PLACEHOLDER_RE.match(shown))
    value = (field.get("value") or "").strip()
    if not value:
        return True
    if field.get("tag") == "select":
        options = field.get("options") or []
        if _PLACEHOLDER_RE.match(value) and (not options or value == options[0]):
            return True
    return False


def _country(data: dict[str, Any]) -> str:
    explicit = str(data.get("country") or "").strip()
    if explicit:
        return explicit
    location = str(data.get("location") or "")
    return location.split(",")[-1].strip() if "," in location else ""


def _dial_code(data: dict[str, Any]) -> str:
    """'+91' from an explicit phone_country_code, else the phone's own prefix."""
    explicit = str(data.get("phone_country_code") or "").strip()
    if explicit:
        return explicit if explicit.startswith("+") else "+" + explicit
    match = re.match(r"\s*\+(\d{1,3})", str(data.get("phone") or ""))
    return f"+{match.group(1)}" if match else ""


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
        # Only a resume/CV upload gets the resume; a portfolio or certificate
        # input is left for the model to ask about (it used to receive the
        # resume PDF silently).
        return ("", "resume") if wants_resume(field) else None
    listbox = is_listbox_button(field)
    if tag not in ("input", "textarea", "select") and not listbox:
        return None
    if in_repeating_section(field):
        # Languages and Websites entries come from the profile in order; a
        # job entry's Location is that job's, not the profile's (model).
        if not is_blank(field):
            return None
        value = entry_value(field, profile.load_profile())
        if not value:
            return None
        if tag == "select":
            option = match_option(value, field.get("options") or [])
            return (option, "profile") if option else None
        if listbox or tag in ("input", "textarea"):
            return value, "profile"
        return None
    # A dropdown button is type="button" - it must not fall to this guard.
    if not listbox and field_type in ("checkbox", "radio", "submit", "button", "reset", "image", "password"):
        return None

    data = profile.load_profile()
    options = field.get("options") or []
    # Phone-code selects default to SOME country (+246 was seen); a wrong
    # default is the one existing value that must be overridden.
    if tag == "select" and any(_DIAL_CODE_RE.search(o) for o in options[:30]):
        code = _dial_code(data)
        if not code:
            return None
        if code in (field.get("value") or ""):
            return None  # already right
        option = match_option(code, options) or next((o for o in options if code in o), "")
        return (option, "profile") if option else None
    if not is_blank(field):
        return None  # never overwrite what is already there

    label = field.get("label") or ""
    name = (field.get("name") or "").strip().lower()
    autocomplete = (field.get("autocomplete") or "").strip().lower()
    norm_label = profile.fingerprint(label)
    if profile.is_secret(f"{label} {name}"):
        return None

    for key, ac_values, name_re, label_re in _RULES:
        value = str(data.get(key) or "").strip()
        if not value and key in ("first_name", "last_name"):
            parts = str(data.get("full_name") or "").split()
            if key == "first_name" and parts:
                value = parts[0]
            elif key == "last_name" and len(parts) > 1:
                value = " ".join(parts[1:])
        if not value and key == "country":
            value = _country(data)
        if key == "phone_country_code":
            value = _dial_code(data)
        if not value:
            continue
        if key == "location" and "," in value and re.search(r"\bcity\b", norm_label):
            value = value.split(",")[0].strip()  # "Gurgaon, India" -> City: Gurgaon
        matched = (
            (autocomplete and autocomplete in ac_values)
            or (name and name_re.fullmatch(name) is not None)
            or (norm_label and label_re.search(norm_label) is not None)
        )
        if not matched:
            continue
        if tag == "select":
            option = match_option(value, field.get("options") or [])
            if not option and key == "phone_country_code":
                option = next((o for o in field.get("options") or [] if value in o), "")
            return (option, "profile") if option else None
        if listbox:
            # Options are unknown until the list opens; the worker matches
            # the value against them then (and refuses when nothing fits).
            return value, "profile"
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
        if listbox:
            return value, "saved"
        if tag == "input" and field_type not in _FILLABLE_TYPES:
            return None
        return value, "saved"
    return None


# Renamed cities: a form's list may carry either name, and searching one
# never shows the other ("Gurgaon" found only Gurgaon in Bihar; the Haryana
# city is listed as Gurugram).
_CITY_ALIASES = {
    "gurgaon": "gurugram", "bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai",
    "calcutta": "kolkata", "poona": "pune", "trivandrum": "thiruvananthapuram",
    "cochin": "kochi", "mysore": "mysuru", "baroda": "vadodara", "allahabad": "prayagraj",
    "belgaum": "belagavi", "mangalore": "mangaluru", "simla": "shimla", "cawnpore": "kanpur",
}
_CITY_ALIASES.update({v: k for k, v in list(_CITY_ALIASES.items())})


def city_aliases(value: str) -> list[str]:
    """Other spellings of the city that starts `value` ("Gurgaon, India" ->
    ["Gurugram, India"]); empty for anything not in the table."""
    text = (value or "").strip()
    if not text:
        return []
    head = re.split(r"[,\s]", text, 1)[0]
    alias = _CITY_ALIASES.get(head.lower())
    if not alias:
        return []
    return [alias.title() + text[len(head):]]


def plain(text: str) -> str:
    """Lower-case, accents stripped: Workday lists "Haryāna" and "Bihār";
    nobody types the macron."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


def match_option(value: str, options: list[str]) -> str:
    """Pick the <select> option for a value in code: exact, case- and
    accent-insensitive, then whole-word containment - never a bare substring
    guess."""
    if not value:
        return ""
    for option in options:
        if option == value:
            return option
    lowered = plain(value)
    for option in options:
        if plain(option) == lowered:
            return option
    pattern = re.compile(rf"\b{re.escape(lowered)}\b")
    hits = [o for o in options if pattern.search(plain(o))]
    return hits[0] if len(hits) == 1 else ""
