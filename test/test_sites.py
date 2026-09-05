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
