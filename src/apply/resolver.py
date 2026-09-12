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


# What a document upload accepts. A slot that takes only images is a photo
# box (ALTEN has one next to the resume drop zone), never the resume.
_DOC_ACCEPT_RE = re.compile(r"pdf|\.docx?|msword|wordprocessing|officedocument|\.rtf|\.txt|\.odt",
                            re.IGNORECASE)


def wants_resume(field: dict[str, Any]) -> bool:
    """A file input that is for a resume/CV: named like one, or unlabelled
    (the common single-upload form). Portfolio/certificate uploads are not."""
    accept = str(field.get("accept") or "").strip()
    if accept and not _DOC_ACCEPT_RE.search(accept):
        return False
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
    # An explicit city wins over the one split out of "location".
    ("city", ("address-level2",),
     re.compile(r"^(city|town|current[_ ]?city)$"),
     re.compile(r"^(current |present )?(city|town)$")),
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
    # Salary boxes come in threes on some forms (ALTEN): currency, period,
    # amount. These two must be read BEFORE the amount rules below, or
    # "Expected Salary Currency" resolves to the expected pay itself.
    ("salary_currency", (),
     re.compile(r"^(currency|currencycode\d*|(current|expected)[_ ]?salary[_ ]?currency)$"),
     re.compile(r"\b(salary )?currency\b")),
    ("salary_period", (),
     re.compile(r"^((current|expected)[_ ]?salary[_ ]?period|salary[_ ]?period|pay[_ ]?period)$"),
     re.compile(r"\b(salary|pay) period\b|\bper (annum|month)\b")),
    ("current_ctc", (),
     re.compile(r"^(current[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(current|present) (ctc|salary|compensation|pay)\b")),
    ("expected_ctc", (),
     re.compile(r"^(expected[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(expected|desired) (ctc|salary|compensation|pay)\b")),
    ("willing_to_relocate", (),
     re.compile(r"^(relocate|willing[_ ]?to[_ ]?relocate)$"),
     re.compile(r"\bwilling to relocate\b")),
    ("postal_code", ("postal-code",),
     re.compile(r"^(zip|zipcode|postal[_ ]?code|pin[_ ]?code|postcode)$"),
     re.compile(r"^(zip|postal|pin)( ?/ ?postal)?( code)?$|^postcode$")),
    ("date_of_birth", ("bday",),
     re.compile(r"^(dob|date[_ ]?of[_ ]?birth|birth[_ ]?date|birthdate)$"),
     re.compile(r"\b(date of birth|birth date)\b|^dob$")),
    ("willing_to_travel", (),
     re.compile(r"^(travel|willing[_ ]?to[_ ]?travel)$"),
     re.compile(r"\bwilling to travel\b|\bopen to travel\b")),
    ("preferred_location", (),
     re.compile(r"^(preferred[_ ]?location|location[_ ]?preference|preferred[_ ]?work[_ ]?location)$"),
     re.compile(r"\bpreferred (work )?location\b|\blocation preference\b|\bpreferred city\b")),
    ("earliest_start_date", (),
     re.compile(r"^(start[_ ]?date|available[_ ]?from|availability|earliest[_ ]?start[_ ]?date|joining[_ ]?date)$"),
     re.compile(r"\b(earliest )?(start|joining) date\b|\bavailable (from|to start)\b"
                r"|\bwhen can you (join|start)\b|\bdate of joining\b")),
    ("how_did_you_hear", (),
     re.compile(r"^(source|referral[_ ]?source|how[_ ]?did[_ ]?you[_ ]?hear)$"),
     re.compile(r"\bhow did you (hear|find|learn)\b|\bsource of (application|referral)\b")),
    # Relevant experience must be read BEFORE total experience, or "years of
    # relevant experience" resolves to the whole career.
    ("relevant_experience_years", (),
     re.compile(r"^(relevant[_ ]?experience|years[_ ]?of[_ ]?relevant[_ ]?experience)$"),
     re.compile(r"\brelevant experience\b")),
    # Education, split out of the one "education" line: a 345-entry "Field of
    # study" dropdown and a degree list are answered from the profile instead
    # of the model guessing "Computer Science", which is not an option.
    ("university", (),
     re.compile(r"^(university|college|school|institute|institution)$"),
     re.compile(r"^(name of )?(university|college|school|institute|institution)$"
                r"|\b(university|college) name\b")),
    ("highest_education_level", (),
     re.compile(r"^(degree|qualification|education[_ ]?level|highest[_ ]?qualification)$"),
     re.compile(r"\b(highest )?(degree|qualification)\b|\beducation level\b"
                r"|\blevel of education\b")),
    ("field_of_study", (),
     re.compile(r"^(field[_ ]?of[_ ]?study|major|specialization|specialisation|discipline)$"),
     re.compile(r"\bfield of study\b|\bmajor\b|\bspeciali[sz]ation\b|\bdiscipline\b")),
    ("graduation_year", (),
     re.compile(r"^(graduation[_ ]?year|year[_ ]?of[_ ]?passing|passing[_ ]?year)$"),
     re.compile(r"\b(graduation|passing|completion) year\b|\byear of (graduation|passing)\b")),
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
    # A work entry's date boxes carry no work section of their own; the
    # page-order pass is what says which entry they belong to.
    if field.get("work_entry") is not None:
        return True
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


