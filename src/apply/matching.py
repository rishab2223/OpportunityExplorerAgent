"""The best option for an answer a dropdown does not list word for word.

Called only after the exact matcher (worker._choose_option) has found
nothing. Three layers, cheapest first:

1. rules: an ordered scale (Fluent -> Advanced, never above what the profile
   says), the option that is the answer's leading words (Computer Science ->
   Computer), a degree's short form (B.Tech -> Bachelors) and a small family
   of study fields;
2. remembered: what an earlier pick chose for the same answer from the same
   options;
3. a model call for what is left, one per page (pick_with_model), whose
   picks are remembered for next time.

Every pick says why, for the transcript. "Other", "No Selection" and their
kind are never chosen here: they say nothing true about the candidate.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from src import llm_cache
from src.apply import resolver

# Rows that are not answers.
NEUTRAL_OPTION_RE = re.compile(
    r"^\s*(no selection|select|select one|please select|choose|choose one|--+|other|others|none|"
    r"n/?a|not applicable|prefer not to (say|answer|disclose)|decline( to (answer|self identify))?)\s*\W*$",
    re.IGNORECASE)
_YES_NO_RE = re.compile(r"^\s*(yes|no)\s*$", re.IGNORECASE)

# A language level, most specific first ("upper intermediate" before
# "intermediate"). Ranked so that a pick never claims more than the profile:
# Fluent takes Advanced when that is the best the list offers, never Native.
_LEVELS: tuple[tuple[float, str], ...] = (
    (6, r"native|mother tongue|bilingual"),
    (5, r"fluent|expert|full professional"),
    (3.5, r"upper intermediate"),
    (2, r"pre intermediate|elementary|limited working"),
    (4, r"advanced|proficient|professional working"),
    (3, r"intermediate|conversational"),
    (1, r"basic|beginner|limited"),
)

# Degree short forms, as the profile or the model may write them.
_DEGREE_FORMS = (
    (r"b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|bca|bachelor(?:'s|s)? of \w+", "Bachelors"),
    (r"m\.?\s?tech|m\.?\s?e\b|m\.?\s?s\b|m\.?\s?sc|mca|mba|master(?:'s|s)? of \w+", "Masters"),
    (r"ph\.?\s?d|doctor(?:ate)? of \w+", "Doctorate"),
)

# A computing degree's field, as broad lists word it. Tried in order; a word
# matching more than one option decides nothing.
_COMPUTING = re.compile(
    r"^(computer science( (and|&) engineering)?|computer engineering|cse|cs|"
    r"information technology|it|software engineering|computer applications)$")
_COMPUTING_WORDS = ("computer", "information technology", "software", "engineering")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", resolver.plain(text))


def _answers(options: list[str]) -> list[int]:
    """Indices of the rows that are real answers."""
    return [i for i, o in enumerate(options) if o.strip() and not NEUTRAL_OPTION_RE.match(o)]


def _level(text: str) -> float | None:
    flat = " ".join(_words(text))
    for rank, pattern in _LEVELS:
        if re.search(rf"\b(?:{pattern})\b", flat):
            return rank
    return None


def _scale_pick(options: list[str], value: str) -> int:
    """The highest level the list offers that does not overstate `value`."""
    have = _level(value)
    if have is None:
        return -1
    ranked = [(i, _level(options[i])) for i in _answers(options)]
    ranked = [(i, r) for i, r in ranked if r is not None]
    if len(ranked) < 2:
        return -1
    fitting = [(i, r) for i, r in ranked if r <= have]
    if not fitting:
        return -1
    top = max(r for _, r in fitting)
    best = [i for i, r in fitting if r == top]
    return best[0] if len({resolver.plain(options[i]) for i in best}) == 1 else -1


def _leading_words_pick(options: list[str], value: str) -> int:
    """The one option that is the answer's first words: "Computer" for
    "Computer Science". Never a yes or a no, never a row that is not an
    answer, and never one of several."""
    want = _words(value)
    if len(want) < 2:
        return -1
    hits = []
    for i in _answers(options):
        have = _words(options[i])
        if have and len(have) < len(want) and want[:len(have)] == have \
                and not _YES_NO_RE.match(options[i]):
            hits.append(i)
    return hits[0] if len(hits) == 1 else -1


# What a short form says about the subject, for lists that spell the degree
# out: "Bachelor of Technology", "Master of Business Administration". The
# family alone took the first of them - "B.Tech" became "Bachelor of Arts",
# "MBA" "Master of Arts" (code review, Oct 2026).
_DEGREE_SUBJECTS = (
    (r"[bm]\.?\s?tech|[bm]\.?\s?e\b", ("technology", "engineering")),
    (r"[bm]\.?\s?sc|m\.?\s?s\b", ("science",)),
    (r"bca|mca", ("computer applications",)),
    (r"mba|bba", ("business administration", "management")),
    (r"b\.?\s?com|m\.?\s?com", ("commerce",)),
    (r"b\.?\s?a\b|m\.?\s?a\b", ("arts",)),
)
# A family member that names no subject: "Bachelors", "Bachelor's Degree",
# "Bachelors (approx 16 yrs)".
GENERIC_DEGREE_RE = re.compile(
    r"^\s*(bachelor|master|doctor|associate)\w*('?s)?( degree)?( or equivalent)?\s*(\(.*\))?\s*$")
_FAMILY_STEMS = {"Bachelors": "bachelor", "Masters": "master", "Doctorate": "doctor"}


def _degree_pick(options: list[str], value: str, form: str) -> int | None:
    """The family member a short form means: the one naming its subject,
    else the one naming none, else the only one. -1 when several are left to
    choose between; None when the list has no member of the family."""
    subjects = next((s for p, s in _DEGREE_SUBJECTS if re.match(rf"^(?:{p})", resolver.plain(value).strip())), ())
    stem = _FAMILY_STEMS[form]
    family = [i for i in _answers(options) if resolver.plain(options[i]).strip().startswith(stem)]
    if not family:
        return None

    def one(indices: list[int]) -> int:
        return indices[0] if len({resolver.plain(options[i]) for i in indices}) == 1 else -1

    named = [i for i in family if any(re.search(rf"\b{s}\b", resolver.plain(options[i])) for s in subjects)]
    if named:
        return one(named)
    generic = [i for i in family if GENERIC_DEGREE_RE.match(resolver.plain(options[i]))]
    return one(generic or family)


def _degree_form(value: str) -> str:
    flat = resolver.plain(value).strip()
    for pattern, canonical in _DEGREE_FORMS:
        if re.fullmatch(pattern, flat) or re.match(rf"^(?:{pattern})\b", flat):
            return canonical
    return ""


def _computing_pick(options: list[str], value: str) -> int:
    if not _COMPUTING.match(" ".join(_words(value))):
        return -1
    answers = _answers(options)
    for word in _COMPUTING_WORDS:
        hits = [i for i in answers if re.search(rf"\b{word}\b", " ".join(_words(options[i])))]
        if word == "engineering":
            # The broad heading only: "Electrical Engineering" was the
            # nearest field to Computer Science when it was the one
            # engineering on the list (code review, Oct 2026).
            hits = [i for i in hits if not set(_words(options[i])) - {"engineering", "and", "technology", "tech"}]
        if len(hits) == 1:
            return hits[0]
    return -1


def rule_pick(options: list[str], value: str, choose) -> tuple[int, str]:
    """(index, why) by the rules, or (-1, ""). `choose(options, value)` is
    the exact matcher, used again on a rewritten value."""
    if not options or not (value or "").strip():
        return -1, ""
    for alt in resolver.equivalents(value):
        index = choose(options, alt)
        if index >= 0:
            return index, f"'{value}' is offered as '{options[index]}'"
    index = _scale_pick(options, value)
    if index >= 0:
        return index, f"the closest level the list offers to '{value}', and not above it"
    form = _degree_form(value)
    if form and resolver.plain(form) != resolver.plain(value):
        index = _degree_pick(options, value, form)
        if index is None:
            index = choose(options, form)
        if index >= 0:
            return index, f"'{value}' is a {form[:-1].lower()}'s degree"
    index = _leading_words_pick(options, value)
    if index >= 0:
        return index, f"'{options[index]}' is the broad heading for '{value}'"
    index = _computing_pick(options, value)
    if index >= 0:
        return index, f"'{options[index]}' is the nearest field to '{value}' on the list"
    return -1, ""


# -- remembered picks -------------------------------------------------------

_KIND = "option_pick"


def _memory_key(value: str, options: list[str]) -> str:
    return llm_cache.key(resolver.plain(value),
                         "|".join(sorted(resolver.plain(o) for o in options if o.strip())))


def remembered(options: list[str], value: str) -> tuple[int, str]:
    """What an earlier pick chose for this answer from these options."""
    entry = llm_cache.get(_KIND, _memory_key(value, options)) if options and value else None
    chosen = str((entry or {}).get("option") or "")
    for i, option in enumerate(options):
        if chosen and option.strip() == chosen.strip():
            return i, f"chosen for '{value}' before"
    return -1, ""


def remember(options: list[str], value: str, option: str) -> None:
    llm_cache.put(_KIND, _memory_key(value, options), {"option": option})


def best_option(options: list[str], value: str, choose) -> tuple[int, str]:
    """Rules, then memory. (-1, "") when neither knows."""
    index, why = rule_pick(options, value, choose)
    if index >= 0:
        return index, why
    return remembered(options, value)


# -- the model ---------------------------------------------------------------

class OptionPick(BaseModel):
    ref: int = Field(description="The number of the question, as given")
    option: str = Field(default="", description="One option copied exactly from that question's list, or empty")
    match: Literal["exact", "close", "loose", "none"] = Field(
        description="exact: same meaning; close: the nearest honest fit; loose: a stretch; none: nothing fits")
    reason: str = Field(default="", description="A few words")
    position: int = Field(default=0, description=(
        "Only for a question whose options are in a picture: the chosen option's row in "
        "the pictured list, 1 for the top row; else 0"))


class OptionPicks(BaseModel):
    picks: list[OptionPick] = Field(default_factory=list)


PICK_SYSTEM = (
    "You choose dropdown options on a job application for the candidate. For each "
    "question you get the candidate's own answer and the options the form offers. "
    "Pick the option that says the same thing, or the nearest one that is still TRUE "
    "of the candidate. Never claim more than the answer says: 'Fluent' may become "
    "'Advanced', never 'Native'; '6 years' may fall in '5-7 years', never '8+'. Never "
    "pick 'Other', 'No Selection', 'None' or 'Prefer not to say' unless the answer "
    "itself says so. Copy the option exactly as written. If nothing fits honestly, "
    "return match 'none' with an empty option. Rate each pick: exact, close, loose "
    "or none. Some questions have their options in a picture of the open list "
    "instead: read them from it, top to bottom, copy the chosen one as it is shown "
    "and give its row number (1 = the top row of the list). A row you cannot read "
    "clearly is never chosen: return 'none'."
)


def pick_with_model(invoke, items: list[dict[str, Any]],
                    images: list[bytes] | None = None) -> dict[int, OptionPick]:
    """One call for every open question on the page. `items`: ref, question,
    value, options - or, for a list that could not be read, no options and
    `picture`, its number among `images`. Returns the checked picks by ref:
    the option must be on that question's list, word for word, and the rating
    exact or close; a picture's pick must name its row."""
    if not items:
        return {}
    lines = []
    for item in items:
        if item.get("picture"):
            options = (f"not readable as text - they are in picture {item['picture']}, the "
                       f"list opened after '{item.get('typed') or item['value']}' was typed")
        else:
            options = "; ".join(item["options"])
        lines.append(f"{item['ref']}. Question: {item['question']}\n"
                     f"   Candidate's answer: {item['value']}\n   Options: {options}")
    if images:
        result = invoke(PICK_SYSTEM, "\n\n".join(lines), OptionPicks, images=images)
    else:
        result = invoke(PICK_SYSTEM, "\n\n".join(lines), OptionPicks)
    by_ref = {item["ref"]: item for item in items}
    good: dict[int, OptionPick] = {}
    for pick in result.picks:
        item = by_ref.get(pick.ref)
        if item is None or pick.match not in ("exact", "close"):
            continue
        if item.get("picture"):
            # Nothing to check it against but the row and the box's read-back
            # when it is taken.
            listed = pick.option.strip()
            if pick.position < 1 or not listed or NEUTRAL_OPTION_RE.match(listed):
                continue
        else:
            listed = next((o for o in item["options"] if o.strip() == pick.option.strip()), "")
            if not listed or NEUTRAL_OPTION_RE.match(listed):
                continue
        good[pick.ref] = pick.model_copy(update={"option": listed})
    return good
