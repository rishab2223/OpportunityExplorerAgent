from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import answers, history
from src.apply import profile, resolver

DUMMY_PROFILE = {
    "full_name": "Test User",
    "email": "test@example.invalid",
    "phone": "+91 00000 00000",
    "location": "Gurgaon",
    "linkedin": "https://linkedin.com/in/test",
    "github": "",
    "notice_period": "60 days",
    "expected_ctc": "30 LPA",
    "work_authorization": "Yes",
}


def field(**kw) -> dict:
    base = {"id": 1, "tag": "input", "type": "text", "label": "", "name": "",
            "autocomplete": "", "value": "", "group": ""}
    base.update(kw)
    return base


class ResolverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_db = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"
        self._original_profile = profile.PROFILE_PATH
        profile.PROFILE_PATH = Path(self._tmp.name) / "apply_profile.json"
        profile.PROFILE_PATH.write_text(json.dumps(DUMMY_PROFILE), encoding="utf-8")

    def tearDown(self) -> None:
        history.DB_PATH = self._original_db
        profile.PROFILE_PATH = self._original_profile
        self._tmp.cleanup()


class ProfileMappingTests(ResolverTestCase):
    def test_autocomplete_is_trusted_first(self) -> None:
        got = resolver.resolve(field(autocomplete="email", label="Anything"))
        self.assertEqual(got, ("test@example.invalid", "profile"))

    def test_exact_name_match(self) -> None:
        got = resolver.resolve(field(name="phone_number"))
        self.assertEqual(got, ("+91 00000 00000", "profile"))

    def test_label_match_is_anchored_not_substring(self) -> None:
        self.assertEqual(
            resolver.resolve(field(label="Email address")),
            ("test@example.invalid", "profile"),
        )
        # A different person's email field must never take the candidate's.
        self.assertIsNone(resolver.resolve(field(label="Manager email")))
        self.assertIsNone(resolver.resolve(field(label="Referee name")))

    def test_first_and_last_name_derived_from_full_name(self) -> None:
        self.assertEqual(resolver.resolve(field(label="First name")),
                         ("Test", "profile"))
        self.assertEqual(resolver.resolve(field(label="Last name")),
                         ("User", "profile"))
        self.assertEqual(resolver.resolve(field(autocomplete="given-name")),
                         ("Test", "profile"))
        self.assertEqual(resolver.resolve(field(autocomplete="family-name")),
                         ("User", "profile"))

    def test_notice_period_label(self) -> None:
        got = resolver.resolve(field(label="What is your notice period?"))
        self.assertEqual(got, ("60 days", "profile"))

    def test_empty_profile_value_does_not_match(self) -> None:
        self.assertIsNone(resolver.resolve(field(label="GitHub profile", name="github")))

    def test_existing_value_never_overwritten(self) -> None:
        self.assertIsNone(resolver.resolve(field(autocomplete="email", value="x@y.z")))

    def test_secret_label_never_resolved(self) -> None:
        self.assertIsNone(resolver.resolve(field(label="Email OTP", autocomplete="email")))

    def test_sensitive_topic_never_from_profile(self) -> None:
        # work_authorization is in the profile, but must come from the bank
        # (user-confirmed) instead - the map has no rule for it.
        self.assertIsNone(resolver.resolve(field(label="Work authorization")))


class ControlTypeTests(ResolverTestCase):
    def test_file_input_is_resume(self) -> None:
        self.assertEqual(resolver.resolve(field(type="file")), ("", "resume"))

    def test_checkbox_radio_button_never_resolved(self) -> None:
        self.assertIsNone(resolver.resolve(field(type="checkbox", label="Notice period")))
        self.assertIsNone(resolver.resolve(field(type="radio", label="Notice period")))
        self.assertIsNone(resolver.resolve(field(tag="button", type="", label="Notice period")))

    def test_select_matches_an_option(self) -> None:
        got = resolver.resolve(field(tag="select", type="", label="Notice period",
                                     options=["30 days", "60 days", "90 days"]))
        self.assertEqual(got, ("60 days", "profile"))

    def test_select_without_matching_option_unresolved(self) -> None:
        self.assertIsNone(resolver.resolve(field(tag="select", type="", label="Notice period",
                                                 options=["Immediate", "1 month"])))


class BankFallbackTests(ResolverTestCase):
    def test_neutral_bank_answer_used(self) -> None:
        answers.remember("Favourite programming language", "Python")
        got = resolver.resolve(field(label="Favourite programming language"))
        self.assertEqual(got, ("Python", "saved"))

    def test_sensitive_bank_answer_not_used_by_sweep(self) -> None:
        # Sensitive answers go through the ask gate (loud log), never the
        # silent sweep.
        answers.remember("Do you require visa sponsorship?", "No")
        self.assertIsNone(resolver.resolve(field(label="Do you require visa sponsorship?")))


class MatchOptionTests(unittest.TestCase):
    def test_exact_then_case_insensitive_then_word(self) -> None:
        self.assertEqual(resolver.match_option("60 days", ["30 days", "60 days"]), "60 days")
        self.assertEqual(resolver.match_option("yes", ["Yes", "No"]), "Yes")
        self.assertEqual(resolver.match_option("India", ["India (IN)", "USA"]), "India (IN)")

    def test_ambiguous_or_missing_gives_nothing(self) -> None:
        self.assertEqual(resolver.match_option("days", ["30 days", "60 days"]), "")
        self.assertEqual(resolver.match_option("90 days", ["30 days", "60 days"]), "")
        self.assertEqual(resolver.match_option("", ["a"]), "")
