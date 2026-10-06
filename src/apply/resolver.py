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
from datetime import date, timedelta
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
    # Folded, because Rippling spells it "Résumé": the accents meant the box
    # was not a resume field at all, so no resume was ever prepared for it and
    # nothing logged a failure - the upload simply never happened.
    haystack = plain(" ".join(
        str(field.get(k) or "") for k in ("label", "name", "elid", "group", "text")
    ))
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
     # "Retype Email Address" (SuccessFactors) is the same address again.
     re.compile(r"^(work |primary |your )?e ?mail( address)?$"
                r"|^(retype|re ?type|re ?enter|confirm|repeat|verify)( your)? e ?mail( address)?$")),
    ("phone", ("tel", "tel-national"),
     re.compile(r"^(phone|mobile|phone[_ ]?number|mobile[_ ]?number|contact[_ ]?number)$"),
     re.compile(r"^(mobile|phone|mobile phone)( number)?$|^contact number$")),
    # Derived from the location's last part in resolve(). Typing "India" also
    # resolves phone-code widgets ("India (+91)") - the one rule covers both.
    # Before country: "Country Phone Code" (Workday) wants "+91", not "India".
    # The value is derived in resolve() from phone_country_code / the phone.
    ("phone_country_code", (),
     re.compile(r"^(phone[_ ]?country[_ ]?code|country[_ ]?phone[_ ]?code|dial(ing)?[_ ]?code|country[_ ]?code)$"),
     # "Country/Region Code" beside SuccessFactors' phone box.
     re.compile(r"^(phone |mobile )?country (region )?(phone )?code$|^country phone code$"
                r"|^dial(ing)? code$|^phone code$")),
    ("country", ("country-name", "country"),
     re.compile(r"^(country|country[_ ]?of[_ ]?residence|phone[_ ]?country)$"),
     # "Country/Region of Residence:*" (SuccessFactors, Oct 2026) was left
     # empty, and the State list below it - which follows the country - then
     # offered the whole world.
     re.compile(r"^country( ?/? ?region)?( of residence)?$|^country ?/ ?region( code)?$"
                r"|^(current )?country of (residence|domicile)$")),
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
     # The fingerprint drops the slash: "State/Province" reads "state province"
     # (SuccessFactors, Oct 2026), which the slash-only pattern never matched.
     re.compile(r"^state( ?/? ?(province|region|territory))?$|^province$")),
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
     re.compile(r"^(portfolio|website|personal[_ ]?site|website[_ ]?link)$"),
     # "Website link" is the label on Rippling's box; the anchored pattern
     # wanted the bare word and let it through empty.
     re.compile(r"\b(portfolio|personal (web)?site)\b|^website( link| url)?$")),
    # EPAM (Oct 2026): "Most recent employer" and "Job Title at Recent
    # employer". Anchored, so the title's label is not taken for the company.
    ("current_company", ("organization",),
     re.compile(r"^(company|current[_ ]?company|employer|organization)$"),
     re.compile(r"\b(current |present )(company|employer)\b"
                r"|^(most |your )?(recent|last|latest|previous) (company|employer)( name)?$")),
    ("current_title", ("organization-title",),
     re.compile(r"^(title|job[_ ]?title|current[_ ]?title|designation)$"),
     re.compile(r"\b(current |present )(title|designation|role)\b"
                r"|^(job )?(title|designation|role) at (your )?(most )?(current|recent|last|latest) "
                r"(company|employer)$")),
    # "Total Professional Experience*" (EPAM): a word may sit between.
    ("total_experience_years", (),
     re.compile(r"^(experience|total[_ ]?experience|years[_ ]?of[_ ]?experience)$"),
     re.compile(r"\b(years? of|total)( professional| work| industry| overall)? experience\b")),
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
    # "Current Annual Compensation" (EPAM): a word may sit between.
    ("current_ctc", (),
     re.compile(r"^(current[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(current|present)( annual| yearly| total| fixed| base)? (ctc|salary|compensation|pay)\b")),
    ("expected_ctc", (),
     re.compile(r"^(expected[_ ]?(ctc|salary|compensation))$"),
     re.compile(r"\b(expected|desired)( annual| yearly| total| fixed| base)? (ctc|salary|compensation|pay)\b")),
    ("willing_to_relocate", (),
     re.compile(r"^(relocate|willing[_ ]?to[_ ]?relocate)$"),
     re.compile(r"\bwilling to relocate\b")),
    ("postal_code", ("postal-code",),
     re.compile(r"^(zip|zipcode|postal[_ ]?code|pin[_ ]?code|postcode)$"),
     # "Pincode", "PIN Code", "Zip / PIN", "PIN/Postal code": the fingerprint
     # drops the slash, so the pair reads "zip pin". EPAM (Oct 2026): "PIN code
     # (postal code)" reads "pin code postal code" - each name may carry its
     # own "code".
     re.compile(r"^(zip|postal|pin)( ?code)?( ?/? ?(postal|pin|zip)( ?code)?)?$|^postcode$")),
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
    ("father_name", (),
     re.compile(r"^father'?s?[_ ]?(full[_ ]?)?name$"),
     re.compile(r"^(your )?father ?'?s? (full )?name$|^name of (your )?father$")),
    # EPAM's optional "Preferred Pronouns" (Oct 2026). Only what the profile
    # holds; an empty one leaves the box alone.
    ("pronouns", (),
     re.compile(r"^(preferred[_ ]?)?pronouns?$"),
     re.compile(r"^(your |preferred )?pronouns?$")),
    # Relevant experience must be read BEFORE total experience, or "years of
    # relevant experience" resolves to the whole career.
    ("relevant_experience_years", (),
     re.compile(r"^(relevant[_ ]?experience|years[_ ]?of[_ ]?relevant[_ ]?experience)$"),
     re.compile(r"\brelevant experience\b")),
    # One skill, not the list: the profile's first (strongest) skill. EPAM's
    # "Primary Skill*" dropdown (Oct 2026). Derived in resolve().
    ("primary_skill", (),
     re.compile(r"^(primary|main)[_ ]?skill$"),
     re.compile(r"^(primary|main|core) skill$")),
    # Education, split out of the one "education" line: a 345-entry "Field of
    # study" dropdown and a degree list are answered from the profile instead
    # of the model guessing "Computer Science", which is not an option.
    ("university", (),
     re.compile(r"^(university|college|school|institute|institution)$"),
     # Not anchored to the bare word: Rippling asks "Share the name of your
     # Institute/College:", which matched nothing, so a required box stayed
     # empty with the answer sitting in the profile.
     re.compile(r"^(name of )?(university|college|school|institute|institution)$"
                r"|\bname of (your |the )?(university|college|school|institut\w+)"
                r"|\b(university|college|school|institut\w+) name\b")),
    ("gpa_10_point", (),
     re.compile(r"^gpa[_ ]?10([_ ]?point)?$|^cgpa[_ ]?10$"),
     re.compile(r"\b10[\s-]*point\b")),
    ("gpa_5_point", (),
     re.compile(r"^gpa[_ ]?5([_ ]?point)?$|^cgpa[_ ]?5$"),
     re.compile(r"\b5[\s-]*point\b")),
    ("degree_recognized_by", (),
     re.compile(r"^degree[_ ]?recognized[_ ]?by$"),
     # "My degree was awarded by an institution recognized by:" - a dropdown
     # of accrediting bodies (UGC, AICTE, ...), constant for a candidate and
     # asked by every Indian form, so it is a profile fact, not a model guess.
     re.compile(r"\brecogni[sz]ed by\b|\bdegree was awarded\b"
                r"|\baccredit(ed|ing) (body|authority)\b")),
    ("highest_education_level", (),
     re.compile(r"^(degree|qualification|education[_ ]?level|highest[_ ]?qualification)$"),
     re.compile(r"\b(highest )?(degree|qualification)\b|\beducation level\b"
                r"|\blevel of education\b")),
    ("field_of_study", (),
     re.compile(r"^(field[_ ]?of[_ ]?study|major|specialization|specialisation|discipline)$"),
     # "^field$" is the whole label, never a word inside one: Prachay's box
     # beside Qualification is labelled with the bare word and was left for
     # the candidate to type, while "Required field", "This field is
     # required" and "Field of work" all contain it and mean nothing of
     # the sort. The required marker is folded away before matching, so a
     # "Field *" and a "Field * (required)" are the same label here.
     re.compile(r"\bfield of study\b|\bmajor\b|\bspeciali[sz]ation\b|\bdiscipline\b"
                r"|^field( required)?$")),
    ("graduation_year", (),
     re.compile(r"^(graduation[_ ]?year|year[_ ]?of[_ ]?passing|passing[_ ]?year)$"),
     re.compile(r"\b(graduation|passing|completion) year\b|\byear of (graduation|passing)\b")),
]


