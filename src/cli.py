from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agent.graph import build_graph, initial_state
from src.config import load_env, load_yaml_config


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Autonomous job-matching agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="Run scrape → score → enrich matches → JSON dump")
    run_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config/settings.yaml",
    )
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.config)
    return 1


def cmd_run(config_path: Path | None) -> int:
    cfg = load_yaml_config(config_path)
    env = load_env()
    graph = build_graph(cfg, env)
    result = graph.invoke(initial_state(cfg, env))
    n = len(result.get("matches") or [])
    run_path = result.get("run_output_path") or ""
    short_path = result.get("shortlisted_path") or ""
    if result.get("failed_step"):
        print(
            f"FAILED at {result.get('failed_step')}: {result.get('error_message')}",
            file=sys.stderr,
        )
        if run_path:
            print(f"Run output: {run_path}", file=sys.stderr)
        return 1
    print(f"OK: {n} match(es)")
    if run_path:
        print(f"Run output: {run_path}")
    if short_path:
        print(f"Shortlisted: {short_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
