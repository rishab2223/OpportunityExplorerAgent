from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.apply import profile


class ProfileFileTestCase(unittest.TestCase):
    """A scratch profile file: never the candidate's real one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original = profile.PROFILE_PATH
        profile.PROFILE_PATH = Path(self._tmp.name) / "apply_profile.json"
        profile._cache = None

    def tearDown(self) -> None:
        profile.PROFILE_PATH = self._original
        profile._cache = None
        self._tmp.cleanup()

    def write(self, data: dict) -> None:
        profile.PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")
        profile._cache = None

    def on_disk(self) -> dict:
        return json.loads(profile.PROFILE_PATH.read_text(encoding="utf-8"))


class SaveTests(ProfileFileTestCase):
    def test_a_save_merges_and_keeps_keys_the_page_does_not_know(self) -> None:
        self.write({"full_name": "Test User", "work_authorization": "Yes", "learned": {"x": 1}})
        self.assertEqual(profile.save_profile({"total_experience_years": "6.5"}), {})
        data = self.on_disk()
        self.assertEqual(data["total_experience_years"], "6.5")
        self.assertEqual(data["full_name"], "Test User")
        self.assertEqual(data["work_authorization"], "Yes")
        self.assertEqual(data["learned"], {"x": 1})

    def test_the_next_read_sees_the_save_at_once(self) -> None:
        # An apply session reads through the cache; it must not keep the old value.
        self.write({"total_experience_years": "6"})
        self.assertEqual(profile.load_profile()["total_experience_years"], "6")
        profile.save_profile({"total_experience_years": "7"})
        self.assertEqual(profile.load_profile()["total_experience_years"], "7")

    def test_save_creates_the_file(self) -> None:
        self.assertFalse(profile.PROFILE_PATH.exists())
        self.assertEqual(profile.save_profile({"full_name": "Test User"}), {})
        self.assertEqual(self.on_disk()["full_name"], "Test User")
        self.assertIn("jobs", self.on_disk())          # the whole template is written

    def test_a_file_that_does_not_parse_is_never_written_over(self) -> None:
        profile.PROFILE_PATH.write_text('{"full_name": "Test User", broken', encoding="utf-8")
        profile._cache = None
        errors = profile.save_profile({"total_experience_years": "6"})
        self.assertIn("_file", errors)
        self.assertIn("broken", profile.PROFILE_PATH.read_text(encoding="utf-8"))

    def test_any_error_writes_nothing(self) -> None:
        self.write({"full_name": "Test User", "email": "test@example.com"})
        errors = profile.save_profile({"full_name": "Changed", "email": "not-an-email"})
        self.assertIn("email", errors)
        self.assertEqual(self.on_disk()["full_name"], "Test User")

    def test_checks_by_kind(self) -> None:
        self.write({})
        errors = profile.save_profile({
            "total_experience_years": "six", "linkedin": "linkedin.com/in/test",
            "graduation_year": "2020"})
        self.assertEqual(set(errors), {"total_experience_years", "linkedin"})
        for ok in ("6", "6.5", "6+", ""):
            with self.subTest(value=ok):
                self.assertEqual(profile.save_profile({"total_experience_years": ok}), {})

    def test_only_profile_keys_can_be_set(self) -> None:
        self.write({"full_name": "Test User"})
        errors = profile.save_profile({"pan_number": "ABCDE1234F"})
        self.assertEqual(list(errors), ["pan_number"])
        self.assertNotIn("pan_number", self.on_disk())


class JobsTests(ProfileFileTestCase):
    JOB = {"title": "Engineer", "company": "Example Co", "location": "Remote",
           "start": "01/2020", "end": "02/2021", "current": False, "description": "Built things."}

    def test_a_current_job_has_no_end(self) -> None:
        self.write({})
        self.assertEqual(profile.save_profile({"jobs": [{**self.JOB, "current": True}]}), {})
        self.assertEqual(self.on_disk()["jobs"][0]["end"], "")

    def test_dates_must_be_month_and_year(self) -> None:
        self.write({})
        errors = profile.save_profile({"jobs": [{**self.JOB, "start": "2020"}]})
        self.assertIn("MM/YYYY", errors["jobs"])

    def test_an_entry_needs_a_title_or_company(self) -> None:
        self.write({})
        errors = profile.save_profile({"jobs": [{**self.JOB, "title": "", "company": ""}]})
        self.assertIn("jobs", errors)

    def test_extra_job_keys_survive(self) -> None:
        self.write({})
        profile.save_profile({"jobs": [{**self.JOB, "team": "Platform"}]})
        self.assertEqual(self.on_disk()["jobs"][0]["team"], "Platform")


class PageDataTests(ProfileFileTestCase):
    def test_every_template_key_is_on_the_page(self) -> None:
        self.write({"full_name": "Test User"})
        data = profile.page_data()
        shown = {f["key"] for s in data["sections"] for f in s["fields"]}
        self.assertLessEqual(set(profile.TEMPLATE), shown)
        self.assertEqual(data["values"]["full_name"], "Test User")
        self.assertEqual(data["values"]["notice_period"], "")

    def test_an_unknown_key_appears_under_other(self) -> None:
        self.write({"work_authorization": "Yes", "learned": {"x": 1}})
        other = [s for s in profile.page_data()["sections"] if s["title"] == "Other"]
        self.assertEqual([f["key"] for f in other[0]["fields"]], ["work_authorization"])

    def test_no_file_yet(self) -> None:
        data = profile.page_data()
        self.assertFalse(data["exists"])
        self.assertEqual(data["values"], profile.TEMPLATE)


if __name__ == "__main__":
    unittest.main()
