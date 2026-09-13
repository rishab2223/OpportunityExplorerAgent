from __future__ import annotations

import unittest

from src.apply import sites
from src.apply.sites import linkedin


class ClosedDetectionTests(unittest.TestCase):
    JOB_URL = "https://in.linkedin.com/jobs/view/software-engineer-at-servify-4458960558"

    def test_banner_text(self) -> None:
        self.assertTrue(linkedin.is_closed(
            "Software Engineer\nServify\nNo longer accepting applications", self.JOB_URL))
        self.assertTrue(linkedin.is_closed("This job is no longer available", self.JOB_URL))

    def test_a_posting_that_was_taken_down(self) -> None:
        # LinkedIn shows this instead of a banner, and the page has no form
        # fields, so without it the candidate was asked to paste a URL for a
        # job that no longer exists.
        self.assertTrue(linkedin.is_closed(
            "Unable to load the page\nJob id provided may not be valid or the job "
            "posting has been removed.\nGo to Jobs", self.JOB_URL))

    def test_redirect_to_search_page(self) -> None:
        self.assertTrue(linkedin.is_closed(
            "", "https://www.linkedin.com/jobs/search/?keywords=software"))
        self.assertTrue(linkedin.is_closed(
            "", "https://www.linkedin.com/jobs/collections/recommended/"))

    def test_open_job_is_not_closed(self) -> None:
        self.assertFalse(linkedin.is_closed(
            "Software Engineer at Servify\nEasy Apply\nSave", self.JOB_URL))
        self.assertFalse(linkedin.is_closed("", "https://employer.example/apply/form"))


class PickApplyTests(unittest.TestCase):
    def test_top_card_apply_beats_similar_jobs_easy_apply(self) -> None:
        buttons = [
            {"id": 3, "tag": "button", "text": "Apply", "label": ""},          # top card
            {"id": 9, "tag": "button", "text": "Easy Apply", "label": ""},     # similar jobs rail
        ]
        target, kind = linkedin.pick_apply(buttons)
        self.assertEqual(target["id"], 3)
        self.assertEqual(kind, "external")

    def test_easy_apply_on_top_card(self) -> None:
        buttons = [
            {"id": 2, "tag": "button", "text": "Easy Apply", "label": ""},
            {"id": 8, "tag": "button", "text": "Apply", "label": ""},
        ]
        target, kind = linkedin.pick_apply(buttons)
        self.assertEqual(target["id"], 2)
        self.assertEqual(kind, "easy_apply")

    def test_no_apply_control(self) -> None:
        self.assertEqual(
            linkedin.pick_apply([{"id": 1, "tag": "button", "text": "Save", "label": ""}]),
            (None, ""),
        )


class PageStateTests(unittest.TestCase):
    """What the page is showing, which is what replaced asking the candidate
    "type done when it has loaded"."""

    JOB_URL = "https://in.linkedin.com/jobs/view/software-engineer-at-servify-4458960558"

    def state(self, fields, text="Software Engineer at Servify") -> str:
        return linkedin.page_state(fields, text, self.JOB_URL)

    def test_nothing_rendered_yet(self) -> None:
        # The shell is there, the job card is not: keep waiting.
        self.assertEqual(self.state([]), "")
        self.assertEqual(
            self.state([{"tag": "a", "text": "Jobs", "label": ""},
                        {"tag": "button", "text": "Skip to search", "label": ""}]),
            "",
        )

    def test_an_apply_button_is_ready(self) -> None:
        self.assertEqual(
            self.state([{"tag": "button", "text": "Easy Apply", "label": ""}]), "apply")
        self.assertEqual(
            self.state([{"tag": "button", "text": "Apply", "label": ""}]), "apply")

    def test_a_closed_banner_beats_a_leftover_apply_button(self) -> None:
        got = self.state([{"tag": "button", "text": "Apply", "label": ""}],
                         "Servify\nNo longer accepting applications")
        self.assertEqual(got, "closed")

    def test_login_beats_apply(self) -> None:
        # A logged-out page shows both; clicking Apply bounces to the authwall.
        got = self.state([{"tag": "button", "text": "Apply", "label": ""},
                          {"tag": "a", "text": "Sign in", "label": ""}])
        self.assertEqual(got, "login")

    def test_a_removed_posting_reads_as_closed(self) -> None:
        got = self.state([], "Unable to load the page\nJob id provided may not be valid "
                             "or the job posting has been removed.")
        self.assertEqual(got, "closed")


