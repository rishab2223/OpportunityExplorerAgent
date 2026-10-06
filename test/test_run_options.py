from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from src import history, llm_cache
from src.agent.nodes import dump, enrich, score
from src.config import AppConfig, EnvSettings
from src.models import JobScore, TexEnrichment
from src.web import run_options

BASE = AppConfig.model_validate({
    "min_score": 7,
    "experience": {"enabled": True, "mode": "review", "review_min_score": 9},
    "scrape": {"max_detail_jobs": 150, "sources": ["indeed", "linkedin"]},
})


class TempDbCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "history.db"
        self._prefs = run_options.PREFERENCES_PATH
        run_options.PREFERENCES_PATH = Path(self._tmp.name) / "run_preferences.json"

    def tearDown(self) -> None:
        history.DB_PATH = self._db
        run_options.PREFERENCES_PATH = self._prefs
        self._tmp.cleanup()


class ApplyOverridesTests(unittest.TestCase):
    def test_per_source_caps_and_the_fallback(self) -> None:
        cfg = run_options.apply(BASE, {"scrape": {"max_jobs": {"linkedin": 40}}})
        self.assertEqual(cfg.scrape.cap("linkedin"), 40)
        self.assertEqual(cfg.scrape.cap("indeed"), 150)
        self.assertEqual(BASE.scrape.cap("linkedin"), 150)   # the base is untouched

    def test_out_of_range_is_refused_whole(self) -> None:
        for bad in ({"scrape": {"max_jobs": {"linkedin": 900}}},
                    {"min_score": 11},
                    {"claude": {"effort": "extreme"}},
                    {"openai": {"enrich_concurrency": 99}},
                    {"scrape": {"sources": []}},
                    {"scrape": {"sources": ["monster"]}}):
            with self.assertRaises(run_options.BadOptions, msg=str(bad)):
                run_options.apply(BASE, bad)

    def test_only_listed_keys_can_be_set(self) -> None:
        for bad in ({"scrape": {"apify": {"indeed_actor": "x"}}},
                    {"claude": {"backend": "api"}},
                    {"apply_provider": "openai"}):
            with self.assertRaises(run_options.BadOptions, msg=str(bad)):
                run_options.apply(BASE, bad)

    def test_what_the_popup_shows_goes_back_unchanged(self) -> None:
        shown = run_options.values(BASE)
        again = run_options.apply(BASE, shown)
        self.assertEqual(run_options.values(again), shown)

    def test_sources_are_normalised(self) -> None:
        cfg = run_options.apply(BASE, {"scrape": {"sources": ["LinkedIn"]}})
        self.assertEqual(cfg.scrape.sources, ["linkedin"])

    def test_the_log_line_names_the_choices(self) -> None:
        cfg = run_options.apply(BASE, {"scrape": {"sources": ["linkedin"], "max_jobs": {"linkedin": 40}}})
        line = run_options.summary_line(cfg)
        self.assertIn("LinkedIn 40 jobs", line)
        self.assertNotIn("Indeed", line)


class ScrapeCapTests(TempDbCase):
    def test_each_source_stops_at_its_own_cap(self) -> None:
        # pyflakes caught `cap` undefined in _collect after a partial edit (Oct
        # 2026): the next scrape would have crashed. Covered directly now.
        from src.config import ScrapeConfig
        from src.scrape import apify_jobs

        scrape = ScrapeConfig(max_detail_jobs=5, max_jobs={"linkedin": 2})
        items = [{"id": str(i)} for i in range(10)]
        mapper = lambda item: apify_jobs.JobPosting(source="x", job_id=item["id"])  # noqa: E731
        self.assertEqual(len(apify_jobs._collect(items, mapper, scrape, "linkedin")), 2)
        self.assertEqual(len(apify_jobs._collect(items, mapper, scrape, "indeed")), 5)

    def test_a_role_posted_in_many_cities_is_named_once(self) -> None:
        from src.agent.nodes import scrape as scrape_node
        from src.models import JobPosting

        history.record({"job_id": "old", "company": "Accenture", "title": "Custom Software Engineer",
                        "source": "indeed"}, "closed")
        jobs = [JobPosting(source="indeed", job_id=f"new{i}", company="Accenture",
                           title="Custom Software Engineer") for i in range(3)]
        lines: list[str] = []
        with mock.patch.object(scrape_node.progress, "log", side_effect=lines.append):
            kept = scrape_node._drop_seen(jobs, AppConfig())
        self.assertEqual(kept, [])
        named = [ln for ln in lines if "Skipping" in ln]
        self.assertEqual(len(named), 1)
        self.assertIn("(3 postings)", named[0])