# A parenthesised instruction is guidance for the human, not the question, and
# fingerprint() strips the brackets that mark it as an aside - so its words
# join the label and can be matched as if they were the subject.
#
# "Education (use this format: course-institute-year of passing)" is a box for
# a whole education entry. Its hint mentions a year of passing, the graduation
# rule matched that, and the box was filled with 2019. Only hints that
# announce themselves as instructions are removed; "Notice period (in days)"
# and "Experience (in years)" keep every word, because there the bracket IS
# the question.
_FORMAT_HINT_RE = re.compile(
    r"\([^()]*\b(?:use this format|in this format|format|e\s*\.?\s*g|eg|"
    r"i\s*\.?\s*e|for example|example|sample)\b[^()]*\)",
    re.IGNORECASE,
)


def _without_format_hint(label: str) -> str:
    trimmed = _FORMAT_HINT_RE.sub(" ", label or "")
    # A label that was ONLY a hint keeps its text: better to match on it than
    # on nothing at all.
    return trimmed if trimmed.strip() else (label or "")


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


def plain_label(label: str) -> str:
    """A label read as words. A box with no caption is labelled by its name -
    DentCare's "language_proficiency[]" (Oct 2026) - and the underscore is a
    word character, so neither "language" nor "proficiency" was a word in it:
    the row went unfilled and its Add was clicked again for an entry that was
    already there."""
    return re.sub(r"[_\[\]]+", " ", label or "").strip()


