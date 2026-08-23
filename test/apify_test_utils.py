"""Shared helpers for standalone Apify actor tests. Not used by the main pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def load_apify_token() -> str:
    load_dotenv(ROOT / ".env")
    token = (os.getenv("APIFY_TOKEN") or "").strip()
    if not token:
        raise SystemExit("APIFY_TOKEN is not set in .env")
    return token


def _run_field(run: object, camel: str, snake: str):
    if run is None:
        return None
    if isinstance(run, dict):
        return run.get(camel) or run.get(snake)
    return getattr(run, snake, None) or getattr(run, camel, None)


def run_actor(actor_id: str, run_input: dict) -> list[dict]:
    client = ApifyClient(load_apify_token())
    print(f"Starting actor {actor_id}")
    print(f"Input: {json.dumps(run_input, indent=2)}")
    run = client.actor(actor_id).call(run_input=run_input)
    dataset_id = _run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise SystemExit(f"Actor {actor_id} returned no dataset")
    status = _run_field(run, "status", "status")
    print(f"Run status={status} dataset={dataset_id}")
    items = [i for i in client.dataset(str(dataset_id)).iterate_items() if isinstance(i, dict)]
    return items


def save_items(name: str, items: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.json"
    path.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")
    print(f"Saved {len(items)} item(s) to {path}")
    if items:
        print("First item keys:", sorted(items[0].keys()))
        preview = {k: items[0].get(k) for k in list(items[0])[:12]}
        print("First item preview:", json.dumps(preview, indent=2, default=str)[:2000])
    return path