class PreferenceTests(TempDbCase):
    def test_saved_choices_are_the_next_defaults(self) -> None:
        with mock.patch.object(run_options, "load_yaml_config", return_value=BASE):
            run_options.save_preferences({"min_score": 8})
            self.assertEqual(run_options.effective_config().min_score, 8)
            self.assertTrue(run_options.describe()["saved"])
            self.assertEqual(run_options.describe()["defaults"]["min_score"], 7)
            run_options.clear_preferences()
            self.assertEqual(run_options.effective_config().min_score, 7)

    def test_an_invalid_choice_is_never_saved(self) -> None:
        with mock.patch.object(run_options, "load_yaml_config", return_value=BASE):
            with self.assertRaises(run_options.BadOptions):
                run_options.save_preferences({"min_score": 0})
        self.assertFalse(run_options.PREFERENCES_PATH.exists())

    def test_a_stale_preference_file_does_not_block_runs(self) -> None:
        run_options.PREFERENCES_PATH.write_text(json.dumps({"min_score": 99}), encoding="utf-8")
        with mock.patch.object(run_options, "load_yaml_config", return_value=BASE):
            self.assertEqual(run_options.effective_config().min_score, 7)


JD = ("We are looking for a backend engineer to build services on AWS with Node.js, "
      "PostgreSQL and Docker, working with product teams across the business. ")


def _job(job_id: str, company: str = "Acme", title: str = "Engineer", city: str = "Pune") -> dict:
    # The same posting in another city differs by the city name alone.
    return {"source": "indeed", "job_id": job_id, "company": company, "title": title,
            "location": city, "description": JD * 3 + f"Location: {city}.", "relevance": 8}


LATEX = ("\\documentclass{article}\\begin{document}\n\\section*{Summary}\nOld summary.\n"
         "\\section*{Skills}\nPython.\n\\end{document}\n")


class EnrichSpeedTests(TempDbCase):
    def _run(self, matches: list[dict], cfg: AppConfig | None = None) -> tuple[dict, list[str]]:
        calls: list[str] = []
        lock = threading.Lock()

        def invoke(system, user, schema):
            with lock:
                calls.append(system)
            return TexEnrichment(resume_edit_suggestions="Rephrased the summary.",
                                 interview_prep="Q1" if "Also provide interview prep" in system else "",
                                 section_edits=[{"heading": "Summary", "latex_body": "\nNew summary.\n"}])

        state = {"matches": matches, "resume_latex": LATEX, "resume_text": "resume"}
        with mock.patch.object(enrich, "make_invoker", return_value=invoke):
            out = enrich.node_enrich(state, cfg or AppConfig(), EnvSettings())
        return out, calls

    def test_interview_prep_is_left_for_later_by_default(self) -> None:
        out, calls = self._run([_job("a")])
        self.assertIn("Leave interview_prep empty", calls[0])
        self.assertEqual(out["matches"][0]["interview_prep"], "")
        out, calls = self._run([_job("b")], AppConfig(interview_prep="run"))
        self.assertIn("Also provide interview prep", calls[0])
        self.assertEqual(out["matches"][0]["interview_prep"], "Q1")

    def test_the_same_posting_in_another_city_is_tailored_once(self) -> None:
        out, calls = self._run([_job("a"), _job("b", title="Engineer ", city="Chennai"),
                                _job("c", company="Other")])
        self.assertEqual(len(calls), 2)
        rows = {m["job_id"]: m for m in out["matches"]}
        self.assertEqual([m["job_id"] for m in out["matches"]], ["a", "b", "c"])   # order kept
        self.assertEqual(rows["b"]["resume_latex"], rows["a"]["resume_latex"])
        self.assertIn("New summary", rows["b"]["resume_latex"])
        self.assertIn("Location: Chennai", rows["b"]["description"])             # its own posting

    def test_a_different_role_under_the_same_title_is_not_a_twin(self) -> None:
        other = dict(_job("b"), description="A data platform role: Spark, Kafka, Airflow, and "
                                             "a lot of SQL, on a team of twelve in Bengaluru. " * 3)
        _, calls = self._run([_job("a"), other])
        self.assertEqual(len(calls), 2)

    def test_a_later_run_reuses_the_result(self) -> None:
        self._run([_job("a")])
        out, calls = self._run([_job("a")])
        self.assertEqual(calls, [])
        self.assertIn("New summary", out["matches"][0]["resume_latex"])
        # A changed posting is a new call; so is reuse turned off.
        changed = dict(_job("a"), description="A different JD")
        self.assertEqual(len(self._run([changed])[1]), 1)
        self.assertEqual(len(self._run([_job("a")], AppConfig(reuse_enrichment=False))[1]), 1)

    def test_on_demand_prep_writes_only_prep(self) -> None:
        from src.models import MatchEnrichment, ScoredJob

        seen = []

        def invoke(system, user, schema):
            seen.append((system, user))
            return MatchEnrichment(interview_prep=" Tell me about S3. ")

        prep = enrich.write_prep(invoke, "my resume", ScoredJob.model_validate(_job("a")))
        self.assertEqual(prep, "Tell me about S3.")
        self.assertIn("interview prep", seen[0][0])
        self.assertIn("Location: Pune", seen[0][1])