class WaitForPageTests(unittest.TestCase):
    class FakePage:
        """Renders its job card after `ticks` polls, like LinkedIn does after
        domcontentloaded."""

        def __init__(self, ticks: int, fields=None, text: str = "") -> None:
            self.ticks = ticks
            self.polls = 0
            self.waited = 0
            self.url = "https://www.linkedin.com/jobs/view/123"
            self._fields = fields if fields is not None else [
                {"tag": "button", "text": "Easy Apply", "label": ""}]
            self._text = text

        def wait_for_timeout(self, ms: int) -> None:
            self.waited += ms

    def _patch(self, page):
        """Stand in for browser.snapshot / page_text against the fake page."""
        from src.apply import browser

        def snapshot(target):
            target.polls += 1
            return target._fields if target.polls > target.ticks else []

        def page_text(target, limit=2500):
            return target._text if target.polls > target.ticks else ""

        self.addCleanup(setattr, browser, "snapshot", browser.snapshot)
        self.addCleanup(setattr, browser, "page_text", browser.page_text)
        browser.snapshot = snapshot
        browser.page_text = page_text

    def test_a_page_that_is_ready_at_once_does_not_wait(self) -> None:
        page = self.FakePage(ticks=0)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "apply")
        self.assertEqual(page.waited, 0)      # no sleep at all

    def test_a_slow_card_is_waited_for(self) -> None:
        page = self.FakePage(ticks=3)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "apply")
        self.assertEqual(page.waited, 3 * linkedin.READY_STEP)

    def test_a_page_that_never_renders_gives_up_and_says_so(self) -> None:
        # The caller then hands back to the candidate rather than guessing.
        page = self.FakePage(ticks=10**6)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page, timeout=1000), "")
        self.assertLessEqual(page.waited, 1000)

    def test_a_closed_posting_is_recognised_without_a_keystroke(self) -> None:
        page = self.FakePage(ticks=1, fields=[], text="No longer accepting applications")
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "closed")


class LoginDetectionTests(unittest.TestCase):
    def test_authwall_urls(self) -> None:
        for url in ("https://www.linkedin.com/authwall?trk=x",
                    "https://www.linkedin.com/uas/login?session_redirect=y",
                    "https://www.linkedin.com/checkpoint/lg/login"):
            self.assertTrue(linkedin.LOGIN_URL_RE.search(url), url)
        self.assertFalse(
            linkedin.LOGIN_URL_RE.search("https://in.linkedin.com/jobs/view/swe-123")
        )


class DetectTests(unittest.TestCase):
    def test_linkedin_hosts(self) -> None:
        self.assertEqual(sites.detect("https://in.linkedin.com/jobs/view/swe-at-x-123"), "linkedin")
        self.assertEqual(sites.detect("https://www.linkedin.com/jobs/view/456"), "linkedin")

    def test_everything_else_is_generic(self) -> None:
        self.assertEqual(sites.detect("https://www.capgemini.com/jobs/528304"), "")
        self.assertEqual(sites.detect("https://nvidia.wd5.myworkdayjobs.com/x"), "")
        self.assertEqual(sites.detect("https://notlinkedin.com.evil.example/x"), "")
        self.assertEqual(sites.detect(""), "")


class NotCurrentlyAcceptingTests(unittest.TestCase):
    def test_linkedins_newer_wording_is_closed(self) -> None:
        from src.apply.sites import linkedin
        from src.apply.worker import _looks_closed

        text = "Fullstack - Software Engineer II. Pune Division. Not currently accepting applications"
        self.assertTrue(linkedin.is_closed(text, "https://www.linkedin.com/jobs/view/123/"))
        self.assertTrue(_looks_closed(text))
        self.assertFalse(_looks_closed("Over 100 people clicked apply. Easy Apply"))
