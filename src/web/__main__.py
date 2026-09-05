from __future__ import annotations

import argparse

import uvicorn
from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Opportunity Explorer web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    # The UI holds SSE streams open (run logs, apply chat); without a graceful-
    # shutdown timeout, Ctrl+C waits forever for those connections to close.
    uvicorn.run(
        "src.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        timeout_graceful_shutdown=3,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
