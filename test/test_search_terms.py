"""Search terms per job site, from the run settings popup to what Apify gets."""

from __future__ import annotations

import unittest
from unittest import mock

from src.config import AppConfig
from src.scrape import apify_jobs, search_terms
from src.web import run_options

INDEED = ('title:("Software Engineer" or "Software Developer" or "Senior Software Engineer" '
          'or "Senior Software Developer" or "NodeJS")')
LINKEDIN = ("https://www.linkedin.com/jobs/search/?keywords=software%20engineer%20OR%20software%20developer"
            "%20OR%20nodejs%20OR%20backend%20engineer&geoId=102713980&sortBy=DD")
BASE = AppConfig.model_validate({"scrape": {"keywords": INDEED, "apify": {"linkedin_input": {"searchUrls": [LINKEDIN]}}}})


class ShapeTests(unittest.TestCase):
    def test_todays_indeed_query_reads_as_terms_and_back_unchanged(self) -> None:
        terms = search_terms.indeed_terms(INDEED)
        self.assertEqual(terms, ["Software Engineer", "Software Developer", "Senior Software Engineer",
                                 "Senior Software Developer", "NodeJS"])
        self.assertEqual(search_terms.indeed_query(terms), INDEED)

    def test_todays_linkedin_url_reads_as_terms_and_back_unchanged(self) -> None:
        terms = search_terms.linkedin_terms(LINKEDIN)
        self.assertEqual(terms, ["software engineer", "software developer", "nodejs", "backend engineer"])
        self.assertEqual(search_terms.linkedin_url(LINKEDIN, terms), LINKEDIN)

    def test_new_linkedin_terms_keep_the_rest_of_the_url(self) -> None:
        url = search_terms.linkedin_url(LINKEDIN, ["platform engineer", "SRE"])
        self.assertIn("keywords=platform%20engineer%20OR%20SRE", url)
        self.assertIn("geoId=102713980", url)
        self.assertIn("sortBy=DD", url)

    def test_a_plain_keyword_is_one_term(self) -> None:
        self.assertEqual(search_terms.indeed_terms("software engineer"), ["software engineer"])
        self.assertEqual(search_terms.indeed_terms(""), [])

    def test_characters_that_break_a_query_are_taken_out_and_repeats_dropped(self) -> None:
        self.assertEqual(search_terms.clean(['  "Node.js" (backend) ', "node.js (backend)", "", "Go"]),
                         ["Node.js backend", "Go"])


class PopupTests(unittest.TestCase):
    def test_the_popup_shows_what_each_site_searches_today(self) -> None:
        shown = run_options.values(BASE)["scrape"]["search_terms"]
        self.assertEqual(shown["indeed"][-1], "NodeJS")
        self.assertEqual(shown["linkedin"][-1], "backend engineer")

    def test_terms_sent_back_unchanged_change_nothing(self) -> None:
        shown = run_options.values(BASE)
        again = run_options.apply(BASE, shown)
        self.assertEqual(run_options.values(again), shown)

    def test_a_site_that_is_on_needs_a_term(self) -> None:
        with self.assertRaises(run_options.BadOptions):
            run_options.apply(BASE, {"scrape": {"search_terms": {"linkedin": []}}})
        # Switched off, an empty list is fine.
        run_options.apply(BASE, {"scrape": {"sources": ["indeed"], "search_terms": {"linkedin": []}}})

    def test_too_many_or_too_long_terms_are_refused(self) -> None:
        with self.assertRaises(run_options.BadOptions):
            run_options.apply(BASE, {"scrape": {"search_terms": {"indeed": [f"t{i}" for i in range(16)]}}})
        with self.assertRaises(run_options.BadOptions):
            run_options.apply(BASE, {"scrape": {"search_terms": {"indeed": ["x" * 61]}}})

    def test_the_log_line_says_what_is_searched(self) -> None:
        cfg = run_options.apply(BASE, {"scrape": {"search_terms": {"linkedin": ["platform engineer"]}}})
        line = run_options.summary_line(cfg)
        self.assertIn("LinkedIn for platform engineer", line)
        self.assertIn("Indeed for Software Engineer / ", line)