def is_language_name_label(label: str) -> bool:
    return bool(re.match(r"^\s*languages?\b", plain_label(label), re.IGNORECASE))


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
    'Example University - Bachelors, Computer and Information Science,
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
# A control that names one numbered entry of the list: "Delete Work
# Experience 3", "Remove Employment 2". Anchored to a delete/remove verb, so
# a heading that merely ends in a digit ("Employment history 2020") cannot
# renumber the walk.
_ENTRY_LABEL_RE = re.compile(
    r"\b(delete|remove)\b.*\b(work|employment|experience|position|job)\b.*\d\s*$",
    re.IGNORECASE)
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
            # UKG numbers the ENTRY rather than the section: its boxes carry
            # no section at all and the only thing that says which entry they
            # belong to is the "Delete Work Experience 3" button above them.
            # Reading only the section collapsed all four entries onto
            # position 0, so a form the site had already filled from the
            # resume counted as one entry, Add Experience was clicked twice
            # more, and the same job was written into both - three identical
            # "Software Engineer at Acme" rows on a submitted application.
            if not numbered and _ENTRY_LABEL_RE.search(label):
                numbered = re.search(r"(\d+)\s*$", label)
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


_NOTICE_LABEL_RE = re.compile(r"\bnotice\b", re.IGNORECASE)
_IMMEDIATE_RE = re.compile(
    r"\b(immediate\w*|available now|right away|asap|none|nil|no notice)\b", re.IGNORECASE)
_NOTICE_UNIT_RE = re.compile(r"\bin\s+(days?|weeks?|months?)\b|\(\s*(days?|weeks?|months?)\s*\)",
                             re.IGNORECASE)
