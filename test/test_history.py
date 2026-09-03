from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from src import history

JOB = {
    "job_id": "indeed:abc123",
    "company": "Acme Corp",
    "title": "Senior Software Engineer",
    "location": "Gurgaon, India",
    "source": "indeed",
    "apply_url": "https://example.com/apply/1",
}


def status_of(entry: dict | None) -> str:
    return entry["status"] if entry else ""


class HistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._original_path
        self._tmp.cleanup()


class FingerprintTests(HistoryTestCase):
    def test_normalizes_case_punctuation_and_spacing(self) -> None:
        self.assertEqual(
            history.fingerprint("  Acme Corp™ ", "Senior Software-Engineer (Node.js)"),
            "acme corp|senior software engineer node js",
        )

    def test_empty_inputs(self) -> None:
        self.assertEqual(history.fingerprint("", ""), "")
        self.assertEqual(history.fingerprint("Acme", ""), "acme|")


class RecordTests(HistoryTestCase):
    def test_record_and_match_by_id(self) -> None:
        history.record(JOB, "applied", stamp="20260830T161516", note="manual")
        by_id, by_fp = history.snapshot()
        self.assertEqual(status_of(by_id.get(JOB["job_id"])), "applied")
        fp = history.fingerprint(JOB["company"], JOB["title"])
        self.assertEqual(status_of(by_fp.get(fp)), "applied")

    def test_match_by_fingerprint_when_id_changed(self) -> None:
        history.record(JOB, "applied")
        by_id, by_fp = history.snapshot()
        reissued_id = "indeed:zzz999"
        self.assertNotIn(reissued_id, by_id)
        self.assertIn(history.fingerprint("Acme Corp", "Senior Software Engineer"), by_fp)

    def test_record_replaces_status(self) -> None:
        history.record(JOB, "skipped")
        history.record(JOB, "applied")
        by_id, _ = history.snapshot()
        self.assertEqual(list(by_id), [JOB["job_id"]])
        self.assertEqual(status_of(by_id[JOB["job_id"]]), "applied")

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            history.record(JOB, "maybe")
        with self.assertRaises(ValueError):
            history.record({"company": "X"}, "applied")  # no job_id


class ReferralTests(HistoryTestCase):
    def test_referral_round_trip_with_contact(self) -> None:
        history.record(JOB, "referral_pending", contact="Priya (ex-colleague)")
        by_id, by_fp = history.snapshot()
        entry = by_id[JOB["job_id"]]
        self.assertEqual(entry["status"], "referral_pending")
        self.assertEqual(entry["contact"], "Priya (ex-colleague)")
        self.assertTrue(entry["marked_at"])
        fp = history.fingerprint(JOB["company"], JOB["title"])
        self.assertEqual(by_fp[fp]["contact"], "Priya (ex-colleague)")

    def test_pending_to_sent_to_cleared(self) -> None:
        history.record(JOB, "referral_pending", contact="Priya")
        history.record(JOB, "referral_sent", contact="Priya")
        by_id, _ = history.snapshot()
        self.assertEqual(by_id[JOB["job_id"]]["status"], "referral_sent")
        self.assertEqual(by_id[JOB["job_id"]]["contact"], "Priya")
        # Referral failed: the row is deleted, the job is eligible again.
        self.assertTrue(history.forget(JOB["job_id"]))
        self.assertEqual(history.snapshot(), ({}, {}))

    def test_referral_statuses_are_valid_and_grouped(self) -> None:
        for status in history.REFERRAL_STATUSES:
            self.assertIn(status, history.VALID_STATUSES)


class MigrationTests(HistoryTestCase):
    def test_pre_contact_database_upgraded_in_place(self) -> None:
        # Build a DB with the old schema (no contact column), as shipped
        # before the referral feature.
        conn = sqlite3.connect(history.DB_PATH)
        conn.executescript(
            """
            CREATE TABLE job_history (
              job_id TEXT PRIMARY KEY, status TEXT NOT NULL, company TEXT,
              title TEXT, location TEXT, source TEXT, apply_url TEXT,
              fingerprint TEXT, stamp TEXT, note TEXT, marked_at TEXT NOT NULL
            );
            INSERT INTO job_history VALUES
              ('old:1', 'applied', 'Acme', 'SWE', '', 'indeed', '', 'acme|swe',
               '', 'manual', '2026-08-30T00:00:00+00:00');
            """
        )
        conn.commit()
        conn.close()

        by_id, _ = history.snapshot()  # must not raise on the missing column
        self.assertEqual(by_id["old:1"]["status"], "applied")
        self.assertEqual(by_id["old:1"]["contact"], "")
        history.record(JOB, "referral_pending", contact="Priya")
        by_id, _ = history.snapshot()
        self.assertEqual(by_id[JOB["job_id"]]["contact"], "Priya")