EDUCATION_SECTION_RE = re.compile(r"\beducation\b|\bacademics?\b", re.IGNORECASE)
_SCHOOL_LABEL_RE = re.compile(r"\b(school|university|college|institut\w*)\b", re.IGNORECASE)
_DEGREE_LABEL_RE = re.compile(r"\b(degree|qualification)\b", re.IGNORECASE)
_STUDY_LABEL_RE = re.compile(
    r"\b(field of study|major|discipline|stream|branch|speciali[sz]ation|course)\b", re.IGNORECASE)
_FROM_LABEL_RE = re.compile(r"\b(from|start|joined)\b", re.IGNORECASE)
_TO_LABEL_RE = re.compile(r"\b(to|end|graduat\w*|completion)\b", re.IGNORECASE)


def profile_education(data: dict[str, Any]) -> list[dict[str, str]]:
    """The profile's education line(s) split into their parts:
    'NorthCap University - Bachelors, Computer and Information Science,
    2015-2019' -> school, degree, field, start, end. Several entries are
    separated by ';'."""
    out: list[dict[str, str]] = []
    for part in re.split(r"[;\n]+", str(data.get("education") or "")):
        part = part.strip()
        if not part:
            continue
        school, _, rest = part.partition(" - ")
        entry: dict[str, str] = {"school": school.strip()}
        words: list[str] = []
        for bit in (b.strip() for b in rest.split(",")):
            if not bit:
                continue
            years = re.findall(r"(?:19|20)\d{2}", bit)
            if years and not re.search(r"[a-z]{3}", bit, re.IGNORECASE):
                entry["start"] = years[0]
                entry["end"] = years[-1]
            else:
                words.append(bit)
        if words:
            entry["degree"] = words[0]
        if len(words) > 1:
            entry["field"] = words[1]
        out.append(entry)
    return out


WORK_SECTION_RE = re.compile(
    r"\b(work|professional|employment)\s+(experience|history)\b|\bprevious employment\b",
    re.IGNORECASE)
# A date group's own label read as a heading. Workday puts an entry's Month
# and Year boxes under a section called "From*"/"To*", and Esko gives its
# From*/To* boxes no section at all, so nothing there names the entry they
# belong to. Anchored on purpose: "Reason to leave" must not read as a To.
DATE_GROUP_RE = re.compile(
    r"^\s*(from|to|start|end|start date|end date|date from|date to)\s*\*?\s*:?\s*$",
    re.IGNORECASE)
_JOB_TITLE_RE = re.compile(r"\b(job )?title\b|\bposition\b|\brole\b|\bdesignation\b", re.IGNORECASE)
# What starts a NEW entry on an unnumbered list. Deliberately tighter than
# _JOB_TITLE_RE, which matches the word "role": Esko labels its description
# box "Role description", and treating that as a title split every entry in
# two and left the second job with nothing to fill it from.
_ENTRY_START_RE = re.compile(
    r"\b(job ?title|position title|designation)\b|^\s*(job )?title\s*\*?\s*$",
    re.IGNORECASE)
_JOB_EMPLOYER_RE = re.compile(r"\b(company|employer|organi[sz]ation)\b", re.IGNORECASE)
_JOB_LOCATION_RE = re.compile(r"^\s*(job\s+)?location\b|\b(city|town)\b", re.IGNORECASE)
_JOB_DESCRIPTION_RE = re.compile(
    r"\b(role description|description|responsibilit\w*|duties|achievements|summary)\b",
    re.IGNORECASE)
