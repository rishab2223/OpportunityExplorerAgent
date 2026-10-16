"""The words a run searches for, per job site, as the run settings popup
shows them (Oct 2026).

Each site takes them in its own shape, and the settings file says them that
way: Indeed a title query, `title:("Software Engineer" or "NodeJS")`, LinkedIn
the `keywords=` of a search URL, `software engineer OR nodejs`. The popup shows
both as a plain list of terms. A list read from either shape and written back
gives the same query, so a run whose terms nobody touched searches exactly
what it did before.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# How many terms and how long each: src.config (SEARCH_TERMS_MAX,
# SEARCH_TERM_CHARS), checked when the settings are read.

# Characters that would break the query they go into. Taken out, not refused:
# a stray quote in a term is a typo, not a reason to stop a run.
_UNSAFE = re.compile(r'["()]')
_TITLE_RE = re.compile(r"^\s*title\s*:\s*\((.*)\)\s*$", re.IGNORECASE | re.DOTALL)


def clean(terms: list[Any]) -> list[str]:
    """Trimmed, without characters that break a query, no blanks or repeats."""
    out: list[str] = []
    for term in terms or []:
        text = " ".join(_UNSAFE.sub(" ", str(term)).split())
        if text and text.lower() not in (t.lower() for t in out):
            out.append(text)
    return out


def indeed_terms(keyword: str) -> list[str]:
    """The terms of an Indeed keyword: `title:("A" or "B")` gives A and B; any
    other text is one term as it stands."""
    keyword = (keyword or "").strip()
    found = _TITLE_RE.match(keyword)
    if not found:
        return [keyword] if keyword else []
    parts = re.split(r"\s+or\s+", found.group(1), flags=re.IGNORECASE)
    return clean(p.strip().strip('"') for p in parts)


def indeed_query(terms: list[str]) -> str:
    """Terms as Indeed's title query: each matched in the job title."""
    return "title:(" + " or ".join(f'"{t}"' for t in clean(terms)) + ")"


def linkedin_terms(url: str) -> list[str]:
    """The terms in a LinkedIn search URL's keywords=, split on OR."""
    for key, value in parse_qsl(urlsplit(url or "").query, keep_blank_values=True):
        if key == "keywords":
            return clean(re.split(r"\s+OR\s+", value))
    return []


def linkedin_url(url: str, terms: list[str]) -> str:
    """The search URL with its keywords= replaced by these terms, the rest
    (location, sort, recency) as it was."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "keywords"]
    query.insert(0, ("keywords", " OR ".join(clean(terms))))
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


def current(source: str, scrape, apify, default_linkedin_urls: list[str]) -> list[str]:
    """What a run searches a site for: the terms chosen for it, or, when none
    were, the ones its settings.yaml query already holds."""
    chosen = clean((scrape.search_terms or {}).get(source) or [])
    if chosen:
        return chosen
    if source == "indeed":
        return indeed_terms(apify.indeed_input.get("keyword") or scrape.keywords)
    if source == "linkedin":
        urls = apify.linkedin_input.get("searchUrls") or default_linkedin_urls
        return linkedin_terms(urls[0]) if urls else []
    return []
