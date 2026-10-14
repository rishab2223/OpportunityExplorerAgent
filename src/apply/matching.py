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
    "or none."
)


def pick_with_model(invoke, items: list[dict[str, Any]]) -> dict[int, OptionPick]:
    """One call for every open question on the page. `items`: ref, question,
    value, options. Returns the checked picks by ref: the option must be on
    that question's list, word for word, and the rating exact or close."""
    if not items:
        return {}
    lines = []
    for item in items:
        options = "; ".join(item["options"])
        lines.append(f"{item['ref']}. Question: {item['question']}\n"
                     f"   Candidate's answer: {item['value']}\n   Options: {options}")
    result = invoke(PICK_SYSTEM, "\n\n".join(lines), OptionPicks)
    by_ref = {item["ref"]: item for item in items}
    good: dict[int, OptionPick] = {}
    for pick in result.picks:
        item = by_ref.get(pick.ref)
        if item is None or pick.match not in ("exact", "close"):
            continue
        listed = next((o for o in item["options"] if o.strip() == pick.option.strip()), "")
        if not listed or NEUTRAL_OPTION_RE.match(listed):
            continue
        good[pick.ref] = pick.model_copy(update={"option": listed})
    return good
