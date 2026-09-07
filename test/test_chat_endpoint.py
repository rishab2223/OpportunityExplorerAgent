from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from src.apply import session as apply_session
from src.web.app import app


class ChatWhileBusyTests(unittest.TestCase):
    """Text sent while the worker is not waiting must be refused: it would be
    consumed as the answer to the NEXT question, typed into a field and
    possibly saved to the answer bank."""

    def setUp(self) -> None:
        self.sess = apply_session.start("20260101T000000", "test:chat", "X")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        # Leave no live session behind for other tests.
        self.sess.finish("aborted", "test teardown")

    def test_busy_session_refuses_chat(self) -> None:
        self.sess.status = "running"
        resp = self.client.post(f"/api/apply/{self.sess.id}/chat", json={"text": "hello"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("busy", resp.json()["detail"])
        self.assertTrue(self.sess._answers.empty())

    def test_waiting_session_accepts_chat(self) -> None:
        self.sess.status = "waiting_for_user"
        resp = self.client.post(f"/api/apply/{self.sess.id}/chat", json={"text": "6 years"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sess._answers.get_nowait(), "6 years")

    def test_replay_downgrades_answered_prompts(self) -> None:
        # A reconnecting page must not reopen an answered modal or re-arm an
        # answered question; only the live pending prompt keeps its type.
        self.sess.emit("choice", "Which resume?", kind="resume", meta={})
        self.sess.emit("answer", "tailored")
        self.sess.emit("question", "Notice period?")
        _, backlog = self.sess.subscribe()
        from src.web.app import _apply_stream

        stream = _apply_stream(self.sess.id)
        first = next(stream)
        second = next(stream)
        third = next(stream)
        self.assertIn('"type": "history"', first)
        self.assertIn('"type": "answer"', second)
        self.assertIn('"type": "question"', third)
        stream.close()
        self.assertEqual(len(backlog), 3)


if __name__ == "__main__":
    unittest.main()
