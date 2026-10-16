"""Scoring out of 100: six marked parts, a second look under the bar, and a
failed call that keeps the job instead of losing it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import history
from src.agent.nodes import score
from src.config import AppConfig, EnvSettings


def card(total_skills: int, **rest) -> score.ScoreCard:
    base = dict(experience=15, role=15, stack=8, company=8, location=10)
    base.update(rest)
    return score.ScoreCard(skills=total_skills, why_score="fit", **base)


def _job(job_id: str, title: str) -> dict:
    return {"source": "indeed", "job_id": job_id, "company": f"Co {job_id}", "title": title,
            "description": f"{title}: build backend services on AWS with Node.js. " * 20}


class FakeModels:
    """ChatOpenAI stand-in: answers by model name and job title."""

    def __init__(self, answers: dict[str, dict[str, object]]):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model, **_kwargs):
        outer = self

        class Llm:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, prompt):
                title = prompt.split("JOB TITLE: ", 1)[1].split("\n", 1)[0]
                outer.calls.append((model, title))
                answer = outer.answers[model][title]
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return Llm()


class ScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "history.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._db
        self._tmp.cleanup()

    def _score(self, jobs, answers, **cfg):
        fake = FakeModels(answers)
        config = AppConfig.model_validate({"reuse_enrichment": False, **cfg})
        with mock.patch.object(score, "ChatOpenAI", side_effect=fake), \
                mock.patch.object(score, "RETRY_AFTER_S", 0):
            out = score.node_score({"raw_jobs": jobs, "resume_text": "resume"}, config,
                                   EnvSettings(openai_api_key="x"))
        return {j["title"]: j for j in out["scored"]}, fake.calls

    def test_the_score_is_the_sum_of_the_parts_held_to_their_ranges(self) -> None:
        result = score.to_score(score.ScoreCard(skills=50, experience=-3, role=15, stack=10,
                                                company=10, location=10))
        self.assertEqual(result.score_parts["skills"], 35)
        self.assertEqual(result.score_parts["experience"], 0)
        self.assertEqual((result.relevance, result.score_scale), (80, 100))

    def test_the_prompt_names_every_part_and_its_points(self) -> None:
        for name, most, _anchors in score.PARTS:
            self.assertIn(f"{name} (0-{most})", score.SCORE_SYSTEM)
        self.assertEqual(sum(most for _n, most, _a in score.PARTS), 100)

    def test_just_under_the_bar_gets_a_second_look_and_is_kept_when_it_passes(self) -> None:
        cheap, strong = "gpt-5.6-luna", "gpt-5.6-sol"
        scored, calls = self._score(
            [_job("a", "Strong"), _job("b", "Close"), _job("c", "Weak")],
            # 86, 68 (just under 70) and 33.
            {cheap: {"Strong": card(30), "Close": card(12), "Weak": card(0, role=0, stack=0)},
             strong: {"Close": card(20)}})
        self.assertEqual(scored["Close"]["relevance"], 76)
        self.assertIn("First look 68", scored["Close"]["second_look"])
        # Only the band pays for the stronger model.
        self.assertEqual([c for c in calls if c[0] == strong], [(strong, "Close")])
        self.assertEqual(scored["Weak"]["second_look"], "")

    def test_a_second_look_that_still_falls_short_keeps_the_first_score(self) -> None:
        scored, _calls = self._score(
            [_job("b", "Close")],
            {"gpt-5.6-luna": {"Close": card(12)}, "gpt-5.6-sol": {"Close": card(10)}})
        self.assertEqual(scored["Close"]["relevance"], 68)
        self.assertIn("Second look by gpt-5.6-sol: 66", scored["Close"]["second_look"])

    def test_no_second_look_when_turned_off(self) -> None:
        _scored, calls = self._score([_job("b", "Close")], {"gpt-5.6-luna": {"Close": card(12)}},
                                     second_look_below=0)
        self.assertEqual(len(calls), 1)

    def test_a_job_whose_call_fails_twice_is_kept_to_check_by_hand(self) -> None:
        scored, calls = self._score([_job("x", "Broken")],
                                    {"gpt-5.6-luna": {"Broken": RuntimeError("rate limited")}})
        job = scored["Broken"]
        self.assertTrue(job["score_failed"])
        self.assertEqual(job["relevance"], 70)                 # at the bar: it reaches the shortlist
        self.assertIn("check it yourself", job["why_score"])
        self.assertEqual(len(calls), 2)                        # tried once more first


if __name__ == "__main__":
    unittest.main()
