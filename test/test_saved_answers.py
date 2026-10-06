from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from src import answers, history
from src.config import AppConfig
from src.web import held_insight, runs


class TempDataTestCase(unittest.TestCase):
    """A temporary history DB (which holds the answer bank) and outputs
    folder: never the candidate's real ones."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (history.DB_PATH, runs.OUTPUT_DIR)
        history.DB_PATH = root / "history.db"
        runs.OUTPUT_DIR = root / "outputs"
        answers._invalidate()

    def tearDown(self) -> None:
        history.DB_PATH, runs.OUTPUT_DIR = self._saved
        answers._invalidate()
        self._tmp.cleanup()


class SavedAnswerTests(TempDataTestCase):
    def test_listed_with_their_usage(self) -> None:
        answers.remember("Notice period", "Immediate")
        answers.remember("How did you hear about us?", "LinkedIn")
        listed = {e["question_key"]: e for e in answers.all_entries()}
        self.assertEqual(listed["notice_period"]["answer"], "Immediate")
        self.assertIn("times_used", listed["notice_period"])

    def test_an_edit_is_what_the_next_form_gets(self) -> None:
        answers.remember("Notice period", "30 days")
        self.assertEqual(answers.recall("Notice period")["answer"], "30 days")   # warms the cache
        self.assertEqual(answers.update("notice_period", "Immediate"), "")
        self.assertEqual(answers.recall("What is your notice period?")["answer"], "Immediate")

    def test_an_edit_cannot_store_a_secret_or_nothing(self) -> None:
        answers.remember("Notice period", "30 days")
        self.assertIn("never stored", answers.update("notice_period", "my password is x"))
        self.assertIn("delete it instead", answers.update("notice_period", "  "))
        self.assertEqual(answers.lookup("notice_period")["answer"], "30 days")

    def test_editing_a_missing_answer(self) -> None:
        self.assertIn("no saved answer", answers.update("nope", "x"))

    def test_citizenship_is_a_declaration_and_a_second_one_is_its_own(self) -> None:
        # Ericsson on SuccessFactors (Oct 2026): "Citizenship 1" and "2".
        self.assertEqual(answers.remember("Citizenship 1:", "Indian", "Citizenship 1:*"), "citizenship")
        self.assertEqual(answers.lookup("citizenship")["kind"], "sensitive")
        self.assertEqual(answers.recall("Nationality")["answer"], "Indian")
        self.assertIsNone(answers.recall("Citizenship 2:"))     # never filled with the first's
        self.assertEqual(answers.classify("Country of citizenship"), "sensitive")

    def test_a_conflict_of_interest_answer_is_a_declaration(self) -> None:
        q = "Do you have a potential Conflict of Interest as described above? (Yes or No)"
        self.assertEqual(answers.remember(q, "No"), "conflict_of_interest")
        self.assertEqual(answers.lookup("conflict_of_interest")["kind"], "sensitive")

    def test_marital_status_and_a_driving_licence_held(self) -> None:
        self.assertEqual(answers.remember("Marital Status *", "Single"), "marital_status")
        self.assertEqual(answers.lookup("marital_status")["kind"], "sensitive")
        self.assertEqual(answers.remember("driving_license", "Yes"), "driving_licence_held")
        self.assertEqual(answers.recall("Do you have a driving licence?")["answer"], "Yes")
        # Its number is an identifier, and is never kept.
        self.assertEqual(answers.remember("Driving licence number", "DL-0420"), "")
        self.assertEqual(answers.remember("Passport Number", "Z1234567"), "")

    def test_delete_means_the_agent_asks_again(self) -> None:
        answers.remember("Notice period", "30 days")
        self.assertIsNotNone(answers.recall("Notice period"))
        self.assertTrue(answers.forget("notice_period"))
        self.assertIsNone(answers.recall("Notice period"))


class HeldInsightTests(TempDataTestCase):
    STAMP = "20261002T100000"

    def _run(self, held: list[tuple[str, str]], decisions: dict[str, str]) -> None:
        directory = runs.OUTPUT_DIR / self.STAMP
        directory.mkdir(parents=True)
        (directory / "run.json").write_text("{}", encoding="utf-8")
        (directory / "held_back.json").write_text(json.dumps([
            {"source": "indeed", "job_id": job_id, "company": f"Co {job_id}", "title": "Engineer",
             "held_back": reason} for job_id, reason in held]), encoding="utf-8")
        (directory / "applications.json").write_text(json.dumps(
            {job_id: {"decision": d} for job_id, d in decisions.items()}), encoding="utf-8")

    def _summary(self, mode: str = "review", tolerance: float = 1, years: str = "6") -> dict:
        cfg = AppConfig.model_validate({"experience": {
            "enabled": True, "mode": mode, "review_min_score": 9, "tolerance_years": tolerance}})
        with unittest.mock.patch.object(held_insight.run_options, "effective_config", return_value=cfg), \
             unittest.mock.patch.object(held_insight.profile, "load_profile",
                                        return_value={"total_experience_years": years}):
            return held_insight.summary()

    def test_keeping_most_suggests_a_looser_tolerance(self) -> None:
        self._run([("a", "asks for 8+ years; your limit is 7"), ("b", "asks for 9+ years; your limit is 7"),
                   ("c", "asks for 8+ years; your limit is 7"), ("d", "asks for 8+ years; your limit is 7")],
                  {"a": "yes", "b": "yes", "c": "yes", "d": "no"})
        result = self._summary()
        self.assertEqual((result["kept"], result["skipped"]), (3, 1))
        # 9+ was kept at 6 years: a tolerance of 3 would have let it through.
        self.assertIn("tolerance_years: 3", result["suggestion"])
        self.assertIn("now 1", result["suggestion"])

    def test_history_counts_as_a_decision(self) -> None:
        self._run([(i, "asks for 8+ years; your limit is 7") for i in "abc"], {})
        for job_id in "abc":
            history.record({"job_id": job_id, "company": f"Co {job_id}", "title": "Engineer",
                            "source": "indeed"}, "applied")
        self.assertIn("tolerance_years: 2", self._summary()["suggestion"])

    def test_too_few_decisions_say_nothing(self) -> None:
        self._run([("a", "asks for 8+ years; your limit is 7"), ("b", "asks for 8+ years; your limit is 7")],
                  {"a": "yes", "b": "yes"})
        self.assertEqual(self._summary()["suggestion"], "")

    def test_a_tolerance_already_loose_enough_says_nothing(self) -> None:
        self._run([(i, "asks for 8+ years; your limit is 9") for i in "abc"], {i: "yes" for i in "abc"})
        self.assertEqual(self._summary(tolerance=3)["suggestion"], "")

    def test_skipping_everything_suggests_drop(self) -> None:
        self._run([(i, "asks for 8+ years; your limit is 7") for i in "abcde"], {i: "no" for i in "abcde"})
        self.assertIn("mode: drop", self._summary()["suggestion"])

    def test_closed_says_nothing_about_the_rule(self) -> None:
        self._run([(i, "asks for 8+ years; your limit is 7") for i in "abcde"], {})
        for job_id in "abcde":
            history.record({"job_id": job_id, "company": f"Co {job_id}", "title": "Engineer",
                            "source": "indeed"}, "closed")
        self.assertEqual(self._summary(), {"kept": 0, "skipped": 0, "suggestion": ""})

    def test_drop_mode_gets_no_suggestion(self) -> None:
        self._run([(i, "asks for 8+ years; your limit is 7") for i in "abc"], {i: "yes" for i in "abc"})
        self.assertEqual(self._summary(mode="drop")["suggestion"], "")


if __name__ == "__main__":
    unittest.main()