_NOTICE_SPAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(day|week|month|year)s?", re.IGNORECASE)
_PER_DAY = {"day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}


def notice_days(text: str) -> float | None:
    """'Immediate Joiner' -> 0, '2 months' -> 60, '30 days' -> 30, '45' -> 45.
    None when the phrase says nothing about a length of time."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if _IMMEDIATE_RE.search(raw):
        return 0.0
    span = _NOTICE_SPAN_RE.search(raw)
    if span:
        return float(span.group(1)) * _PER_DAY[span.group(2).lower()]
    bare = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", raw)
    return float(bare.group(1)) if bare else None


def derived_start_date(data: dict[str, Any], today: date | None = None) -> str:
    """The earliest start date the notice period implies, as YYYY-MM-DD, or ''
    when the notice period is empty or says no length of time.

    "What exact earliest start date?" was asked on three real forms, with
    earliest_start_date empty in the profile and the notice period right
    beside it. A date that lands on a weekend moves to the Monday after.
    """
    days = notice_days(str(data.get("notice_period") or ""))
    if days is None:
        return ""
    start = (today or date.today()) + timedelta(days=round(days))
    while start.weekday() >= 5:
        start += timedelta(days=1)
    return start.isoformat()


def notice_for_field(value: str, field: dict[str, Any]) -> str:
    """A notice period in the unit THIS box asks for.

    LinkedIn's "Notice Period (In days)*" is a text input that validates as a
    number, so "Immediate Joiner" went in and the form answered "Enter a
    decimal number larger than 0.0". The profile keeps the human phrase; this
    converts it wherever a box wants a count instead.
    """
    label = " ".join(str(field.get(k) or "") for k in ("label", "group", "section"))
    if not _NOTICE_LABEL_RE.search(label):
        return value
    unit_match = _NOTICE_UNIT_RE.search(label)
    numeric = (field.get("type") or "").lower() == "number"
    if not unit_match and not numeric:
        return value                      # a free-text box keeps the phrase
    days = notice_days(value)
    if days is None:
        return value
    unit = (unit_match.group(1) or unit_match.group(2) or "days").lower() if unit_match else "days"
    per = _PER_DAY["day" if unit.startswith("day") else
                   "week" if unit.startswith("week") else "month"]
    count = days / per
    return str(int(count)) if count == int(count) else f"{count:.1f}"


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
        if is_language_name_label(label):
            return name
        if _LEVEL_LABEL_RE.search(plain_label(label)):
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
        # Radix/shadcn sets role=combobox and aria-expanded on its trigger and
        # no aria-haspopup at all, so the button was not read as a dropdown
        # and fill() was attempted on it ("Element is not an <input>").
        return haspopup == "listbox" or role == "combobox"
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


# Radio groups answered from the profile. The pattern matches the group's
# QUESTION - never an option's own text - and the stored answer must then name
# this very option. Everything else about radios is unchanged: they stay the
# model's to decide, and a legal declaration is refused before this is reached.
_RADIO_GROUP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("college_tier", re.compile(r"\bcollege tier\b|\binstitute tier\b"
                                r"|\btier does your (institute|college)\b")),
    # EPAM (Oct 2026): "Are you willing to relocate to the specified job
    # location?" as Yes / No tiles, left empty with "Yes" in the profile.
    ("willing_to_relocate", re.compile(r"\b(willing|open|ready|able) to relocate\b")),
    ("willing_to_travel", re.compile(r"\b(willing|open|ready|able) to travel\b")),
)


# Facts to offer only where the form insists on them. A GPA is a number that
# says less about six years of work than the work does, and a form that leaves
# it optional is a form that does not need it - so it goes in when it is
# required and is left alone otherwise.
_ONLY_WHEN_REQUIRED = {"gpa_10_point", "gpa_5_point"}


def _radio_from_profile(field: dict[str, Any]) -> tuple[str, str] | None:
    """Tick one option of a known group, or nothing at all.

    Returns "yes" rather than the option text: a radio is ticked, not typed,
    and only the option the profile actually names is ever ticked. A group we
    recognise whose stored answer matches no option picks nothing - better an
    unanswered question than a confidently wrong box.
    """
    question = profile.fingerprint(str(field.get("group") or ""))
    option = str(field.get("label") or "").strip()
    if not question or not option or field.get("checked"):
        return None
    data = profile.load_profile()
    for key, question_re in _RADIO_GROUP_RULES:
        if not question_re.search(question):
            continue
        stored = str(data.get(key) or "").strip()
        if stored and stored.casefold() == option.casefold():
            return "yes", "profile"
        return None
    return None


def resolve(field: dict[str, Any]) -> tuple[str, str] | None:
    """(value, source) for a field the script can fill without the model, else None.

    source is 'profile', 'saved', 'resume' (file inputs) or 'notice period' (a
    start date worked out from it), for the log line.
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
    if about_someone_else(field):
        return None
    listbox = is_listbox_button(field)
    # A styled radio is a div, so the tag says nothing; what it acts as does.
    if tag not in ("input", "textarea", "select") and not listbox \
            and field_type not in ("radio", "checkbox"):
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
    if not listbox and field_type == "radio":
        picked = _radio_from_profile(field)
        if picked is not None:
            return picked
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
    norm_label = profile.fingerprint(_without_format_hint(label))
    if profile.is_secret(f"{label} {name}"):
        return None
    if answers.classify(label, str(field.get("group") or "")) == "sensitive":
        # Work authorisation, sponsorship and the demographic questions are
        # legal declarations. They are answered only from an answer the
        # candidate confirmed themselves, through the ask gate, with a loud
        # log line - never silently from the profile, whatever rules exist.
        return None

    years = _education_year(field, data) or _tenure_date(field, data)
    # A whole date (YYYY-MM-DD) suits a text or date box; a bare year suits
    # a text or number box. Neither goes where it would be refused.
    if years and tag == "input" and field_type in (
            ("", "text", "date") if "-" in years else ("", "text", "number")):
        return years, "profile"

    for key, ac_values, name_re, label_re in _RULES:
        value = str(data.get(key) or "").strip()
        derived = False
        if not value and key == "earliest_start_date" and tag in ("input", "textarea") and not listbox:
            # Not for a dropdown: its options are phrases ("Within 30 days"),
            # and a date would match none of them.
            value = derived_start_date(data)
            derived = bool(value)
        if not value and key in ("first_name", "last_name"):
            parts = str(data.get("full_name") or "").split()
            if key == "first_name" and parts:
                value = parts[0]
            elif key == "last_name" and len(parts) > 1:
                value = " ".join(parts[1:])
        if not value and key == "country":
            value = _country(data)
        if not value and key == "primary_skill":
            value = re.split(r"[,;\n]", str(data.get("skills") or ""))[0].strip()
        if not value and key == "portfolio":
            # A "Website link" box with no portfolio to put in it: GitHub is
            # the site this candidate actually has, and leaving it blank was
            # worse than the near-miss. LinkedIn has its own rule and its own
            # box, so it is not a candidate here.
            value = str(data.get("github") or "").strip()
        if key == "phone_country_code":
            value = _dial_code(data)
        if not value:
            continue
        if key in _ONLY_WHEN_REQUIRED and not field.get("required"):
            continue
        if key in ("gpa_10_point", "gpa_5_point"):
            # Forms ask both, and the honest answer to the one you did NOT
            # study on is the option that says so - not a converted figure.
            # myKaarma offers "I attended a university using a 10-point scale"
            # beside "4 or higher / 3 / 2 or below", and a 3.7 fits none of
            # those because a 7.4/10 is not really a 3.7/5.
            studied = str(data.get("gpa_scale") or "").strip()
            asked = "10" if key == "gpa_10_point" else "5"
            if studied and studied != asked:
                value = f"{studied}-point scale"
        if key == "location" and "," in value and re.search(r"\bcity\b", norm_label):
            value = value.split(",")[0].strip()  # "Bangalore, India" -> City: Bangalore
        matched = (
            (autocomplete and autocomplete in ac_values)
            or (name and name_re.fullmatch(name) is not None)
            or (norm_label and label_re.search(norm_label) is not None)
        )
        if not matched:
            continue
        if derived:
            # A real <input type="date"> takes it too: the value is always
            # YYYY-MM-DD, which is the one format such a box accepts.
            fits = tag == "textarea" or field_type in _FILLABLE_TYPES or field_type == "date"
            return (value, "notice period") if fits else None
        if tag == "select":
            option = _select_value(value, field)
            if not option and key == "phone_country_code":
                option = next((o for o in field.get("options") or [] if value in o), "")
            return (option, "profile") if option else None
        if listbox:
            # Options are unknown until the list opens; the worker matches
            # the value against them then (and refuses when nothing fits).
            return value, "profile"
        if tag == "input" and field_type == "date":
            # A real date box takes YYYY-MM-DD and nothing else. It was not
            # a fillable type here, so the profile's "03/10/1997" never went
            # in, the model typed a format the box refused ("Malformed
            # value"), and got it right two minutes later (CryptoMize, Oct 2
            # 2026). The profile writes dates day first (DD/MM/YYYY).
            iso = iso_date(value)
            return (iso, "profile") if iso else None
        if tag == "input" and field_type not in _FILLABLE_TYPES:
            return None
        return value, "profile"

    # Answer bank: neutral topics only. Sensitive entries are used by the ask
    # gate (with loud logging), never silently by the sweep.
    entry = answers.lookup(answers.question_key(label, field.get("group") or ""))
    if entry and entry["kind"] == "neutral":
        value = entry["answer"]
        if tag == "select":
            option = match_option(value, field.get("options") or [])
            if not option:
                return None
            answers.touch(entry["key"])
            return option, "saved"
        if listbox:
            answers.touch(entry["key"])
            return value, "saved"
        if tag == "input" and field_type not in _FILLABLE_TYPES:
            return None
        answers.touch(entry["key"])
        return value, "saved"
    return None


# A school's first and last year, as one form outside an Education section
# asks them: EPAM's "Education Years" (Oct 2026) is a start box under that
# label and an unlabelled end box, known by their ids (educationStartDate,
# educationEndDate). The profile's education line carries "2015-2019".
_EDU_START_RE = re.compile(r"edu\w*?(start|from|begin)|(start|from|begin)\w*?edu", re.IGNORECASE)
# Not "to": "educationHistory" holds it. Not the "end" of "eduAttended" either.
_EDU_END_RE = re.compile(r"edu(?!\w*attend)\w*?(end|until|finish|graduat)|(end|until)\w*?edu",
                         re.IGNORECASE)
_EDU_YEARS_LABEL_RE = re.compile(r"^(education|study|studies|degree) (years|dates|period)$")


def _education_year(field: dict[str, Any], data: dict[str, Any]) -> str:
    if in_repeating_section(field):
        return ""      # an Education entry has its own handling
    ident = f"{field.get('elid') or ''} {field.get('name') or ''}"
    label = profile.fingerprint(_without_format_hint(str(field.get("label") or "")))
    if _EDU_END_RE.search(ident):
        end = True
    elif _EDU_START_RE.search(ident) or _EDU_YEARS_LABEL_RE.match(label):
        end = False
    else:
        return ""
    # The full date first, when the candidate has given one: a date picker
    # that wants a day takes nothing less. Handed on as YYYY-MM-DD, which the
    # date box reads without any day/month ambiguity and writes in its own
    # format.
    full = iso_date(str(data.get("education_end_date" if end else "education_start_date") or ""))
    if full:
        return full
    years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(data.get("education") or "").split(";")[0])
    graduated = str(data.get("graduation_year") or "").strip()
    if end:
        return graduated or (years[-1] if len(years) > 1 else "")
    return years[0] if years and (not graduated or years[0] != graduated) else ""


