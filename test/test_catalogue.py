from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.apply import catalogue, sites


class AtsDetectionTests(unittest.TestCase):
    def test_the_systems_we_have_actually_met(self) -> None:
        self.assertEqual(sites.ats("https://dentsu.wd3.myworkdayjobs.com/en-US/x"), "workday")
        self.assertEqual(sites.ats("https://esko.phenompeople.com/job/y"), "phenom")
        self.assertEqual(sites.ats("https://boards.greenhouse.io/acme/jobs/1"), "greenhouse")
        self.assertEqual(sites.ats("https://jobs.lever.co/acme/1"), "lever")
        self.assertEqual(sites.ats("https://alten.talentrecruit.com/careers"), "talentrecruit")
        self.assertEqual(sites.ats("https://www.linkedin.com/jobs/view/1"), "linkedin")
        # The hosts behind 16 of the 41 dumps, which the catalogue had kept none of.
        self.assertEqual(sites.ats("https://recruiting.ultipro.com/USP1000/JobBoard/x"), "ultipro")
        self.assertEqual(sites.ats("https://ats.rippling.com/acme/jobs/1"), "rippling")
        self.assertEqual(sites.ats("https://jobs.jobvite.com/acme/job/1"), "jobvite")
        self.assertEqual(sites.ats("https://career2.successfactors.eu/career?c=x"), "successfactors")
        self.assertEqual(sites.ats("https://login.icims.eu/x"), "icims")
        self.assertEqual(sites.title("ultipro"), "UKG (UltiPro)")

    def test_a_custom_domain_is_named_from_its_vendor_assets(self) -> None:
        # A Phenom site on the employer's own domain says so only by where it
        # loads from.
        self.assertEqual(sites.ats("https://jobs.employer.example/apply"), "")
        self.assertEqual(sites.ats_from_assets(
            ["jobs.employer.example", "cdn.phenompeople.com", "cdn-prod-static.phenompeople.com",
             "px.ads.linkedin.com", "www.google-analytics.com"]), "phenom")
        # LinkedIn's insight tag is on half the career sites there are.
        self.assertEqual(sites.ats_from_assets(["px.ads.linkedin.com", "example.com"]), "")
        self.assertEqual(sites.ats_from_assets([]), "")

    def test_an_unknown_site_is_not_guessed_at(self) -> None:
        for url in ("https://example.com/careers", "", "not a url", "https://notlinkedin.com/x"):
            self.assertEqual(sites.ats(url), "", url)

    def test_detect_still_names_only_sites_with_a_handler(self) -> None:
        # The apply loop branches on detect(); naming Workday there would send
        # a Workday page into the LinkedIn handler.
        self.assertEqual(sites.detect("https://dentsu.wd3.myworkdayjobs.com/x"), "")
        self.assertEqual(sites.detect("https://www.linkedin.com/jobs/view/1"), "linkedin")
        for url in ("http://in.indeed.com/job/software-developer-a8d8", "https://www.indeed.com/viewjob?jk=1",
                    "https://indeed.co.in/rc/clk?jk=1", "https://smartapply.indeed.com/beta/indeedapply/form"):
            self.assertEqual(sites.detect(url), "indeed", url)
        self.assertEqual(sites.detect("https://notindeed.com/jobs/1"), "")


class CatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original = catalogue.CATALOGUE_PATH
        catalogue.CATALOGUE_PATH = Path(self._tmp.name) / "form_catalogue.json"

    def tearDown(self) -> None:
        catalogue.CATALOGUE_PATH = self._original
        self._tmp.cleanup()

    FIELDS = [
        {"id": 1, "tag": "input", "type": "text", "label": "Job Title*",
         "section": "Work History (Optional) 1", "value": "Software Engineer",
         "required": True, "elid": "jt1", "ordinal": 0},
        {"id": 2, "tag": "input", "type": "text", "label": "Email address",
         "section": "Contact", "value": "someone@example.invalid", "elid": "em"},
        {"id": 3, "tag": "textarea", "type": "", "label": "Role Description",
         "section": "To*", "value": "Built the backend and the pipelines.",
         "maxlength": 500},
        {"id": 4, "tag": "select", "label": "Country", "section": "Contact",
         "options": ["India", "Ireland"], "value": "India"},
    ]

    def test_nothing_the_candidate_typed_is_ever_stored(self) -> None:
        catalogue.record("workday", "https://x.myworkdayjobs.com/apply", self.FIELDS)
        raw = catalogue.CATALOGUE_PATH.read_text(encoding="utf-8")
        for secret in ("Software Engineer", "someone@example.invalid",
                       "Built the backend", "India"):
            self.assertNotIn(secret, raw, secret)
        # But the shape that makes it useful IS there.
        for kept in ("Job Title*", "Work History (Optional) 1", "Role Description",
                     "textarea", "maxlength"):
            self.assertIn(kept, raw, kept)

    def test_the_same_form_seen_twice_is_one_entry_with_a_count(self) -> None:
        url = "https://x.myworkdayjobs.com/apply"
        catalogue.record("workday", url, self.FIELDS)
        catalogue.record("workday", url, self.FIELDS)
        forms = catalogue.load()["workday"]
        self.assertEqual(len(forms), 1)
        self.assertEqual(next(iter(forms.values()))["seen"], 2)

    def test_a_different_step_is_a_different_entry(self) -> None:
        catalogue.record("workday", "https://x/1", self.FIELDS)
        catalogue.record("workday", "https://x/2",
                         [{"tag": "input", "label": "Referral source", "section": "Extra"}])
        self.assertEqual(len(catalogue.load()["workday"]), 2)

    def test_a_label_holding_personal_data_is_redacted(self) -> None:
        # A signed-in Workday page renders the account's e-mail address as a
        # field LABEL, so dropping `value` is not enough. Found by recording
        # a real dump, not by reading the code.
        fields = [
            {"tag": "button", "label": "someone@example.invalid", "section": "Account"},
            {"tag": "input", "label": "Call me on 099993 07737", "section": "Contact"},
        ]
        catalogue.record("workday", "https://x/1", fields)
        raw = catalogue.CATALOGUE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("someone@example.invalid", raw)
        self.assertNotIn("07737", raw)
        self.assertIn("<email>", raw)
        self.assertIn("<number>", raw)

    def test_the_form_key_is_redacted_too(self) -> None:
        # The key is a string in the stored file; redacting only the field
        # list left the address in the key that indexes it.
        catalogue.record("workday", "https://x/1",
                         [{"tag": "button", "label": "someone@example.invalid",
                           "section": "Account"}])
        for key in catalogue.load()["workday"]:
            self.assertNotIn("someone@example.invalid", key)

    def test_the_candidates_own_name_is_taken_out(self) -> None:
        import json as _json
        import tempfile as _tempfile
        from src.apply import profile
        tmp = _tempfile.TemporaryDirectory()
        original = profile.PROFILE_PATH
        profile.PROFILE_PATH = Path(tmp.name) / "apply_profile.json"
        profile.PROFILE_PATH.write_text(
            _json.dumps({"full_name": "Testperson Example",
                         "email": "tp@example.invalid"}), encoding="utf-8")
        try:
            catalogue.record("lever", "https://jobs.lever.co/x",
                             [{"tag": "button", "label": "Signed in as Testperson Example",
                               "section": "Header"}])
            raw = catalogue.CATALOGUE_PATH.read_text(encoding="utf-8")
            self.assertNotIn("Testperson", raw)
            self.assertIn("Signed in as", raw)
        finally:
            profile.PROFILE_PATH = original
            tmp.cleanup()

    def test_recording_can_never_break_an_application(self) -> None:
        # An unwritable path, an unknown site, an empty page: all silent.
        self.assertFalse(catalogue.record("", "https://x", self.FIELDS))
        self.assertFalse(catalogue.record("workday", "https://x", []))
        catalogue.CATALOGUE_PATH = Path(self._tmp.name) / "nope" / "\0" / "bad.json"
        self.assertFalse(catalogue.record("workday", "https://x", self.FIELDS))

    def test_a_corrupt_file_is_ignored_rather_than_raising(self) -> None:
        catalogue.CATALOGUE_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(catalogue.load(), {})
        self.assertTrue(catalogue.record("lever", "https://jobs.lever.co/x", self.FIELDS))
        self.assertIn("lever", catalogue.load())

    def test_a_site_does_not_grow_without_bound(self) -> None:
        for n in range(catalogue.MAX_FORMS_PER_SITE + 8):
            catalogue.record("lever", f"https://jobs.lever.co/{n}",
                             [{"tag": "input", "label": f"Box {n}", "section": f"S{n}"}])
        self.assertLessEqual(len(catalogue.load()["lever"]), catalogue.MAX_FORMS_PER_SITE)

    def test_the_catalogue_holds_no_element_ids(self) -> None:
        # Ids are stamped per page load; a stored one would be an invitation
        # to act without reading the live page.
        catalogue.record("workday", "https://x/1", self.FIELDS)
        stored = json.loads(catalogue.CATALOGUE_PATH.read_text(encoding="utf-8"))
        for form in stored["workday"].values():
            for field in form["fields"]:
                self.assertNotIn("id", field)
                self.assertNotIn("elid", field)


if __name__ == "__main__":
    unittest.main()
