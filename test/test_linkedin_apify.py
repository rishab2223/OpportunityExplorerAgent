"""Standalone test for the LinkedIn Apify actor (dataji/apify-linkdin-jobs).

Not wired into the main agent. Run from the project root:

    python test/test_linkedin_apify.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apify_test_utils import run_actor, save_items
from src.scrape.apify_jobs import WORKING_LINKEDIN_INPUT

# https://console.apify.com/actors/d1gs0RHIwEnsan7XX
LINKEDIN_ACTOR = "d1gs0RHIwEnsan7XX"

# Keep this small while testing to limit Apify cost.
MAX_RESULTS = 10

LINKEDIN_INPUT = {**WORKING_LINKEDIN_INPUT, "maxResults": MAX_RESULTS}


def main() -> None:
    items = run_actor(LINKEDIN_ACTOR, LINKEDIN_INPUT)
    save_items("linkedin_jobs", items)


if __name__ == "__main__":
    main()
