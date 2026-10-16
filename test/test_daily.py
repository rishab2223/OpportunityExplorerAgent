"""The once-a-day batch (#38): the properties table, when it is due, which
waiting jobs get looked at, and what a look records."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src import daily, history, open_check, properties
from src.config import AppConfig
from src.web import runs


class TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db, self._out = history.DB_PATH, runs.OUTPUT_DIR
        history.DB_PATH = Path(self._tmp.name) / "history.db"
        runs.OUTPUT_DIR = Path(self._tmp.name) / "outputs"

    def tearDown(self) -> None:
        history.DB_PATH, runs.OUTPUT_DIR = self._db, self._out
        self._tmp.cleanup()

    def make_run(self, stamp: str, jobs: list[dict]) -> None:
        folder = runs.OUTPUT_DIR / stamp
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        (folder / "shortlisted.json").write_text(json.dumps(jobs), encoding="utf-8")


def job(job_id: str, **extra) -> dict:
    return {"source": "linkedin", "job_id": job_id, "company": f"Co {job_id}", "title": f"Engineer {job_id}",
            "listing_url": f"https://www.linkedin.com/jobs/view/{job_id}", "relevance": 80, **extra}


def stamp_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%dT%H%M%S")


class PropertiesTests(TempDb):
    def test_a_value_is_kept_and_replaced(self) -> None:
        self.assertIsNone(properties.get("daily_batch_done"))
        properties.put("daily_batch_done", "2026-10-15")
        properties.put("daily_batch_done", "2026-10-16")
        self.assertEqual(properties.get("daily_batch_done"), "2026-10-16")
        self.assertIn("daily_batch_done", properties.all_values())


class DueTests(unittest.TestCase):
    def test_runs_once_after_its_time_each_day(self) -> None:
        morning, noon = datetime(2026, 10, 15, 9, 0), datetime(2026, 10, 15, 11, 30)
        self.assertFalse(daily.is_due(morning, "11:30", None))
        self.assertTrue(daily.is_due(noon, "11:30", None))
        self.assertTrue(daily.is_due(datetime(2026, 10, 15, 18, 0), "11:30", "2026-10-14"),
                        "a server started late in the day still runs it")
        self.assertFalse(daily.is_due(noon, "11:30", "2026-10-15"), "never twice a day")


class WhichJobsTests(TempDb):
    def test_every_two_days_while_it_waits(self) -> None:
        now = datetime.now(timezone.utc)
        jobs = [dict(job("new"), stamp=stamp_days_ago(1)),
                dict(job("old"), stamp=stamp_days_ago(3)),
                dict(job("seen"), stamp=stamp_days_ago(5)),
                dict(job("stale"), stamp=stamp_days_ago(5)),
                dict(job("asked"), stamp=stamp_days_ago(5))]
        checks = {"seen": {"checked_at": (now - timedelta(days=1)).isoformat(), "result": "open"},
                  "stale": {"checked_at": (now - timedelta(days=2, hours=1)).isoformat(), "result": "open"},
                  "asked": {"checked_at": (now - timedelta(days=4)).isoformat(), "result": "looks_closed"}}
        picked = [j["job_id"] for j in open_check.due(jobs, checks, 2, now)]
        # Never looked at first, then the longest unchecked.
        self.assertEqual(picked, ["old", "stale"])

    def test_only_jobs_nothing_was_done_with(self) -> None:
        self.make_run(stamp_days_ago(3), [job("waiting"), job("applied"), job("decided")])
        history.record(job("applied"), "applied")
        stamp = runs.list_stamps()[0]
        runs.save_decision(stamp, "decided", decision="no")
        self.assertEqual([j["job_id"] for j in open_check.waiting_jobs()], ["waiting"])

    def test_where_to_look(self) -> None:
        self.assertIn("linkedin.com", open_check.address(job("a", apply_url="https://acme.wd1.myworkdayjobs.com/x")))
        self.assertEqual(open_check.address({"listing_url": "https://in.indeed.com/viewjob?jk=1",
                                             "apply_url": "https://careers.acme.com/job/1"}),
                         "https://careers.acme.com/job/1")
        self.assertEqual(open_check.address({"listing_url": "https://in.indeed.com/viewjob?jk=1",
                                             "apply_url": "https://in.indeed.com/apply/1"}),
                         "https://in.indeed.com/viewjob?jk=1")


class RunTests(TempDb):
    def test_closed_goes_to_history_and_looks_closed_waits_for_you(self) -> None:
        self.make_run(stamp_days_ago(3), [job("dead"), job("maybe"), job("alive")])
        answers = {"dead": ("closed", "LinkedIn says so"), "maybe": ("looks_closed", "404"),
                   "alive": ("open", "")}
        lines: list[str] = []
        with mock.patch.object(open_check, "read_page",
                               side_effect=lambda _page, url: answers[url.rsplit("/", 1)[1]]):
            counts = open_check.run(2, 60, 1.0, log=lines.append, opener=lambda: object())
        self.assertEqual(counts, {"open": 1, "closed": 1, "looks_closed": 1, "unknown": 0})
        self.assertEqual((history.lookup("dead") or {}).get("status"), "closed")
        self.assertIsNone(history.lookup("maybe"), "not recorded: yours to confirm")
        rows = {r["job_id"]: r for r in runs.load_jobs(runs.list_stamps()[0])}
        self.assertEqual(rows["maybe"]["open_check"], "looks_closed")
        self.assertTrue(any("Looks closed" in line for line in lines), lines)
        with mock.patch.object(open_check, "read_page") as again:
            open_check.run(2, 60, 1.0, log=lines.append, opener=lambda: object())
        again.assert_not_called()   # looked at today: not due for two days

    def test_the_cap_leaves_the_rest_for_tomorrow(self) -> None:
        self.make_run(stamp_days_ago(3), [job(str(n)) for n in range(5)])
        lines: list[str] = []
        with mock.patch.object(open_check, "read_page", return_value=("open", "")) as read:
            open_check.run(2, 2, 1.0, log=lines.append, opener=lambda: object())
        self.assertEqual(read.call_count, 2)
        self.assertTrue(any("3 more tomorrow" in line for line in lines), lines)


class BatchTests(TempDb):
    def test_one_search_a_day_even_across_restarts(self) -> None:
        cfg = AppConfig()
        started: list[dict] = []

        def fake_start(overrides=None, remember=False):
            started.append(overrides)
            return "20261015T113000"

        now = datetime(2026, 10, 15, 11, 30)
        with mock.patch.object(open_check, "run", return_value={"open": 0, "closed": 0,
                                                                "looks_closed": 0, "unknown": 0}), \
                mock.patch("src.web.runner.start", side_effect=fake_start):
            daily.run_batch(cfg, now, log=lambda _l: None)
            properties.put(daily.LAST_DONE, "")          # as if stopped before it finished
            daily.run_batch(cfg, now, log=lambda _l: None)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["scrape"]["posted_within"], "24h")
        self.assertEqual(properties.get(daily.LAST_DONE), "2026-10-15")

    def test_a_run_already_going_is_not_started_twice(self) -> None:
        from src.web import runner

        with mock.patch.object(open_check, "run", return_value={"open": 0, "closed": 0,
                                                                "looks_closed": 0, "unknown": 0}), \
                mock.patch.object(runner, "start", side_effect=runner.RunBusy("run x is already in progress")):
            summary = daily.run_batch(AppConfig(), datetime(2026, 10, 15, 12, 0), log=lambda _l: None)
        self.assertIn("search skipped", summary)
        self.assertEqual(properties.get(daily.SEARCH_STARTED), "2026-10-15")


if __name__ == "__main__":
    unittest.main()