class ForgetTests(HistoryTestCase):
    def test_forget_round_trip(self) -> None:
        history.record(JOB, "applied")
        self.assertTrue(history.forget(JOB["job_id"]))
        self.assertEqual(history.snapshot(), ({}, {}))
        self.assertFalse(history.forget(JOB["job_id"]))


class SnapshotTests(HistoryTestCase):
    def test_snapshot_filters_by_status(self) -> None:
        history.record(JOB, "applied")
        history.record(dict(JOB, job_id="x:2", company="Beta"), "skipped")
        by_id, by_fp = history.snapshot({"applied"})
        self.assertEqual(set(by_id), {JOB["job_id"]})
        self.assertEqual({e["status"] for e in by_fp.values()}, {"applied"})
        by_id_all, _ = history.snapshot()
        self.assertEqual(len(by_id_all), 2)
        self.assertEqual(history.snapshot(set()), ({}, {}))

    def test_snapshot_on_missing_db(self) -> None:
        self.assertEqual(history.snapshot(), ({}, {}))


class ConcurrencyTests(HistoryTestCase):
    def test_parallel_writes_all_land(self) -> None:
        def write(offset: int) -> None:
            for i in range(25):
                history.record(dict(JOB, job_id=f"t:{offset}:{i}"), "applied")

        threads = [threading.Thread(target=write, args=(n,)) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        by_id, _ = history.snapshot()
        self.assertEqual(len(by_id), 50)


class DropSeenTests(HistoryTestCase):
    def _jobs(self):
        from src.models import JobPosting

        applied = JobPosting(source="indeed", job_id="indeed:abc123",
                             company="Acme Corp", title="Senior Software Engineer")
        reissued = JobPosting(source="indeed", job_id="indeed:new999",
                              company="Acme Corp", title="Senior Software Engineer")
        skipped = JobPosting(source="indeed", job_id="indeed:skip1",
                             company="Beta", title="Backend Developer")
        fresh = JobPosting(source="indeed", job_id="indeed:fresh",
                           company="Gamma", title="Platform Engineer")
        return applied, reissued, skipped, fresh

    def test_drop_seen_filters_applied_only(self) -> None:
        from src.agent.nodes.scrape import _drop_seen
        from src.config import AppConfig

        cfg = AppConfig()
        applied, reissued, skipped, fresh = self._jobs()
        history.record(applied.model_dump(), "applied")
        history.record(skipped.model_dump(), "skipped")

        kept = _drop_seen([applied, reissued, skipped, fresh], cfg)
        # applied dropped by id, reissued dropped by fingerprint,
        # skipped kept (non-blocking by default), fresh kept.
        self.assertEqual([j.job_id for j in kept], ["indeed:skip1", "indeed:fresh"])

        cfg.history.match_similar = False
        kept = _drop_seen([applied, reissued, skipped, fresh], cfg)
        self.assertEqual(
            [j.job_id for j in kept], ["indeed:new999", "indeed:skip1", "indeed:fresh"]
        )

    def test_drop_seen_blocks_referrals(self) -> None:
        from src.agent.nodes.scrape import _drop_seen
        from src.config import AppConfig
        from src.models import JobPosting

        cfg = AppConfig()
        pending = JobPosting(source="indeed", job_id="indeed:ref1",
                             company="Acme Corp", title="Senior Software Engineer")
        sent = JobPosting(source="indeed", job_id="indeed:ref2",
                          company="Beta", title="Backend Developer")
        fresh = JobPosting(source="indeed", job_id="indeed:fresh",
                           company="Gamma", title="Platform Engineer")
        history.record(pending.model_dump(), "referral_pending", contact="Priya")
        history.record(sent.model_dump(), "referral_sent")

        kept = _drop_seen([pending, sent, fresh], cfg)
        self.assertEqual([j.job_id for j in kept], ["indeed:fresh"])

        cfg.history.skip_referral = False
        kept = _drop_seen([pending, sent, fresh], cfg)
        self.assertEqual(
            [j.job_id for j in kept], ["indeed:ref1", "indeed:ref2", "indeed:fresh"]
        )