class ApifyInputTests(unittest.TestCase):
    def _input(self, cfg, scraper):
        sent = {}

        def fake(_token, _actor, run_input, *_rest, **_kw):
            sent.update(run_input)
            return []

        with mock.patch.object(apify_jobs, "_run_actor", side_effect=fake):
            scraper(cfg.scrape, cfg.scrape.apify, mock.Mock(apify_token="x"))
        return sent

    def test_chosen_terms_reach_both_actors(self) -> None:
        cfg = run_options.apply(BASE, {"scrape": {"search_terms": {
            "indeed": ["Backend Engineer", "NodeJS"], "linkedin": ["backend engineer", "nodejs"]}}})
        indeed = self._input(cfg, apify_jobs.scrape_indeed_apify)
        self.assertEqual(indeed["keyword"], 'title:("Backend Engineer" or "NodeJS")')
        linkedin = self._input(cfg, apify_jobs.scrape_linkedin_apify)
        self.assertIn("keywords=backend%20engineer%20OR%20nodejs", linkedin["searchUrls"][0])
        self.assertIn("f_TPR=", linkedin["searchUrls"][0])     # the recency window still added

    def test_untouched_terms_send_exactly_todays_query(self) -> None:
        cfg = run_options.apply(BASE, run_options.values(BASE))
        self.assertEqual(self._input(cfg, apify_jobs.scrape_indeed_apify)["keyword"], INDEED)
        self.assertTrue(self._input(cfg, apify_jobs.scrape_linkedin_apify)["searchUrls"][0]
                        .startswith(LINKEDIN))


if __name__ == "__main__":
    unittest.main()


class ApifyTimeLimitTests(unittest.TestCase):
    """A fetch is stopped after its time limit and tried again (#49): a hung
    Apify run hung the whole run, and the daily batch with it."""

    def client(self, runs, items_by_dataset):
        calls, aborted = [], []

        class Actor:
            def call(self, **kw):
                calls.append(kw)
                outcome = runs.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        class Dataset:
            def __init__(self, ds):
                self.ds = ds

            def iterate_items(self):
                return iter(items_by_dataset.get(self.ds, []))

        class Run:
            def __init__(self, rid):
                self.rid = rid

            def abort(self):
                aborted.append(self.rid)

        fake = mock.Mock()
        fake.actor.return_value = Actor()
        fake.dataset.side_effect = Dataset
        fake.run.side_effect = Run
        return fake, calls, aborted

    def fetch(self, runs, items, retries=1):
        fake, calls, aborted = self.client(runs, items)
        with mock.patch.object(apify_jobs, "ApifyClient", return_value=fake), \
                mock.patch.object(apify_jobs.progress, "log") as log:
            try:
                got = apify_jobs._run_actor("t", "actor", {}, 10, retries, source="LinkedIn")
            except RuntimeError as exc:
                got = exc
        return got, calls, aborted, [c.args[0] for c in log.call_args_list]

    def test_every_try_has_a_time_limit(self):
        got, calls, _, _ = self.fetch([{"status": "SUCCEEDED", "defaultDatasetId": "d"}], {"d": [{"a": 1}]})
        self.assertEqual(got, [{"a": 1}])
        self.assertEqual(calls[0]["run_timeout"].total_seconds(), 600)
        self.assertGreater(calls[0]["wait_duration"], calls[0]["run_timeout"])

    def test_a_timeout_with_jobs_keeps_them(self):
        got, calls, _, logs = self.fetch([{"status": "TIMED-OUT", "defaultDatasetId": "d"}], {"d": [{"a": 1}]})
        self.assertEqual((got, len(calls)), ([{"a": 1}], 1))
        self.assertIn("timed out after 10 min; using the 1 job(s)", logs[0])

    def test_a_timeout_with_nothing_is_tried_again(self):
        got, calls, aborted, logs = self.fetch(
            [{"status": "RUNNING", "id": "r1", "defaultDatasetId": "d1"},
             {"status": "SUCCEEDED", "defaultDatasetId": "d2"}], {"d2": [{"b": 2}]})
        self.assertEqual((got, len(calls)), ([{"b": 2}], 2))
        self.assertEqual(aborted, ["r1"])                    # stopped, not left billing
        self.assertIn("trying again (2 of 2)", logs[0])

    def test_after_the_last_try_it_says_so(self):
        got, calls, _, _ = self.fetch([RuntimeError("network")] * 2, {})
        self.assertIsInstance(got, RuntimeError)
        self.assertEqual(len(calls), 2)                     # one try and one retry, your call
        self.assertIn("on all 2 tries", str(got))