# The most recent job's dates, asked outside a Work Experience section: EPAM's
# "Tenure at Recent employer" is a start box under that label and an
# unlabelled end box (startDateAtRecentEmployer / endDateAtRecentEmployer).
_RECENT_JOB = r"(recent|current|last|latest|present)\w*?employ"
_TENURE_START_RE = re.compile(rf"(start|from)\w*?{_RECENT_JOB}|{_RECENT_JOB}\w*?(start|from)", re.IGNORECASE)
_TENURE_END_RE = re.compile(rf"(end|until)\w*?{_RECENT_JOB}|{_RECENT_JOB}\w*?(end|until)", re.IGNORECASE)
_TENURE_LABEL_RE = re.compile(r"^(tenure|dates?|period) (at|with) (your )?(most )?(recent|current|last|latest) "
                              r"(employer|company)$")


def _tenure_date(field: dict[str, Any], data: dict[str, Any]) -> str:
    """YYYY-MM-01 for the latest job's start or end. The profile holds a
    month, the box a whole date: the first of the month, as everywhere else
    an employment date needs a day - never for anything but employment."""
    if in_repeating_section(field):
        return ""
    ident = f"{field.get('elid') or ''} {field.get('name') or ''}"
    label = profile.fingerprint(_without_format_hint(str(field.get("label") or "")))
    if _TENURE_END_RE.search(ident):
        end = True
    elif _TENURE_START_RE.search(ident) or _TENURE_LABEL_RE.match(label):
        end = False
    else:
        return ""
    jobs = profile_jobs(data)
    if not jobs:
        return ""
    month, year = (jobs[0]["end_month"], jobs[0]["end_year"]) if end else (
        jobs[0]["start_month"], jobs[0]["start_year"])
    if not (month and year):
        return ""      # a current job has no end; a missing month is not made up
    return f"{int(year):04d}-{int(month):02d}-01"


