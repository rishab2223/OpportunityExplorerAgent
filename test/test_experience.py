from __future__ import annotations

import unittest
import unittest.mock

from src.agent.nodes import scrape
from src.config import AppConfig
from src.experience import candidate_years, required_years
from src.models import JobPosting


class RequiredYearsTests(unittest.TestCase):
    """Phrasings taken from real postings in earlier runs."""

    def test_general_requirements_are_read(self) -> None:
        cases = {
            "8+ years of professional software engineering experience building backend": 8,
            "BTECH/BE with 8+ years of relevant experience.": 8,
            "Requirements 8 - 10years of hands-on full stack development experience": 8,
            "Years of Experience 8–12 years of professional software development": 8,
            "Minimum 4–5 years of relevant software-development experience": 4,
            "Location- Pune Experience- 8+ years Bachelor's degree in Computer Science": 8,
            "Requirements: 6 years (Minimum experience required) Strong proficiency": 6,
            "Minimum 5 Year(s) Of Experience Is Required Educational Qualification": 5,
            "at least 2 years experience or equivalent work experience": 2,
            "9+ years of experience in developing highly scalable cloud-native apps": 9,
            "✔️ 8–14 years of strong software engineering experience ✔️": 8,
            "Minimum five years of software engineering experience.": 5,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(required_years(text), want)

    def test_skill_lines_do_not_set_the_bar(self) -> None:
        # Taking these as the requirement would drop jobs on one technology.
        for text in (
            "3+ years experience in React , typescript , Java functional language.",
            "2+ years experience in microservices architecture, messaging patterns",
            "Minimum 4 - 5 years of hands-on experience with MEAN or MERN stack",
            "10+ years of Kafka experience",
        ):
            with self.subTest(text=text):
                self.assertIsNone(required_years(text))

    def test_the_company_and_schooling_are_not_experience(self) -> None:
        for text in (
            "Here we are 25 years later, having pioneered an industry.",
            "Educational Qualification : 15 years full time education , Summary: As an Application Developer",
            "Over the past ten years, we have built a reputation for strong engineering practice",
            "In operation for over 33 years, the centre is critical",
        ):
            with self.subTest(text=text):
                self.assertIsNone(required_years(text))

    def test_the_largest_general_requirement_wins(self) -> None:
        text = ("Bachelor's degree. 7–10 years of full-stack development experience. "
                "3+ years of experience with AWS.")
        self.assertEqual(required_years(text), 7)

    def test_a_fresher_range_sets_no_bar(self) -> None:
        self.assertIsNone(required_years("*Experience:* 0–3 years *Job Type:* Full-time"))

    def test_no_description(self) -> None:
        self.assertIsNone(required_years(""))


class CandidateYearsTests(unittest.TestCase):
    def test_the_shapes_a_profile_holds(self) -> None:
        self.assertEqual(candidate_years("6"), 6)
        self.assertEqual(candidate_years("6.5"), 6.5)
        self.assertEqual(candidate_years("6+"), 6)
        self.assertEqual(candidate_years("6 years"), 6)
        self.assertAlmostEqual(candidate_years("6 years 6 months"), 6.5)

    def test_nothing_to_read(self) -> None:
        for value in ("", None, "six-ish", "n/a"):
            with self.subTest(value=value):
                self.assertIsNone(candidate_years(value))


def _job(job_id: str, description: str) -> JobPosting:
    return JobPosting(source="indeed", job_id=job_id, title="Engineer",
                      company=f"Co {job_id}", description=description)


class ExperienceFilterTests(unittest.TestCase):
    JOBS = [
        _job("seven", "7+ years of software development experience"),
        _job("eight", "8+ years of professional software engineering experience"),
        _job("unstated", "Strong Node.js skills."),
    ]

    def _run(self, years: str = "6.5", **experience) -> list[JobPosting]:
        # A stand-in profile: never the candidate's real file.
        cfg = AppConfig.model_validate({"experience": experience})
        with unittest.mock.patch.object(scrape.profile, "load_profile",
                                        return_value={"total_experience_years": years}):
            return scrape._drop_over_experienced(self.JOBS, cfg)

    def _kept(self, years: str = "6.5", **experience) -> list[str]:
        return [j.job_id for j in self._run(years, **experience)]

    def test_off_unless_settings_turn_it_on(self) -> None:
        self.assertFalse(AppConfig().experience.enabled)
        self.assertEqual(self._kept(), ["seven", "eight", "unstated"])

    def test_an_empty_profile_value_filters_nothing(self) -> None:
        # Never inferred from the resume: an empty value means no filtering.
        self.assertEqual(self._kept(years="", enabled=True, mode="drop"),
                         ["seven", "eight", "unstated"])

    def test_strict_drops_anything_above(self) -> None:
        self.assertEqual(self._kept(enabled=True, mode="drop"), ["unstated"])

    def test_tolerance_keeps_a_stretch(self) -> None:
        self.assertEqual(self._kept(enabled=True, mode="drop", tolerance_years=1),
                         ["seven", "unstated"])

    def test_the_profile_is_the_only_source_of_years(self) -> None:
        # settings.yaml held its own copy once; a stale one would let the
        # filter and the application forms disagree about the candidate.
        self.assertNotIn("candidate_years", AppConfig().experience.model_dump())
        self.assertEqual(self._kept(years="8", enabled=True, mode="drop"),
                         ["seven", "eight", "unstated"])

    def test_review_keeps_every_job_and_marks_the_ones_over(self) -> None:
        jobs = self._run(enabled=True, mode="review", review_min_score=9, tolerance_years=1)
        self.assertEqual([j.job_id for j in jobs], ["seven", "eight", "unstated"])
        self.assertEqual({j.job_id: j.held_back for j in jobs},
                         {"seven": "", "unstated": "",
                          "eight": "asks for 8+ years; your limit is 7.5"})
        self.assertEqual(self.JOBS[1].held_back, "")   # the scraped job itself is untouched


class ModeSettingTests(unittest.TestCase):
    """Whether a strong match over the limit reaches the candidate is theirs
    to say: no setting is assumed."""

    def test_an_enabled_filter_needs_a_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "experience.mode"):
            AppConfig.model_validate({"experience": {"enabled": True}})

    def test_review_needs_a_bar(self) -> None:
        with self.assertRaisesRegex(ValueError, "review_min_score"):
            AppConfig.model_validate({"experience": {"enabled": True, "mode": "review"}})

    def test_an_unknown_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig.model_validate({"experience": {"enabled": True, "mode": "warn"}})

    def test_off_needs_nothing(self) -> None:
        self.assertIsNone(AppConfig().experience.mode)


