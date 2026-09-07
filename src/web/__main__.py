from __future__ import annotations

import argparse

import uvicorn
from dotenv import load_dotenv


class Server(uvicorn.Server):
    """uvicorn's server, plus: the moment Ctrl+C arrives, tell the open SSE
    streams (run logs, apply chat) to end. They then close inside the
    graceful window, so uvicorn never has to cancel them - which is what
    printed an "Exception in ASGI application" traceback on every stop."""

    def handle_exit(self, sig, frame) -> None:
        from src.web import app as web_app

        web_app.SHUTTING_DOWN.set()
        super().handle_exit(sig, frame)

    async def serve(self, sockets=None) -> None:
        with self.capture_signals():
            await self._serve(sockets)
            # uvicorn re-raises the captured Ctrl+C once serving ends so the
            # process "sees" it. Under asyncio.run that lands on asyncio's own
            # SIGINT handler, which throws KeyboardInterrupt into the still
            # running loop and prints a traceback after a shutdown that
            # already completed cleanly. The shutdown IS the response.
            self._captured_signals.clear()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Opportunity Explorer web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.reload:
            # The reloader runs the app in a child it restarts on file
            # changes; it cannot use our Server subclass. Dev only.
            uvicorn.run(
                "src.web.app:app", host=args.host, port=args.port, reload=True,
                timeout_graceful_shutdown=3,
            )
        else:
            config = uvicorn.Config(
                "src.web.app:app", host=args.host, port=args.port,
                # Backstop only: streams end themselves on Ctrl+C now.
                timeout_graceful_shutdown=3,
            )
            Server(config).run()
    except KeyboardInterrupt:
        # uvicorn re-raises the captured Ctrl+C after a clean shutdown; without
        # this it prints a scary (but harmless) traceback on every stop.
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