# Facts a site pre-fills from its own reading of the CV, often wrongly, and
# that the profile is the truth for. EPAM (Oct 2026) parsed the resume into
# "7 years" of experience (the profile says 6), "Amazon Web Services" as the
# primary skill, "0 years" of relevant experience, and the candidate's own
# project, "Applied AI & LLM Agents", as their job title - and the agent left
# all of it, because it never overwrites a box that holds something. Contact
# details are not on the list: a site's own formatting of a phone number or
# a city is not a disagreement.
CORRECTABLE = frozenset({
    "total_experience_years", "relevant_experience_years", "primary_skill",
    "current_company", "current_title", "notice_period",
})


def rule_key(field: dict[str, Any]) -> str:
    return _rule_key(field)


# A section about another person: its Name, Phone and Qualification are
# theirs. DentCare (Oct 2026) put the candidate's own name and phone in the
# Family Details rows and "Bachelors" in three family members' qualification.
OTHER_PERSON_SECTION_RE = re.compile(
    r"\b(family|dependents?|references?|referees?|emergency contacts?|next of kin|spouse"
    r"|guardians?|nominees?|parents?|relatives?)\b", re.IGNORECASE)


def about_someone_else(field: dict[str, Any]) -> bool:
    return bool(OTHER_PERSON_SECTION_RE.search(str(field.get("section") or "")))


# "Do you currently or have you previously worked for Databricks?" - the
# profile's employers already answer it, and the candidate was asked in chat
# (Databricks, Oct 2026). Only the hiring company counts: "Have you worked for
# a startup?" names no one, and a question about someone the candidate knows
# there is not about their own employment.
_WORKED_HERE_RE = re.compile(
    r"\b(?:worked|employed|been an? (?:employee|intern|contractor)"
    r"|(?:former|ex|previous|past|current)[- ](?:\w+ )?(?:employee|intern|contractor)"
    r"|work(?:ing)? (?:for|at|with))\b", re.IGNORECASE)
