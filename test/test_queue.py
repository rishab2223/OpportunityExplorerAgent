from __future__ import annotations

import unittest

from src.apply import session as apply_session
from src.web import applyqueue


def job(n: int) -> dict:
    return {"stamp": "20260913T120000", "job_id": f"job{n}", "label": f"Company {n}"}


class QueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        applyqueue.reset()
        self.started: list[str] = []

    def tearDown(self) -> None:
        applyqueue.reset()

    def starter(self, stamp: str, job_id: str) -> str:
        self.started.append(job_id)
        return f"label for {job_id}"


class QueueSizeTests(QueueTestCase):
    def test_a_queue_over_the_cap_is_refused(self) -> None:
        too_many = [job(n) for n in range(applyqueue.MAX_QUEUE + 1)]
        with self.assertRaises(applyqueue.QueueFull):
            applyqueue.start(too_many, self.starter)
        self.assertEqual(self.started, [])       # nothing was opened

    def test_an_empty_queue_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            applyqueue.start([], self.starter)
        with self.assertRaises(ValueError):
            applyqueue.start([{"stamp": "", "job_id": ""}], self.starter)

    def test_the_cap_itself_is_allowed(self) -> None:
        applyqueue.start([job(n) for n in range(applyqueue.MAX_QUEUE)], self.starter)
        self.assertEqual(self.started, ["job0"])
        self.assertEqual(len(applyqueue.state()["pending"]), applyqueue.MAX_QUEUE - 1)


class QueueAdvanceTests(QueueTestCase):
    def test_one_job_starts_at_a_time_and_the_next_waits_for_the_browser(self) -> None:
        applyqueue.start([job(1), job(2)], self.starter)
        # Only the first is open: the second must not launch a second Chrome.
        self.assertEqual(self.started, ["job1"])
        self.assertEqual(applyqueue.state()["current"]["job_id"], "job1")

        applyqueue.release("applied")
        self.assertEqual(self.started, ["job1", "job2"])
        self.assertEqual(applyqueue.state()["current"]["job_id"], "job2")

        applyqueue.release("applied")
        self.assertEqual(applyqueue.state()["current"], None)
        self.assertEqual([d["status"] for d in applyqueue.state()["done"]],
                         ["applied", "applied"])
        self.assertFalse(applyqueue.state()["active"])

    def test_the_label_comes_back_from_the_starter(self) -> None:
        applyqueue.start([job(1)], self.starter)
        self.assertEqual(applyqueue.state()["current"]["label"], "label for job1")

    def test_release_without_a_queue_is_a_no_op(self) -> None:
        # A single-row Start apply also ends; the queue must ignore it.
        applyqueue.release("applied")
        self.assertEqual(applyqueue.state()["done"], [])
        self.assertEqual(self.started, [])


class ParkAndAbortTests(QueueTestCase):
    def test_park_leaves_the_job_and_moves_on(self) -> None:
        applyqueue.start([job(1), job(2)], self.starter)
        applyqueue.release("parked")
        self.assertEqual(self.started, ["job1", "job2"])
        state = applyqueue.state()
        self.assertEqual([p["job_id"] for p in state["parked"]], ["job1"])
        self.assertEqual(state["done"], [])      # parked is not an outcome

    def test_abort_stops_the_whole_queue(self) -> None:
        applyqueue.start([job(1), job(2), job(3)], self.starter)
        applyqueue.release("aborted")
        self.assertEqual(self.started, ["job1"])  # job2 and job3 never open
        state = applyqueue.state()
        self.assertEqual(state["current"], None)
        self.assertEqual(state["pending"], [])
        self.assertIn("2 job(s) still queued", state["note"])
        self.assertFalse(state["active"])

    def test_a_failed_job_does_not_stop_the_queue(self) -> None:
        # Only abort stops it. One form that broke should not cost the rest.
        applyqueue.start([job(1), job(2)], self.starter)
        applyqueue.release("failed")
        self.assertEqual(self.started, ["job1", "job2"])


class QueueFailureTests(QueueTestCase):
    def test_a_starter_that_raises_stops_rather_than_churning(self) -> None:
        # Whatever blocked this job (browser busy, bad run folder) almost
        # certainly blocks the next, so the queue stops and says why.
        def broken(stamp: str, job_id: str) -> str:
            raise RuntimeError("a browser is already using that profile")

        applyqueue.start([job(1), job(2), job(3)], broken)
        state = applyqueue.state()
        self.assertEqual(state["current"], None)
        self.assertEqual(state["pending"], [])
        self.assertIn("already using that profile", state["note"])

    def test_clear_drops_the_queue_but_not_the_running_job(self) -> None:
        applyqueue.start([job(1), job(2), job(3)], self.starter)
        state = applyqueue.clear()
        self.assertEqual(state["current"]["job_id"], "job1")   # still running
        self.assertEqual(state["pending"], [])
        applyqueue.release("applied")
        self.assertEqual(self.started, ["job1"])               # nothing followed

    def test_a_second_queue_while_one_runs_is_refused(self) -> None:
        applyqueue.start([job(1)], self.starter)
        with self.assertRaises(ValueError):
            applyqueue.start([job(2)], self.starter)


class ParkCommandTests(unittest.TestCase):
    """The words that end one job versus the words that end everything."""

    def _session(self) -> apply_session.ApplySession:
        return apply_session.ApplySession("20260913T120000", "job1", "Company 1")

    def test_park_raises_parked_not_aborted(self) -> None:
        for word in ("park", "Park", "later", "skip job", "park it"):
            sess = self._session()
            sess.answer(word)
            with self.assertRaises(apply_session.Parked, msg=word):
                sess.ask("anything?")
            # Park is not an abort: the session was never flagged as aborted.
            self.assertFalse(sess.aborted(), word)

    def test_abort_still_aborts(self) -> None:
        for word in ("abort", "stop", "cancel"):
            sess = self._session()
            sess.answer(word)
            with self.assertRaises(apply_session.Aborted, msg=word):
                sess.ask("anything?")
            self.assertTrue(sess.aborted(), word)

    def test_parked_is_not_an_abort_subclass(self) -> None:
        # run_session catches them separately and finishes differently; if
        # Parked inherited from Aborted the wrong branch would win.
        self.assertFalse(issubclass(apply_session.Parked, apply_session.Aborted))

    def test_park_is_terminal_so_the_next_job_may_start(self) -> None:
        self.assertIn("parked", apply_session.TERMINAL_STATUSES)

    def test_a_sentence_containing_park_is_still_an_answer(self) -> None:
        # Only the bare word ends the job: "park" inside a real answer (a
        # location, say) must reach the field like any other text.
        sess = self._session()
        sess.answer("Cyber Park, Gurgaon")
        self.assertEqual(sess.ask("Where do you work?"), "Cyber Park, Gurgaon")
