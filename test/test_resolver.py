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


class CountryAndDialCodeTests(ResolverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._write_profile({**DUMMY_PROFILE, "location": "Gurgaon, India"})

    def _write_profile(self, data: dict) -> None:
        profile.PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_country_derived_from_location(self) -> None:
        got = resolver.resolve(field(label="Country"))
        self.assertEqual(got, ("India", "profile"))
        got = resolver.resolve(field(label="Country of residence"))
        self.assertEqual(got, ("India", "profile"))

    def test_country_select_picks_the_matching_option(self) -> None:
        got = resolver.resolve(field(tag="select", type="", label="Country",
                                     options=["Select", "Diego Garcia (+246)", "India (+91)"]))
        self.assertEqual(got, ("India (+91)", "profile"))

    def test_wrong_dial_code_default_is_overridden(self) -> None:
        # Greenhouse's phone widget defaulted to +246; the one existing value
        # the resolver may replace.
        got = resolver.resolve(field(tag="select", type="", label="Country", value="+246",
                                     options=["+246", "+91", "+1"]))
        self.assertEqual(got, ("+91", "profile"))

    def test_right_dial_code_is_left_alone(self) -> None:
        self.assertIsNone(resolver.resolve(field(tag="select", type="", label="Country",
                                                 value="+91", options=["+246", "+91"])))

    def test_no_dial_code_when_phone_has_no_prefix(self) -> None:
        self._write_profile({**DUMMY_PROFILE, "phone": "9000000000"})
        self.assertIsNone(resolver.resolve(field(tag="select", type="", label="Country",
                                                 value="+246", options=["+246", "+91"])))

    def test_explicit_phone_country_code_wins(self) -> None:
        self._write_profile({**DUMMY_PROFILE, "phone": "9000000000", "phone_country_code": "91"})
        got = resolver.resolve(field(tag="select", type="", label="Country",
                                     value="+246", options=["+246", "+91"]))
        self.assertEqual(got, ("+91", "profile"))


class ControlTypeTests(ResolverTestCase):
    def test_file_input_is_resume(self) -> None:
        # Unlabelled (single-upload forms) and resume-named inputs get the resume.
        self.assertEqual(resolver.resolve(field(type="file")), ("", "resume"))
        self.assertEqual(resolver.resolve(field(type="file", label="Upload CV")), ("", "resume"))

    def test_other_file_inputs_are_left_for_the_model(self) -> None:
        # Regression: every file input got the resume, portfolio uploads included.
        self.assertIsNone(resolver.resolve(field(type="file", label="Portfolio")))
        self.assertIsNone(resolver.resolve(field(type="file", name="certificates")))

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


class CityFromLocationTests(ResolverTestCase):
    def test_city_field_gets_only_the_city(self) -> None:
        profile.PROFILE_PATH.write_text(
            json.dumps({**DUMMY_PROFILE, "location": "Gurgaon, India"}), encoding="utf-8"
        )
        self.assertEqual(resolver.resolve(field(label="City")), ("Gurgaon", "profile"))
        self.assertEqual(resolver.resolve(field(label="Current Location")), ("Gurgaon, India", "profile"))


class PlaceholderSelectTests(ResolverTestCase):
    def test_placeholder_select_counts_as_empty(self) -> None:
        # LinkedIn Easy Apply: <select> showing "Select an option" is blank.
        opts = ["Select an option", "United States of America", "India"]
        self.assertTrue(resolver.is_blank(field(tag="select", type="", label="Country",
                                                value="Select an option", options=opts)))
        self.assertFalse(resolver.is_blank(field(tag="select", type="", label="Country",
                                                 value="India", options=opts)))
        self.assertTrue(resolver.is_blank(field(label="City", value="")))
        self.assertFalse(resolver.is_blank(field(label="City", value="Pune")))

    def test_country_select_on_placeholder_is_filled(self) -> None:
        profile.PROFILE_PATH.write_text(
            json.dumps({**DUMMY_PROFILE, "location": "Gurgaon, India"}), encoding="utf-8"
        )
        opts = ["Select an option", "United States of America", "India"]
        got = resolver.resolve(field(tag="select", type="", label="Country",
                                     value="Select an option", options=opts))
        self.assertEqual(got, ("India", "profile"))

    def test_location_city_label(self) -> None:
        profile.PROFILE_PATH.write_text(
            json.dumps({**DUMMY_PROFILE, "location": "Gurgaon, India"}), encoding="utf-8"
        )
        self.assertEqual(resolver.resolve(field(label="Location (city)")), ("Gurgaon", "profile"))


class ListboxButtonResolveTests(ResolverTestCase):
    def setUp(self) -> None:
        super().setUp()
        profile.PROFILE_PATH.write_text(json.dumps({
            **DUMMY_PROFILE, "location": "Gurgaon, India", "phone": "9000000000",
            "phone_country_code": "+91"}), encoding="utf-8")

    def _button(self, label, text="Select One"):
        # type="button", exactly as the snapshot reports Workday's dropdowns.
        return field(tag="button", type="button", haspopup="listbox", label=label, text=text, value="")

    def test_given_names_label(self) -> None:
        self.assertEqual(resolver.resolve(field(label="Given Name(s)*")), ("Test", "profile"))
        self.assertEqual(resolver.resolve(field(label="Family Name*")), ("User", "profile"))

    def test_blank_and_filled(self) -> None:
        self.assertTrue(resolver.is_blank(self._button("Country*")))
        self.assertFalse(resolver.is_blank(self._button("Country*", text="India")))

    def test_country_and_phone_code_buttons(self) -> None:
        self.assertEqual(resolver.resolve(self._button("Country*")), ("India", "profile"))
        self.assertEqual(resolver.resolve(self._button("Country Phone Code*")), ("+91", "profile"))
        self.assertEqual(resolver.resolve(self._button("Phone Device Type*")), None)
        self.assertIsNone(resolver.resolve(self._button("Country*", text="India")))

    def test_phone_code_select_with_option_text(self) -> None:
        got = resolver.resolve(field(tag="select", type="", label="Country Phone Code",
                                     value="", options=["Select One", "India (+91)", "Canada (+1)"]))
        self.assertEqual(got, ("India (+91)", "profile"))


class RepeatingSectionTests(ResolverTestCase):
    def test_entry_fields_are_left_to_the_model(self) -> None:
        # Workday "My Experience": the profile's location is not a past job's.
        self.assertIsNone(resolver.resolve(field(label="Location", section="Work Experience")))
        self.assertIsNone(resolver.resolve(field(label="Company", section="Employment History")))
        self.assertIsNone(resolver.resolve(field(label="Location", section="Education")))
        self.assertEqual(resolver.resolve(field(label="Location", section="Contact Information")),
                         ("Gurgaon", "profile"))
        self.assertEqual(resolver.resolve(field(label="Location")), ("Gurgaon", "profile"))


class AccentAndAddressTests(ResolverTestCase):
    def setUp(self) -> None:
        super().setUp()
        profile.PROFILE_PATH.write_text(json.dumps({
            **DUMMY_PROFILE, "state": "Haryana", "address_line1": "12 Test Lane"}), encoding="utf-8")

    def test_accent_insensitive_option_match(self) -> None:
        # Workday spells the Indian states with macrons.
        opts = ["Select One", "Bihār", "Haryāna", "Himāchal Pradesh"]
        self.assertEqual(resolver.match_option("Haryana", opts), "Haryāna")
        self.assertEqual(resolver.match_option("bihar", opts), "Bihār")
        self.assertEqual(resolver.match_option("Pradesh", opts), "Himāchal Pradesh")

    def test_state_and_address_rules(self) -> None:
        opts = ["Select One", "Haryāna", "Kerala"]
        self.assertEqual(resolver.resolve(field(tag="select", type="", label="State*", options=opts)),
                         ("Haryāna", "profile"))
        self.assertEqual(resolver.resolve(field(tag="button", type="button", haspopup="listbox",
                                                label="State*", text="Select One")),
                         ("Haryana", "profile"))
        self.assertEqual(resolver.resolve(field(label="Address Line 1")), ("12 Test Lane", "profile"))
        self.assertIsNone(resolver.resolve(field(label="Address Line 2")))


class ProfileSectionEntryTests(ResolverTestCase):
    def setUp(self) -> None:
        super().setUp()
        profile.PROFILE_PATH.write_text(json.dumps({
            **DUMMY_PROFILE, "languages": "English - Intermediate; Hindi - Fluent",
            "github": "https://github.com/test"}), encoding="utf-8")

    def test_languages_parse(self) -> None:
        self.assertEqual(resolver.profile_languages({"languages": "English - Intermediate; Hindi - Fluent"}),
                         [("English", "Intermediate"), ("Hindi", "Fluent")])
        self.assertEqual(resolver.profile_languages({"languages": "English (Fluent), Hindi: Native"}),
                         [("English", "Fluent"), ("Hindi", "Native")])
        self.assertEqual(resolver.profile_languages({"languages": "Tamil"}), [("Tamil", "")])
        self.assertEqual(resolver.profile_languages({}), [])

    def test_kth_language_entry(self) -> None:
        opts = ["Select One", "English", "Hindi", "Tamil"]
        first = field(tag="select", type="", label="Language*", section="Languages 1", options=opts, ordinal=0)
        second = field(tag="select", type="", label="Language*", section="Languages", options=opts, ordinal=1)
        third = field(tag="select", type="", label="Language*", section="Languages", options=opts, ordinal=2)
        self.assertEqual(resolver.resolve(first), ("English", "profile"))
        self.assertEqual(resolver.resolve(second), ("Hindi", "profile"))
        self.assertIsNone(resolver.resolve(third))
        level = field(tag="select", type="", label="Overall*", section="Languages 2",
                      options=["Select One", "Intermediate", "Fluent"], ordinal=1)
        self.assertEqual(resolver.resolve(level), ("Fluent", "profile"))
        # A listbox button for the language works the same way.
        button = field(tag="button", type="button", haspopup="listbox", label="Language*",
                       text="Select One", section="Languages", ordinal=1)
        self.assertEqual(resolver.resolve(button), ("Hindi", "profile"))

    def test_websites_take_the_profile_links_in_order(self) -> None:
        self.assertEqual(resolver.resolve(field(label="URL*", section="Websites 1", ordinal=0)),
                         ("https://linkedin.com/in/test", "profile"))
        self.assertEqual(resolver.resolve(field(label="URL*", section="Websites", ordinal=1)),
                         ("https://github.com/test", "profile"))
        self.assertIsNone(resolver.resolve(field(label="URL*", section="Websites", ordinal=2)))
        # Work Experience stays the model's.
        self.assertIsNone(resolver.resolve(field(label="Location", section="Work Experience 1", ordinal=0)))


class PortfolioSectionTests(ResolverTestCase):
    def test_portfolio_websites_take_the_links(self) -> None:
        profile.PROFILE_PATH.write_text(json.dumps({**DUMMY_PROFILE, "github": "https://github.com/test"}), encoding="utf-8")
        self.assertEqual(resolver.resolve(field(label="URL*", section="Portfolio (Optional) 1", ordinal=0)),
                         ("https://linkedin.com/in/test", "profile"))
        self.assertEqual(resolver.resolve(field(label="URL*", section="Portfolio (Optional)", ordinal=1)),
                         ("https://github.com/test", "profile"))

    def test_named_linkedin_box_under_social_urls_keeps_linkedin(self) -> None:
        # Workday: "Social Network URLs" -> "Please enter your LinkedIn URL".
        # The heading matches the Websites rule; the box still gets LinkedIn,
        # and the Portfolio entry gets the remaining link (GitHub).
        profile.PROFILE_PATH.write_text(json.dumps({**DUMMY_PROFILE, "github": "https://github.com/test"}), encoding="utf-8")
        taken = ["https://linkedin.com/in/test"]
        box = field(label="Please enter your LinkedIn URL", section="Social Network URLs", ordinal=0, taken_links=taken)
        self.assertFalse(resolver.in_repeating_section(box))
        self.assertIsNone(resolver.entry_value(box, profile.load_profile()))
        self.assertEqual(resolver.resolve(box), ("https://linkedin.com/in/test", "profile"))
        self.assertEqual(resolver.resolve(field(label="URL*", section="Portfolio (Optional) 1", ordinal=0, taken_links=taken)),
                         ("https://github.com/test", "profile"))
