"""Standalone test for the Indeed Apify actor (kaix/indeed-scraper).

Not wired into the main agent. Run from the project root:

    python test/test_indeed_apify.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apify_test_utils import run_actor, save_items
from src.scrape.apify_jobs import WORKING_INDEED_INPUT

# https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ
INDEED_ACTOR = "BIeK7ZcYUrdxDgOEQ"

# Keep this small while testing to limit Apify cost. Raise toward 200 after the actor looks good.
MAX_ITEMS = 10

INDEED_INPUT = {**WORKING_INDEED_INPUT, "maxItems": MAX_ITEMS}


def main() -> None:
    items = run_actor(INDEED_ACTOR, INDEED_INPUT)
    save_items("indeed_jobs", items)


if __name__ == "__main__":
    main()