_NOT_OWN_EMPLOYMENT_RE = re.compile(
    r"\b(?:know|knows|refer\w*|relatives?|related|family|friends?|anyone|someone|spouse"
    r"|why|what|how|describe)\b", re.IGNORECASE)
_COMPANY_SUFFIX_RE = re.compile(
    r"[,.]?\s+(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|plc|gmbh|ag|pvt|private"
    r"|technologies|technology|systems|software|solutions|labs)\.?$", re.IGNORECASE)


def _company_core(name: str) -> str:
    """"Cisco Systems, Inc." -> "cisco": the part a question would say."""
    core = " ".join(str(name or "").split())
    while True:
        shorter = _COMPANY_SUFFIX_RE.sub("", core).strip()
        if shorter == core or not shorter:
            return core.lower()
        core = shorter


def _names_company(text: str, core: str) -> bool:
    return bool(core) and bool(re.search(rf"(?<!\w){re.escape(core)}(?!\w)", text.lower()))


def worked_here_before(field: dict[str, Any], company: str) -> tuple[str, str] | None:
    """("No", "profile") for "have you worked for <this company>?" when no job
    in the profile was there. One that was is left alone: the dates and the
    role that usually follow are the candidate's to give.

    Only a choice is answered (a dropdown, or the No of a radio pair); a text
    box asking this wants more than a word."""
    field_type = (field.get("type") or "").lower()
    tag = field.get("tag") or ""
    radio = field_type == "radio"
    choice = (tag == "select" or radio or is_listbox_button(field)
              or (tag == "input" and field.get("role") == "combobox"))
    if not choice or about_someone_else(field) or field.get("group_ids"):
        return None
    if radio:
        if field.get("checked") or not re.match(r"\s*no\b", str(field.get("label") or ""), re.IGNORECASE):
            return None
        question = str(field.get("group") or "")
    else:
        if not is_blank(field):
            return None
        question = str(field.get("label") or "")
    core = _company_core(company)
    if (len(core) < 2 or not _WORKED_HERE_RE.search(question)
            or _NOT_OWN_EMPLOYMENT_RE.search(question) or not _names_company(question, core)):
        return None
    data = profile.load_profile()
    employers = [_company_core(j["company"]) for j in profile_jobs(data) if j.get("company")]
    if str(data.get("current_company") or "").strip():
        employers.append(_company_core(str(data["current_company"])))
    if not employers:
        return None   # nothing to go on: an empty profile is not "never"
    if any(e == core or _names_company(e, core) or _names_company(core, e) for e in employers):
        return None
    if radio:
        return "yes", "profile"
    if tag == "select":
        option = _select_value("No", field)
        return (option, "profile") if option else None
    return "No", "profile"


def _rule_key(field: dict[str, Any]) -> str:
    """Which profile rule this box answers to, '' for none - by the same
    autocomplete / name / label tests resolve() uses."""
    label = field.get("label") or ""
    name = (field.get("name") or "").strip().lower()
    autocomplete = (field.get("autocomplete") or "").strip().lower()
    norm_label = profile.fingerprint(_without_format_hint(label))
    for key, ac_values, name_re, label_re in _RULES:
        if ((autocomplete and autocomplete in ac_values)
                or (name and name_re.fullmatch(name) is not None)
                or (norm_label and label_re.search(norm_label) is not None)):
            return key
    return ""


def _agrees(key: str, wanted: str, current: str) -> bool:
    if key.endswith("_years"):
        # "7 years" against 6; "6 years" against "6.5" is the years box of a
        # years-and-months pair, and agrees.
        have = re.search(r"\d+(?:\.\d+)?", current)
        want = re.search(r"\d+(?:\.\d+)?", wanted)
        return bool(have and want) and int(float(have.group())) == int(float(want.group()))
    a = re.sub(r"[^a-z0-9]", "", wanted.lower())
    b = re.sub(r"[^a-z0-9]", "", current.lower())
    # "Cadence" for "Cadence Design Systems", "Immediately" for "Immediate".
    # Not a shared start: "Software Architect" is not "Software Engineer".
    return bool(a and b) and (a in b or b in a)


