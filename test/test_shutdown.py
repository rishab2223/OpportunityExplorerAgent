from __future__ import annotations

import http.client
import logging
import signal
import socket
import threading
import time
import unittest

import uvicorn

from src.apply import session as apply_session
from src.web import app as web_app
from src.web.__main__ import Server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


class CtrlCWithOpenStreamTests(unittest.TestCase):
    """Ctrl+C with the dashboard's SSE stream open used to print an
    'Exception in ASGI application' CancelledError traceback: the stream
    generator sat in a 15s queue wait past uvicorn's 3s graceful window.
    Now the Ctrl+C handler tells streams to end, and the stop is clean."""

    def setUp(self) -> None:
        web_app.SHUTTING_DOWN.clear()
        self.sess = apply_session.start("20260101T000000", "test:shutdown", "X")
        self.sess.status = "waiting_for_user"
        self.port = _free_port()
        self.capture = _Capture()
        logging.getLogger("uvicorn.error").addHandler(self.capture)

    def tearDown(self) -> None:
        logging.getLogger("uvicorn.error").removeHandler(self.capture)
        web_app.SHUTTING_DOWN.clear()
        self.sess.finish("aborted", "test teardown")

    def test_stop_is_clean_with_a_live_stream(self) -> None:
        server = Server(uvicorn.Config(
            web_app.app, host="127.0.0.1", port=self.port,
            timeout_graceful_shutdown=3, log_level="info",
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(server.started, "server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/api/apply/{self.sess.id}/events")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        # The stream is live: nothing more will arrive until we stop.
        started = time.monotonic()
        server.handle_exit(signal.SIGINT, None)   # what Ctrl+C does
        thread.join(timeout=6)
        elapsed = time.monotonic() - started
        conn.close()

        self.assertFalse(thread.is_alive(), "server did not stop")
        self.assertLess(elapsed, 2.5, f"stop took {elapsed:.1f}s: the stream was cancelled, not ended")
        joined = "\n".join(self.capture.records)
        self.assertNotIn("Exception in ASGI application", joined)
        self.assertNotIn("timeout graceful shutdown exceeded", joined)

    def test_next_event_returns_stop_promptly(self) -> None:
        from queue import Queue

        queue: Queue = Queue()
        web_app.SHUTTING_DOWN.set()
        started = time.monotonic()
        self.assertIs(web_app._next_event(queue), web_app._STOP)
        self.assertLess(time.monotonic() - started, 0.5)



class CapturedSignalNotReraisedTests(unittest.TestCase):
    def test_serve_swallows_the_captured_ctrl_c(self) -> None:
        # uvicorn re-raises the captured SIGINT after serving; under
        # asyncio.run that became a KeyboardInterrupt traceback at the end of
        # every clean stop. Our serve() clears it. Main thread, real signals.
        import asyncio

        server = Server(uvicorn.Config(web_app.app, host="127.0.0.1", port=_free_port()))

        async def fake_serve(sockets=None):
            server.handle_exit(signal.SIGINT, None)   # what Ctrl+C does mid-serve

        server._serve = fake_serve
        try:
            asyncio.run(server.serve())
        except KeyboardInterrupt:
            self.fail("the captured Ctrl+C was re-raised after a clean shutdown")
        finally:
            web_app.SHUTTING_DOWN.clear()
        self.assertEqual(server._captured_signals, [])

if __name__ == "__main__":
    unittest.main()
