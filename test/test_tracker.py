"""The Applied page's data (#40) and its messages (#42): stages, timeline,
follow-ups, drafts. A temp database; the model is a stand-in."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src import history, tracker
from src.web import outreach, runs


def job(job_id: str, **extra) -> dict:
    return {"job_id": job_id, "company": f"Co {job_id}", "title": "Backend Engineer", "source": "linkedin",
            "location": "Bengaluru", "apply_url": f"https://example.test/{job_id}",
            "description": "Build Node.js services on AWS.", **extra}


class TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db, self._out = history.DB_PATH, runs.OUTPUT_DIR
        history.DB_PATH = Path(self._tmp.name) / "history.db"
        runs.OUTPUT_DIR = Path(self._tmp.name) / "outputs"

    def tearDown(self) -> None:
        history.DB_PATH, runs.OUTPUT_DIR = self._db, self._out
        self._tmp.cleanup()


class TrackerTests(TempDb):
    def test_every_applied_job_is_listed_at_applied_until_it_moves(self) -> None:
        history.record(job("a"), "applied")
        history.record(job("b"), "skipped")
        apps = tracker.applications()
        self.assertEqual([a["job_id"] for a in apps], ["a"])
        self.assertEqual(apps[0]["stage"], "applied")
        self.assertEqual(tracker.summary(apps)["counts"]["applied"], 1)

    def test_a_stage_change_is_kept_with_its_timeline(self) -> None:
        history.record(job("a"), "applied")
        tracker.set_stage("a", "interview", "round 1 on Friday")
        tracker.set_stage("a", "offer")
        found = tracker.detail("a")
        self.assertEqual(found["stage"], "offer")
        self.assertEqual([e["kind"] for e in found["timeline"]], ["applied", "stage", "stage"])
        self.assertIn("round 1 on Friday", found["timeline"][1]["text"])
        self.assertEqual(tracker.summary([found])["replied"], 1)

    def test_only_applied_jobs_and_known_stages(self) -> None:
        history.record(job("s"), "skipped")
        with self.assertRaises(tracker.NotApplied):
            tracker.set_stage("s", "interview")
        history.record(job("a"), "applied")
        with self.assertRaises(ValueError):
            tracker.set_stage("a", "ghosted")

    def test_ten_quiet_days_suggest_a_follow_up(self) -> None:
        history.record(job("a"), "applied")
        later = datetime.now(timezone.utc) + timedelta(days=tracker.FOLLOW_UP_DAYS + 1)
        self.assertFalse(tracker.applications()[0]["follow_up_due"])
        self.assertTrue(tracker.applications(later)[0]["follow_up_due"])
        tracker.set_stage("a", "rejected")
        self.assertFalse(tracker.applications(later)[0]["follow_up_due"], "a closed application asks nothing")

    def test_mark_sent_sets_the_next_look(self) -> None:
        history.record(job("a"), "applied")
        tracker.save_message("a", "outreach", "Hi Priya, ...")
        follow = tracker.mark_sent("a", "outreach")
        found = tracker.detail("a")
        self.assertTrue(found["messages"]["outreach"]["sent_at"])
        self.assertEqual(found["messages"]["outreach"]["text"], "Hi Priya, ...")
        self.assertEqual(found["follow_up_at"], follow)
        self.assertFalse(found["follow_up_due"])
        self.assertEqual(found["timeline"][-1]["kind"], "sent")
        week = datetime.now(timezone.utc) + timedelta(days=tracker.AFTER_SENT_DAYS, hours=1)
        self.assertTrue(tracker.applications(week)[0]["follow_up_due"])

    def test_contact_and_notes(self) -> None:
        history.record(job("a"), "applied")
        tracker.set_contact("a", "Priya S.", "Engineering Manager", "", "we both studied at NIT")
        tracker.set_notes("a", "second round is system design")
        found = tracker.detail("a")
        self.assertEqual(found["contact"]["relation"], "we both studied at NIT")
        self.assertEqual(found["notes"], "second round is system design")


class MessageTests(TempDb):
    def setUp(self) -> None:
        super().setUp()
        history.record(job("a"), "applied")
        self.prompts: list[str] = []
        patcher = mock.patch.object(outreach, "_candidate",
                                    return_value="CANDIDATE PROFILE:\nfull_name: Test User\n\nNOT EMPLOYMENT: x")
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, draft_text: str):
        def invoke(system, user, schema):
            self.prompts.append(user)
            return schema(draft=draft_text, reply="Wrote it.")
        return invoke

    def test_a_draft_is_written_and_kept(self) -> None:
        out = outreach.draft("a", "outreach", invoke=self.model("Hi [Name], I applied for Backend Engineer."))
        self.assertIn("applied for Backend Engineer", out["draft"])
        self.assertEqual(tracker.get_message("a", "outreach")["text"], out["draft"])
        self.assertIn("claim no connection", self.prompts[0], "no relation given: no connection line")

    def test_the_connection_line_comes_from_the_contact(self) -> None:
        tracker.set_contact("a", "Priya", "EM", "", "we both worked at Acme")
        outreach.draft("a", "referral", invoke=self.model("Hi Priya, ..."))
        self.assertIn("we both worked at Acme", self.prompts[0])

    def test_the_chat_rewrites_the_current_draft(self) -> None:
        tracker.save_message("a", "follow_up", "A long follow-up.")
        out = outreach.chat("a", "follow_up", "make it shorter", invoke=self.model("Short one."))
        self.assertEqual(out["draft"], "Short one.")
        self.assertIn("A long follow-up.", self.prompts[0])
        self.assertIn("make it shorter", self.prompts[0])
        self.assertEqual(tracker.get_message("a", "follow_up")["text"], "Short one.")

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            outreach.draft("a", "cold_call", invoke=self.model("x"))


if __name__ == "__main__":
    unittest.main()