def _scored(job_id: str, relevance: int, held: str = "") -> dict:
    return {"source": "indeed", "job_id": job_id, "title": "Engineer", "company": f"Co {job_id}",
            "relevance": relevance, "held_back": held}


class HeldBackFilterTests(unittest.TestCase):
    def _filter(self, scored: list[dict], **experience) -> dict:
        from src.agent.nodes.score import node_filter

        cfg = AppConfig.model_validate({"min_score": 7, "experience": {
            "enabled": True, "mode": "review", "review_min_score": 9, **experience}})
        return node_filter({"scored": scored}, cfg, None)

    def test_held_jobs_never_reach_the_shortlist(self) -> None:
        state = self._filter([_scored("a", 8), _scored("b", 10, "asks for 8+ years")])
        self.assertEqual([j["job_id"] for j in state["matches"]], ["a"])
        self.assertEqual([j["job_id"] for j in state["held_back"]], ["b"])

    def test_only_strong_ones_are_shown_strongest_first(self) -> None:
        state = self._filter([_scored("weak", 8, "x"), _scored("nine", 9, "x"), _scored("ten", 10, "x")])
        self.assertEqual([j["job_id"] for j in state["held_back"]], ["ten", "nine"])

    def test_capped_per_run(self) -> None:
        state = self._filter([_scored(str(i), 9, "x") for i in range(8)], review_max=3)
        self.assertEqual(len(state["held_back"]), 3)

    def test_nothing_held_without_review(self) -> None:
        from src.agent.nodes.score import node_filter

        state = node_filter({"scored": [_scored("a", 9)]}, AppConfig(), None)
        self.assertEqual(state["held_back"], [])


