"""What the score and enrich steps share: finding the postings that are one
job listed several times, and running one call per job across a pool.

A bank posting one role in six cities gives six scrapes with the same text
bar the city name. Scoring or tailoring each costs a full model call for
the same answer, so the first is worked and the rest copy it. Two postings
are twins only when the company and title match AND the descriptions are
nearly the same text - Recrew's two "Backend Engineer" posts differed by
4% and were two teams (Oct 2026), so the title alone is not enough.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any, Callable, TypeVar

from src import history

T = TypeVar("T")

# How alike two descriptions must be to count as one posting. The city in
# the text is a few characters out of thousands; a different team's wording
# is not.
TWIN_RATIO = 0.97
_SPACE = re.compile(r"\s+")


def _text(item: dict[str, Any]) -> str:
    return _SPACE.sub(" ", str(item.get("description") or "").lower()).strip()


def twins(items: list[dict[str, Any]]) -> dict[int, int]:
    """{index of a copy: index of the first it copies}, in list order."""
    firsts: dict[str, list[int]] = {}
    copies: dict[int, int] = {}
    texts = [_text(item) for item in items]
    for index, item in enumerate(items):
        key = history.fingerprint(str(item.get("company") or ""), str(item.get("title") or ""))
        if not item.get("title"):
            key = ""            # "acme|" would lump every untitled post at Acme together
        for first in firsts.get(key, []) if key else []:
            a, b = texts[first], texts[index]
            if a == b or (abs(len(a) - len(b)) <= 0.05 * max(len(a), 1)
                          and SequenceMatcher(None, a, b).ratio() >= TWIN_RATIO):
                copies[index] = first
                break
        else:
            if key:
                firsts.setdefault(key, []).append(index)
    return copies


def run_pool(
    items: list[T],
    work: Callable[[T, str], Any],
    workers: int,
    on_done: Callable[[int, Any], None] | None = None,
) -> list[Any]:
    """`work(item, tag)` for every item on `workers` threads. Results come
    back in list order; `on_done(index, result)` runs as each finishes, in
    completion order, for progress lines. An exception in one item stops the
    pool and the rest are cancelled, so `work` should catch what it can live
    with.
    """
    total = len(items)
    workers = max(1, min(workers, total))
    results: list[Any] = [None] * total
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(work, item, f"{i + 1}/{total}"): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            results[index] = result = future.result()
            if on_done is not None:
                on_done(index, result)
    finally:
        # On failure, drop the queued items instead of burning calls on them.
        pool.shutdown(wait=True, cancel_futures=True)
    return results


class Counter:
    """A thread-safe tally for progress lines."""

    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def bump(self) -> int:
        with self._lock:
            self.value += 1
            return self.value
