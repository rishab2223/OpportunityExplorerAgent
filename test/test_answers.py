from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import answers, history


class BankTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._original_path
        self._tmp.cleanup()


class QuestionKeyTests(BankTestCase):
    def test_phrasings_collapse_to_one_topic(self) -> None:
        for phrasing in ("Notice period", "What is your notice period?",
                         "Notice Period (in days)", "notice"):
            self.assertEqual(answers.question_key(phrasing), "notice_period", phrasing)

    def test_work_authorization_variants(self) -> None:
        for phrasing in ("Are you legally authorized to work in India?",
                         "Are you authorised to work in the United States?",
                         "Work authorization status"):
            self.assertEqual(answers.question_key(phrasing), "work_authorization", phrasing)

    def test_group_carries_the_question_for_bare_options(self) -> None:
        # A radio labelled just "Yes" keys on its group text.
        self.assertEqual(
            answers.question_key("Yes", "Do you require visa sponsorship?"),
            "sponsorship",
        )

    def test_unknown_question_falls_back_to_normalized_text(self) -> None:
        self.assertEqual(
            answers.question_key("Favourite programming language?"),
            "favourite programming language",
        )

    def test_empty(self) -> None:
        self.assertEqual(answers.question_key(""), "")


class ClassifyTests(BankTestCase):
    def test_sensitive_topics(self) -> None:
        self.assertEqual(answers.classify("Do you require sponsorship?"), "sensitive")
        self.assertEqual(answers.classify("Gender"), "sensitive")
        self.assertEqual(answers.classify("Yes", "Any criminal convictions?"), "sensitive")

    def test_secret_wins(self) -> None:
        self.assertEqual(answers.classify("Enter the OTP"), "secret")
        self.assertEqual(answers.classify("Password"), "secret")

    def test_neutral(self) -> None:
        self.assertEqual(answers.classify("Notice period"), "neutral")
        self.assertEqual(answers.classify("Favourite language"), "neutral")


class RememberRecallTests(BankTestCase):
    def test_round_trip_across_phrasings(self) -> None:
        answers.remember("What is your notice period?", "60 days")
        entry = answers.recall("Notice Period (in days)")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["answer"], "60 days")
        self.assertEqual(entry["kind"], "neutral")

    def test_sensitive_kind_recorded(self) -> None:
        answers.remember("Do you require visa sponsorship?", "No")
        entry = answers.recall("Will you need sponsorship?")
        self.assertEqual(entry["kind"], "sensitive")

    def test_secrets_never_stored(self) -> None:
        self.assertEqual(answers.remember("Enter the OTP", "123456"), "")
        self.assertEqual(answers.remember("Notice period", "the password is hunter2"), "")
        self.assertIsNone(answers.recall("Enter the OTP"))

    def test_company_specific_never_stored(self) -> None:
        self.assertEqual(
            answers.remember("Why do you want to work at Capgemini?",
                             "Because...", company="Capgemini"),
            "",
        )
        # Same question without the company mention is fine.
        self.assertNotEqual(
            answers.remember("Why this role?", "Because...", company="Capgemini"), ""
        )

    def test_recall_miss_and_forget(self) -> None:
        self.assertIsNone(answers.recall("Never asked"))
        key = answers.remember("Notice period", "60 days")
        self.assertTrue(answers.forget(key))
        self.assertIsNone(answers.recall("Notice period"))
        self.assertFalse(answers.forget(key))

    def test_usage_counter_orders_entries(self) -> None:
        answers.remember("Notice period", "60 days")
        answers.remember("GitHub profile", "https://github.com/x")
        for _ in range(3):
            answers.recall("Notice period")
        rows = answers.entries()
        self.assertEqual(rows[0]["question_key"], "notice_period")
        self.assertEqual(len(rows), 2)


class MigrationTests(BankTestCase):
    def test_learned_map_migrates_once(self) -> None:
        learned = {"notice period": "45 days", "github": "https://github.com/x", "": "junk"}
        self.assertEqual(answers.migrate_learned(learned), 2)
        self.assertEqual(answers.recall("Notice Period")["answer"], "45 days")
        # Idempotent, and never overwrites a newer bank entry.
        answers.remember("notice period", "30 days")
        self.assertEqual(answers.migrate_learned(learned), 0)
        self.assertEqual(answers.recall("notice period")["answer"], "30 days")