_CURRENT_JOB_RE = re.compile(
    r"\bi currently work here\b|\bcurrent(ly)?\b[^.]{0,24}\b(work|working|employed|role|position|job)\b"
    r"|\bpresent (employer|position)\b|\bstill (work|employed)\b", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec")
_PRESENT_RE = re.compile(r"^\s*(present|current|now|ongoing|till date|to date)\s*$", re.IGNORECASE)
_END_WORDS = ("to", "end", "end date", "date to")


def _split_month_year(text: str) -> tuple[str, str, str]:
    """'07/2020', 'Jul 2020', '2020-07' -> ('07/2020', '07', '2020'). Anything
    that is not a month and a year gives three empty strings: a wrong date on
    an application is worse than a box the model or the candidate fills."""
    raw = str(text or "").strip()
    if not raw or _PRESENT_RE.match(raw):
        return "", "", ""
    year = ""
    month = ""
    years = re.findall(r"(?:19|20)\d{2}", raw)
    if years:
        year = years[-1]
    name = re.search(r"[a-z]{3}", raw, re.IGNORECASE)
    if name:
        word = name.group(0).lower()
        if word in _MONTHS:
            month = f"{_MONTHS.index(word) + 1:02d}"
    if not month:
        for number in re.findall(r"\d{1,2}", re.sub(r"(?:19|20)\d{2}", " ", raw)):
            if 1 <= int(number) <= 12:
                month = f"{int(number):02d}"
                break
    if not (month and year):
        return "", "", ""
    return f"{month}/{year}", month, year


def _first(entry: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def profile_jobs(data: dict[str, Any]) -> list[dict[str, str]]:
    """The profile's `jobs`, most recent first, with every date already split
    the way a form asks for it: the whole box ("07/2020") and the Month and
    Year pieces Workday wants separately. Every value is a string, so a work
    slot is looked up by name exactly like a part of an education entry.

    The candidate's own project work is NOT in here and must never be: it
    belongs on the resume, not in an employment history.
    """
    rows = data.get("jobs")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _first(row, "title", "role", "position")
        company = _first(row, "company", "employer", "organisation", "organization")
        if not (title or company):
            continue
        end_raw = _first(row, "end", "to", "end_date")
        current = bool(row.get("current")) or not end_raw or bool(_PRESENT_RE.match(end_raw))
        start, start_month, start_year = _split_month_year(
            _first(row, "start", "from", "start_date"))
        end, end_month, end_year = ("", "", "") if current else _split_month_year(end_raw)
        out.append({
            "title": title,
            "company": company,
            "location": _first(row, "location", "city"),
            "description": _first(row, "description", "summary"),
            "current": "yes" if current else "",
            "start": start, "start_month": start_month, "start_year": start_year,
            "end": end, "end_month": end_month, "end_year": end_year,
        })
    return out


def _date_side(*scopes: str) -> str:
    """"start" or "end" when one of these headings is a date group's name."""
    for scope in scopes:
        found = DATE_GROUP_RE.match(str(scope or ""))
        if found:
            return "end" if found.group(1).lower() in _END_WORDS else "start"
    return ""


def work_slot(field: dict[str, Any]) -> str:
    """Which part of a job this box wants - title, start_month and so on - or
    an empty string.

    The order of these tests is load-bearing. The checkbox is decided first
    because "I currently work here" contains the word work and would
    otherwise be claimed by a looser rule, and a bare Month or Year box is
    decided before any label rule because its own label says nothing about
    which of the two dates it belongs to.
    """
    label = str(field.get("label") or "").strip()
    field_type = (field.get("type") or "").lower()
    if field_type in ("checkbox", "radio"):
        if field_type == "checkbox" and _CURRENT_JOB_RE.search(label):
            return "current"
        return ""
    part = re.match(r"^\s*(month|mm|year|yyyy|day|dd)\s*\*?\s*$", label, re.IGNORECASE)
    if part:
        side = str(field.get("work_dates") or "") or _date_side(
            field.get("group"), field.get("section"))
        if not side:
            return ""
        piece = part.group(1).lower()
        if piece in ("month", "mm"):
            return f"{side}_month"
        if piece in ("year", "yyyy"):
            return f"{side}_year"
        return ""   # the profile holds no day, and a guessed one is a lie
    whole = _date_side(label)
    if whole:
        return whole
    if _JOB_DESCRIPTION_RE.search(label):
        return "description"
    if _JOB_EMPLOYER_RE.search(label):
        return "company"
    if _JOB_TITLE_RE.search(label):
        return "title"
    if _JOB_LOCATION_RE.search(label):
        return "location"
    return ""


def _match_job(shown: dict[str, str], jobs: list[dict[str, str]], position: int) -> int:
    """Which profile job an entry that already names one is. The position is
    accepted only when it agrees with what the page shows; otherwise a unique
    match on title and company wins, and an entry naming a job the profile
    does not hold gets -1, which means leave it alone."""
    def agrees(job: dict[str, str]) -> int:
        hits = 0
        for slot in ("title", "company"):
            want = plain(job.get(slot) or "")
            got = shown.get(slot, "")
            if want and got:
                hits += 1 if (want in got or got in want) else -1
        return hits

    if 0 <= position < len(jobs) and agrees(jobs[position]) > 0:
        return position
    scored = [i for i, job in enumerate(jobs) if agrees(job) > 0]
    return scored[0] if len(scored) == 1 else -1


def tag_work_entries(fields: list[dict[str, Any]], jobs: list[dict[str, str]]) -> None:
    """Tie every box of a work-history entry to the profile job it belongs to.

    `ordinal` cannot do this on its own, because the boxes that matter most
    sit OUTSIDE the work section entirely: in the real dumps Workday's Month
    and Year carry section "From*"/"To*" and Esko's From*/To* carry no
    section at all. So the page is walked in order instead.

    Sets on every field of an entry: work_pos (which entry on the page),
    work_entry (which profile job, or -1 meaning fill nothing) and, for a
    bare Month or Year box, work_dates.

    An entry that already names an employer the profile does not know gets
    -1 rather than whichever job sits at that position. Writing one job's
    dates into another job's entry would put a false employment record on a
    submitted application, so position alone is never enough.
    """
    inside = False
    pos = -1
    entries: dict[int, list[dict[str, Any]]] = {}
    for field in fields:
        section = str(field.get("section") or "")
        group = str(field.get("group") or "")
        label = str(field.get("label") or "")
        if WORK_SECTION_RE.search(section) or WORK_SECTION_RE.search(group):
            numbered = re.search(r"(\d+)\s*$", section)
            if numbered:
                pos = max(0, int(numbered.group(1)) - 1)
            elif not inside:
                pos = 0
            elif _ENTRY_START_RE.search(label):
                pos += 1        # an unnumbered list starts an entry per title
            inside = True
        elif inside and (not section or DATE_GROUP_RE.match(section)):
            pass                # a stray box still belongs to the entry above
        else:
            inside = False
            continue
        side = _date_side(group, section)
        if side:
            field["work_dates"] = side
        field["work_pos"] = pos
        entries.setdefault(pos, []).append(field)

    for position, group_fields in entries.items():
        shown: dict[str, str] = {}
        for field in group_fields:
            slot = work_slot(field)
            if slot in ("title", "company") and str(field.get("value") or "").strip():
                shown[slot] = plain(str(field.get("value")))
        index = _match_job(shown, jobs, position) if shown else position
        for field in group_fields:
            field["work_entry"] = index if 0 <= index < len(jobs) else -1


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
    if EDUCATION_SECTION_RE.search(section):
        # The profile knows the school, the degree and the exact field of
        # study; the model guessed "Computer Science", which is not one of
        # the 345 options, while "Computer and Information Science" is.
        entries = profile_education(data)
        if ordinal >= len(entries):
            return None
        entry = entries[ordinal]
        for pattern, part in ((_SCHOOL_LABEL_RE, "school"), (_DEGREE_LABEL_RE, "degree"),
                              (_STUDY_LABEL_RE, "field"), (_FROM_LABEL_RE, "start"),
                              (_TO_LABEL_RE, "end")):
            if pattern.search(label):
                return entry.get(part) or None
        return None
    if field.get("work_entry") is not None or WORK_SECTION_RE.search(section):
        # Employment history is the profile's, not the model's: it never
        # changes between applications, and re-deriving it from the resume
        # every time was both the slowest round and the least consistent.
        jobs = profile_jobs(data)
        index = field.get("work_entry")
        if index is None:
            index = ordinal
        if not 0 <= index < len(jobs):
            return None       # a page entry the profile has nothing for
        slot = work_slot(field)
        return (jobs[index].get(slot) or None) if slot else None
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
    """A dropdown that is not a <select> and takes no typing: Workday's
    BUTTON with a listbox popup, Angular Material's <mat-select>. It has to
    be opened and its option clicked, and its shown choice is its text, not
    a value attribute."""
    tag = (field.get("tag") or "").lower()
    if tag in ("input", "textarea", "select"):
        return False
    haspopup = (field.get("haspopup") or "").lower()
    role = (field.get("role") or "").lower()
    if tag == "button":
        return haspopup == "listbox"
    return role in ("combobox", "listbox") or haspopup in ("listbox", "true")


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


# Boxes that carry a contact word but are not the candidate's own address or
# number: another person's, a part of the number, or a one-time code.
_NOT_MY_CONTACT_RE = re.compile(
    r"\b(manager|referee|reference|supervisor|recruiter|colleague|emergency|"
    r"parent|guardian|employer|company|friend|spouse|next of kin)\b"
    r"|\bext(ension)?\b|\bcountry\b|\bcode\b|\btype\b|\bconfirm\b|\bverify\b",
    re.IGNORECASE,
)
_EMAIL_WORD_RE = re.compile(r"\be[\s_-]?mail\b", re.IGNORECASE)
_PHONE_WORD_RE = re.compile(r"\b(phone|mobile|cell|telephone|contact number)\b", re.IGNORECASE)


def contact_topic(field: dict[str, Any]) -> str:
    """'email' or 'phone' for a box that holds the candidate's own contact
    detail, else ''. These two are checked after every step: an ATS that
    parses the uploaded resume overwrote the e-mail with a mis-read address,
    and a wrong one means the employer cannot reply at all.

    Looser than the fill rules on purpose - "Enter Email address (Required)"
    is not a shape resolve() matches by label, but it is still the address
    an employer would write to."""
    if field.get("tag") not in ("input", "textarea"):
        return ""
    if (field.get("type") or "").lower() not in _FILLABLE_TYPES:
        return ""
    label = str(field.get("label") or "")
    name = (field.get("name") or "").strip()
    scope = f"{label} {name}"
    if _NOT_MY_CONTACT_RE.search(scope) or profile.is_secret(scope):
        return ""
    autocomplete = (field.get("autocomplete") or "").strip().lower()
    if autocomplete in ("email",) or _EMAIL_WORD_RE.search(scope):
        return "email"
    if autocomplete in ("tel", "tel-national") or _PHONE_WORD_RE.search(scope):
        return "phone"
    return ""


def same_contact(topic: str, shown: str, wanted: str) -> bool:
    """Does the box hold the candidate's detail? E-mail compares exactly
    (case aside); a phone compares digits, tolerating a country code or the
    national leading zero a widget adds."""
    shown, wanted = (shown or "").strip(), (wanted or "").strip()
    if not wanted:
        return True
    if topic == "email":
        return shown.lower() == wanted.lower()
    here, there = re.sub(r"\D", "", shown), re.sub(r"\D", "", wanted)
    if not here or not there:
        return not here and not there
    tail = there[-10:]
    return here.endswith(tail) or there.endswith(here[-10:])


CAPTURED_OPTIONS = 40   # how many a snapshot keeps; more means the list is cut


def _select_value(value: str, field: dict[str, Any]) -> str:
    """The option to select. When the captured list is truncated (Esko's
    Field of study has 345 options and the snapshot keeps 40) the raw value
    is returned instead, and the worker searches the whole list in the
    browser."""
    options = field.get("options") or []
    option = match_option(value, options)
    if option:
        return option
    return value if len(options) >= CAPTURED_OPTIONS else ""


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
        # Languages, Websites, Education and Work Experience entries come
        # from the profile in order. A job entry's Location is that job's,
        # never the candidate's current one.
        if field_type == "radio":
            return None   # picking an option needs the group's question
        if field_type == "checkbox":
            # A checkbox always carries a value ("on" on Workday, "false" on
            # Esko), so is_blank() is never true for one. What it already
            # answers is `checked`.
            if field.get("checked"):
                return None
        elif not is_blank(field):
            return None
        value = entry_value(field, profile.load_profile())
        if not value:
            return None
        if tag == "select":
            option = _select_value(value, field)
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
    if answers.classify(label, str(field.get("group") or "")) == "sensitive":
        # Work authorisation, sponsorship and the demographic questions are
        # legal declarations. They are answered only from an answer the
        # candidate confirmed themselves, through the ask gate, with a loud
        # log line - never silently from the profile, whatever rules exist.
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
            option = _select_value(value, field)
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