def correction(field: dict[str, Any]) -> tuple[str, str] | None:
    """(profile value, current value) for a box that already holds something
    the profile contradicts - only for the CORRECTABLE facts - else None."""
    current = str(field.get("value") or "").strip()
    if not current or is_blank(field) or about_someone_else(field):
        return None
    # EPAM pre-filled "Tenure at Recent employer" with 02/01/2026 - the start
    # of the candidate's own project, not of the job.
    tenure = _tenure_date(field, profile.load_profile())
    if tenure:
        year, month = tenure[:4], int(tenure[5:7])
        numbers = [int(n) for n in re.findall(r"\d+", current)]
        agrees = (year in current and any(n == month for n in numbers if n <= 12))
        return None if agrees else (tenure, current)
    key = _rule_key(field)
    if key not in CORRECTABLE or in_repeating_section(field):
        return None
    resolved = resolve(dict(field, value=""))
    if resolved is None or resolved[1] != "profile":
        return None
    wanted = resolved[0]
    # A project is never a job title, however the two compare.
    if _agrees(key, wanted, current) and not (key == "current_title" and _is_project_entry(current)):
        return None
    return wanted, current


def _is_project_entry(text: str) -> bool:
    """One of the candidate's own projects, which is never a job title."""
    projects = str(profile.load_profile().get("not_employment") or "")
    t = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(t) and any(
        t == re.sub(r"[^a-z0-9]", "", p.lower()) for p in re.split(r"[;\n]", projects) if p.strip())


# Renamed cities: a form's list may carry either name, and searching one
# never shows the other: searching the old name turns up a same-named city
# in another state, while the one that is wanted is listed under the new.
_CITY_ALIASES = {
    "gurgaon": "gurugram", "bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai",
    "calcutta": "kolkata", "poona": "pune", "trivandrum": "thiruvananthapuram",
    "cochin": "kochi", "mysore": "mysuru", "baroda": "vadodara", "allahabad": "prayagraj",
    "belgaum": "belagavi", "mangalore": "mangaluru", "simla": "shimla", "cawnpore": "kanpur",
}
_CITY_ALIASES.update({v: k for k, v in list(_CITY_ALIASES.items())})


def city_aliases(value: str) -> list[str]:
    """Other spellings of the city that starts `value` ("Bangalore, India" ->
    ["Bengaluru, India"]); empty for anything not in the table."""
    text = (value or "").strip()
    if not text:
        return []
    head = re.split(r"[,\s]", text, 1)[0]
    alias = _CITY_ALIASES.get(head.lower())
    if not alias:
        return []
    return [alias.title() + text[len(head):]]


def plain(text: str) -> str:
    """Lower-case, accents stripped: Workday lists "Karnātaka" and "Odishā";
    nobody types the macron."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DAY_FIRST_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


def iso_date(value: str) -> str:
    """A profile date as YYYY-MM-DD: ISO as it is, otherwise day first
    (DD/MM/YYYY, as the profile asks for). '' when it is not a real date -
    never a guess at which number is the month."""
    from datetime import date

    text = (value or "").strip()
    found = _ISO_DATE_RE.match(text)
    if found:
        year, month, day = (int(g) for g in found.groups())
    else:
        found = _DAY_FIRST_RE.match(text)
        if not found:
            return ""
        day, month, year = (int(g) for g in found.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def match_option(value: str, options: list[str]) -> str:
    """Pick the <select> option for a value in code: exact, case- and
    accent-insensitive, then whole-word containment - never a bare substring
    guess. Then the same answer's other wordings (equivalents())."""
    found = _match_option(value, options)
    if found or not value:
        return found
    for alt in equivalents(value):
        found = _match_option(alt, options)
        if found:
            return found
    return ""


def _match_option(value: str, options: list[str]) -> str:
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


# An immediate notice period, as forms word it. EPAM (Oct 2026) offers
# "Available now", and "Immediate Joiner" shares no word with it: the list
# emptied on typing and the box was left. Only a phrase that is ABOUT
# availability triggers it: "None" or "0" could be the answer to anything,
# and "Now" matches too much ("Not now").
_IMMEDIATE_ANSWER_RE = re.compile(
    r"^(immediate(ly)?( joiner| joining| start| availability| availability to join)?"
    r"|available (now|immediately)|no notice( period)?)$")
_IMMEDIATE_WORDINGS = ("Available now", "Immediately available", "Immediately", "Immediate",
                       "Immediate joiner", "0 days", "No notice period", "Currently available")


def equivalents(value: str) -> list[str]:
    """Other ways a form words the same answer; empty when there are none."""
    if _IMMEDIATE_ANSWER_RE.match(plain(value)):
        return [w for w in _IMMEDIATE_WORDINGS if plain(w) != plain(value)]
    return []