class HeldBackFilesTests(unittest.TestCase):
    """dump writes held_back.json; the web loader serves it with the
    shortlist. Temporary folders and history DB only."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from src import history
        from src.agent.nodes import dump
        from src.web import runs

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (dump.OUTPUT_DIR, runs.OUTPUT_DIR, history.DB_PATH)
        dump.OUTPUT_DIR = runs.OUTPUT_DIR = root / "outputs"
        history.DB_PATH = root / "history.db"

    def tearDown(self) -> None:
        from src import history
        from src.agent.nodes import dump
        from src.web import runs

        dump.OUTPUT_DIR, runs.OUTPUT_DIR, history.DB_PATH = self._saved
        self._tmp.cleanup()

    def test_a_run_with_held_jobs(self) -> None:
        from src.agent.nodes import dump
        from src.web import runs

        state = dump.node_dump({
            "run_timestamp": "2026-10-02T10:00:00+00:00",
            "matches": [_scored("short", 8)],
            "held_back": [_scored("held", 9, "asks for 8+ years; your limit is 7")],
        }, AppConfig(compile_pdf=False))
        stamp = dump.stamp_for(state["run_timestamp"])
        self.assertEqual(runs.load_run(stamp)["held_back_count"], 1)
        rows = {row["job_id"]: row for row in runs.load_jobs(stamp)}
        self.assertEqual(set(rows), {"short", "held"})
        self.assertEqual(rows["held"]["held_back"], "asks for 8+ years; your limit is 7")
        self.assertEqual(rows["short"]["held_back"], "")
        # Every endpoint finds a held job the way it finds any other.
        self.assertEqual(runs.find_job(stamp, "held")["company"], "Co held")

    def _write_run(self, stamp: str, held: list[dict], decisions: dict | None = None) -> None:
        import json

        from src.web import runs

        directory = runs.OUTPUT_DIR / stamp
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(json.dumps(
            {"status": "ok", "match_count": 4, "held_back_count": len(held)}), encoding="utf-8")
        (directory / "held_back.json").write_text(json.dumps(held), encoding="utf-8")
        if decisions:
            (directory / "applications.json").write_text(json.dumps(
                {job_id: {"decision": d} for job_id, d in decisions.items()}), encoding="utf-8")

    def test_held_back_waits_across_runs(self) -> None:
        from src.web import runs

        self._write_run("20261001T100000", [_scored("monday", 9, "x"), _scored("moved", 10, "x")],
                        {"moved": "yes"})
        self._write_run("20261002T100000", [_scored("wednesday", 10, "x")])
        waiting = runs.load_held_waiting()
        self.assertEqual([(r["job_id"], r["stamp"]) for r in waiting],
                         [("wednesday", "20261002T100000"), ("monday", "20261001T100000")])

    def test_the_newest_copy_settles_a_job(self) -> None:
        from src.web import runs

        old = {**_scored("x1", 9, "x"), "company": "Same Co", "title": "Same role"}
        new = {**_scored("x2", 9, "x"), "company": "Same Co", "title": "Same role"}
        self._write_run("20261001T100000", [old])
        self._write_run("20261002T100000", [new], {"x2": "yes"})
        # Moved from Wednesday's run: Monday's copy of the same job is not still waiting.
        self.assertEqual(runs.load_held_waiting(), [])

    def test_a_job_in_several_runs_shows_once_from_the_newest(self) -> None:
        from src.web import runs

        self._write_run("20261001T100000", [_scored("same", 9, "x")])
        self._write_run("20261002T100000", [_scored("same", 9, "x")])
        self.assertEqual([r["stamp"] for r in runs.load_held_waiting()], ["20261002T100000"])

    def test_runs_are_listed_with_their_counts(self) -> None:
        from src.web import runs

        self._write_run("20261001T100000", [_scored("a", 9, "x")])
        self.assertEqual(runs.list_runs(), [{"stamp": "20261001T100000", "status": "ok",
                                             "match_count": 4, "held_back_count": 1}])

    def test_a_run_without_held_jobs_writes_no_file(self) -> None:
        from src.agent.nodes import dump

        state = dump.node_dump({"run_timestamp": "2026-10-02T11:00:00+00:00",
                                "matches": [_scored("short", 8)]}, AppConfig(compile_pdf=False))
        from pathlib import Path

        self.assertFalse((Path(state["run_dir"]) / "held_back.json").exists())

    def test_the_shipped_settings_parse(self) -> None:
        from src.config import load_yaml_config

        rule = load_yaml_config().experience
        self.assertIsInstance(rule.enabled, bool)
        self.assertGreaterEqual(rule.tolerance_years, 0)


if __name__ == "__main__":
    unittest.main()