class ScoreReuseTests(TempDbCase):
    def test_a_posting_scored_before_is_not_scored_again(self) -> None:
        calls = []

        class FakeLlm:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, prompt):
                calls.append(prompt)
                return JobScore(relevance=8, why_score="fits")

        state = {"raw_jobs": [_job("a")], "resume_text": "resume"}
        env = EnvSettings(openai_api_key="x")
        with mock.patch.object(score, "ChatOpenAI", return_value=FakeLlm()):
            score.node_score(dict(state), AppConfig(), env)
            out = score.node_score(dict(state), AppConfig(), env)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["scored"][0]["relevance"], 8)


class ParallelPdfTests(unittest.TestCase):
    def test_compiles_run_side_by_side_and_keep_their_records(self) -> None:
        from src.models import MatchRecord
        from src.resume.one_page import TrimResult as FitResult

        running, peak = [0], [0]
        lock = threading.Lock()

        def fake_fit(path, steps=None):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1
            Path(path).with_suffix(".pdf").write_bytes(b"%PDF")
            return FitResult(pages=1, cuts=["CCNA certification"] if "b" in Path(path).stem else [])

        with tempfile.TemporaryDirectory() as tmp:
            jobs = []
            for name in "abcdef":
                tex = Path(tmp) / f"{name}.tex"
                tex.write_text("x", encoding="utf-8")
                jobs.append((MatchRecord(source="indeed", job_id=name, relevance=8), tex, "x"))
            with mock.patch.object(dump, "fit_to_one_page", side_effect=fake_fit):
                compiled = dump._compile_pdfs(jobs, workers=4)
            self.assertTrue((Path(tmp) / dump.FULL_DIR / "b.tex").exists())   # the trimmed one's original
        self.assertEqual(compiled, 6)
        self.assertGreater(peak[0], 1)
        self.assertTrue(all(rec.resume_pdf_file == f"{rec.job_id}.pdf" for rec, _, _ in jobs))

    def test_one_broken_resume_does_not_stop_the_others(self) -> None:
        from src.models import MatchRecord

        def fake_fit(path, steps=None):
            if "b" in Path(path).stem:
                raise RuntimeError("boom")
            Path(path).with_suffix(".pdf").write_bytes(b"%PDF")
            return FitResult(pages=1)

        with tempfile.TemporaryDirectory() as tmp:
            jobs = []
            for name in "abc":
                tex = Path(tmp) / f"{name}.tex"
                tex.write_text("x", encoding="utf-8")
                jobs.append((MatchRecord(source="indeed", job_id=name, relevance=8), tex, "x"))
            with mock.patch.object(dump, "fit_to_one_page", side_effect=fake_fit):
                compiled = dump._compile_pdfs(jobs, workers=2)
        self.assertEqual(compiled, 2)
        self.assertIn("boom", jobs[1][0].resume_pdf_error)


class RerunTests(unittest.TestCase):
    def test_a_second_pass_only_when_latex_asks(self) -> None:
        from src import pdf_compile

        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "r.tex"
            tex.with_suffix(".log").write_text("Output written on r.pdf (1 page).", encoding="utf-8")
            self.assertFalse(pdf_compile._wants_rerun(tex))
            tex.with_suffix(".log").write_text(
                "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.",
                encoding="utf-8")
            self.assertTrue(pdf_compile._wants_rerun(tex))


class CacheTests(TempDbCase):
    def test_keys_and_round_trip(self) -> None:
        key = llm_cache.key("a", "b")
        self.assertNotEqual(key, llm_cache.key("ab", ""))   # parts are separated
        self.assertIsNone(llm_cache.get("enrich", key))
        llm_cache.put("enrich", key, {"x": 1})
        self.assertEqual(llm_cache.get("enrich", key), {"x": 1})
        self.assertIsNone(llm_cache.get("score", key))       # kinds are separate


if __name__ == "__main__":
    unittest.main()
