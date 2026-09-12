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


class EducationEntryTests(ResolverTestCase):
    """The profile's education line answers the Education section, so the
    model never has to guess "Computer Science" at a 345-option dropdown."""

    EDUCATION = "NorthCap University - Bachelors, Computer and Information Science, 2015-2019"

    def setUp(self) -> None:
        super().setUp()
        profile.PROFILE_PATH.write_text(
            json.dumps({**DUMMY_PROFILE, "education": self.EDUCATION}), encoding="utf-8")

    def test_the_line_is_split_into_its_parts(self) -> None:
        entries = resolver.profile_education({"education": self.EDUCATION})
        self.assertEqual(entries, [{
            "school": "NorthCap University", "degree": "Bachelors",
            "field": "Computer and Information Science", "start": "2015", "end": "2019",
        }])
        # Two entries, and a line with no years.
        two = resolver.profile_education(
            {"education": f"{self.EDUCATION}; Delhi Public School - Class XII, Science"})
        self.assertEqual(len(two), 2)
        self.assertEqual(two[1]["school"], "Delhi Public School")
        self.assertEqual(two[1]["field"], "Science")
        self.assertEqual(resolver.profile_education({}), [])

    def test_an_education_entry_is_filled_from_the_profile(self) -> None:
        for label, want in (("School or University*", "NorthCap University"),
                            ("Degree*", "Bachelors"),
                            ("Field of study*", "Computer and Information Science")):
            got = resolver.resolve(field(label=label, section="Education :", ordinal=0))
            self.assertEqual(got, (want, "profile"), label)
        # A second entry the profile does not have is left to the model.
        self.assertIsNone(resolver.resolve(field(label="Degree*", section="Education :", ordinal=1)))

    def test_a_truncated_option_list_keeps_the_raw_value(self) -> None:
        # The snapshot keeps 40 of 345 options, so the wanted one is not in
        # the captured list; the worker searches the full list in the browser.
        truncated = [f"Subject {n}" for n in range(40)]
        got = resolver.resolve(field(tag="select", type="", label="Field of study*",
                                     section="Education :", ordinal=0, options=truncated))
        self.assertEqual(got, ("Computer and Information Science", "profile"))
        # A short list really is the whole list: no match means no fill.
        self.assertIsNone(resolver.resolve(field(tag="select", type="", label="Field of study*",
                                                 section="Education :", ordinal=0,
                                                 options=["Physics", "Chemistry"])))


JOBS = [
    {"title": "Senior Engineer", "company": "Northwind Systems", "location": "Noida",
     "start": "07/2020", "end": "01/2026", "description": "Backends and pipelines."},
    {"title": "Engineer Intern", "company": "Northwind Systems", "location": "Pune",
     "start": "06/2019", "end": "07/2020", "description": "Rendering work in C++."},
]


def workday_fields() -> list[dict]:
    """The shape of the real Workday dump (fields 9-33). The Month and Year
    boxes carry section "From*"/"To*", NOT the work section, and the Delete
    button comes before the Job Title."""
    out: list[dict] = []
    for n in (1, 2):
        section = f"Work History (Optional) {n}"
        out.append(field(id=len(out), tag="button", type="", label="Delete",
                         text="Delete", section=section, group=section))
        out.append(field(id=len(out), label="Job Title*", section=section))
        out.append(field(id=len(out), label="Company*", section=section))
        out.append(field(id=len(out), label="Location", section=section))
        out.append(field(id=len(out), type="checkbox", label="I currently work here",
                         section=section, value="on", checked=False))
        for side in ("From*", "To*"):
            out.append(field(id=len(out), type="", label="Month", section=side))
            out.append(field(id=len(out), type="", label="Year", section=side))
        out.append(field(id=len(out), tag="textarea", type="",
                         label="Role Description", section="To*"))
    out.append(field(id=len(out), tag="button", type="", label="Add Another",
                     text="Add Another", group="Role Description", section="To*"))
    out.append(field(id=len(out), label="School or University*",
                     section="Education (Optional) 1"))
    return out


def esko_fields() -> list[dict]:
    """The shape of the real Esko/Phenom dump (fields 12-28): one unnumbered
    heading for every entry, and From*/To* boxes with no section at all."""
    out: list[dict] = []
    for _ in range(2):
        section = "Work Experience :"
        out.append(field(id=len(out), label="Job Title*", section=section))
        out.append(field(id=len(out), label="Company*", section=section))
        out.append(field(id=len(out), label="From*", section=""))
        out.append(field(id=len(out), label="To*", section=""))
        out.append(field(id=len(out), type="checkbox", label="I currently work here",
                         section=section, group="I currently work here",
                         value="false", checked=False))
        out.append(field(id=len(out), tag="textarea", type="",
                         label="Role description", section=section))
    return out


class ProfileJobsTests(ResolverTestCase):
    def test_dates_are_split_the_way_forms_ask_for_them(self) -> None:
        jobs = resolver.profile_jobs({"jobs": [dict(JOBS[0])]})
        self.assertEqual(jobs[0]["start"], "07/2020")
        self.assertEqual(jobs[0]["start_month"], "07")
        self.assertEqual(jobs[0]["start_year"], "2020")
        self.assertEqual(jobs[0]["end_month"], "01")

    def test_other_date_spellings_are_accepted(self) -> None:
        for written in ("Jul 2020", "2020-07", "7/2020", "July 2020"):
            jobs = resolver.profile_jobs(
                {"jobs": [{"title": "T", "company": "C", "start": written}]})
            self.assertEqual(jobs[0]["start"], "07/2020", written)

    def test_a_date_that_cannot_be_read_is_left_empty_not_guessed(self) -> None:
        jobs = resolver.profile_jobs(
            {"jobs": [{"title": "T", "company": "C", "start": "some time in 2020"}]})
        self.assertEqual(jobs[0]["start"], "")
        self.assertEqual(jobs[0]["start_month"], "")

    def test_a_current_job_has_no_end_date(self) -> None:
        for ending in ({"end": "Present"}, {"end": ""}, {"current": True, "end": "01/2026"}):
            jobs = resolver.profile_jobs(
                {"jobs": [{"title": "T", "company": "C", "start": "07/2020", **ending}]})
            self.assertEqual(jobs[0]["current"], "yes", ending)
            self.assertEqual(jobs[0]["end"], "", ending)

    def test_alias_keys_and_rubbish_rows(self) -> None:
        jobs = resolver.profile_jobs({"jobs": [
            {"role": "Engineer", "employer": "Acme", "city": "Pune", "summary": "Work."},
            {"nothing": "useful"},
            "not a dict",
        ]})
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]["title"], jobs[0]["company"]), ("Engineer", "Acme"))
        self.assertEqual((jobs[0]["location"], jobs[0]["description"]), ("Pune", "Work."))

    def test_a_profile_with_no_jobs_gives_nothing(self) -> None:
        self.assertEqual(resolver.profile_jobs({}), [])
        self.assertEqual(resolver.profile_jobs({"jobs": "not a list"}), [])


class WorkEntryTaggingTests(ResolverTestCase):
    def test_workday_date_boxes_are_tied_to_their_entry(self) -> None:
        fields = workday_fields()
        resolver.tag_work_entries(fields, resolver.profile_jobs({"jobs": JOBS}))
        by_entry: dict[int, list[str]] = {}
        for f in fields:
            if f.get("work_entry") is None:
                continue
            by_entry.setdefault(f["work_entry"], []).append(
                f"{f['label']}/{f.get('work_dates', '')}")
        self.assertIn("Month/start", by_entry[0])
        self.assertIn("Year/end", by_entry[0])
        self.assertIn("Month/start", by_entry[1])
        self.assertIn("Role Description/end", by_entry[1])
        # The Add button sits under "To*" with no work words at all; the
        # section opener needs it tagged or it would never be clicked.
        self.assertIn("Add Another/end", by_entry[1])
        # And the block must close at the next real heading.
        school = next(f for f in fields if f["label"] == "School or University*")
        self.assertIsNone(school.get("work_entry"))

    def test_esko_section_less_date_boxes_are_tied_to_their_entry(self) -> None:
        fields = esko_fields()
        resolver.tag_work_entries(fields, resolver.profile_jobs({"jobs": JOBS}))
        entries = [f["work_entry"] for f in fields if f["label"] == "From*"]
        self.assertEqual(entries, [0, 1])

    def test_a_description_box_does_not_start_a_new_entry(self) -> None:
        # "Role description" holds the word role; reading that as a title
        # split every Esko entry in two and lost the second job.
        fields = esko_fields()
        resolver.tag_work_entries(fields, resolver.profile_jobs({"jobs": JOBS}))
        self.assertEqual(max(f["work_pos"] for f in fields if "work_pos" in f), 1)


class WorkEntryResolveTests(ResolverTestCase):
    def _with_jobs(self, fields):
        data = dict(DUMMY_PROFILE, jobs=JOBS)
        profile.PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")
        resolver.tag_work_entries(fields, resolver.profile_jobs(data))
        return fields

    def test_every_part_of_an_entry_comes_from_the_profile(self) -> None:
        fields = self._with_jobs(workday_fields())
        got = {}
        for f in fields:
            if f.get("work_entry") == 1:
                out = resolver.resolve(f, {})
                if out:
                    got.setdefault(f["label"], []).append(out[0])
        self.assertEqual(got["Job Title*"], ["Engineer Intern"])
        self.assertEqual(got["Company*"], ["Northwind Systems"])
        self.assertEqual(got["Location"], ["Pune"])
        self.assertEqual(got["Month"], ["06", "07"])
        self.assertEqual(got["Year"], ["2019", "2020"])
        self.assertEqual(got["Role Description"], ["Rendering work in C++."])

    def test_a_whole_date_box_takes_the_whole_date(self) -> None:
        fields = self._with_jobs(esko_fields())
        starts = [resolver.resolve(f, {}) for f in fields if f["label"] == "From*"]
        self.assertEqual([s[0] for s in starts], ["07/2020", "06/2019"])

    def test_the_currently_here_checkbox_is_reachable_at_last(self) -> None:
        # It always carries a value ("on" on Workday, "false" on Esko), so the
        # blank test could never be true for it and it was unreachable.
        jobs = [dict(JOBS[0], end="Present"), JOBS[1]]
        data = dict(DUMMY_PROFILE, jobs=jobs)
        profile.PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")
        fields = workday_fields()
        resolver.tag_work_entries(fields, resolver.profile_jobs(data))
        boxes = [f for f in fields if f["label"] == "I currently work here"]
        self.assertEqual(resolver.resolve(boxes[0], {}), ("yes", "profile"))
        # Only the current job, and never a tick that is already there.
        self.assertIsNone(resolver.resolve(boxes[1], {}))
        self.assertIsNone(resolver.resolve(dict(boxes[0], checked=True), {}))

    def test_a_radio_in_a_work_entry_is_still_the_models(self) -> None:
        fields = self._with_jobs(workday_fields())
        radio = dict(fields[4], type="radio", label="Employment type", checked=False)
        self.assertIsNone(resolver.resolve(radio, {}))

    def test_an_entry_naming_a_job_the_profile_lacks_is_left_alone(self) -> None:
        fields = workday_fields()
        for f in fields:
            if f["label"] == "Company*" and f["section"].endswith("2"):
                f["value"] = "Some Other Employer"
            if f["label"] == "Job Title*" and f["section"].endswith("2"):
                f["value"] = "Consultant"
        self._with_jobs(fields)
        second = [f for f in fields if f.get("work_pos") == 1]
        self.assertTrue(all(f["work_entry"] == -1 for f in second))
        self.assertTrue(all(resolver.resolve(f, {}) is None for f in second))

    def test_two_jobs_at_one_employer_are_told_apart_by_title(self) -> None:
        # The real profile has exactly this: two roles at the same company.
        fields = workday_fields()
        titles = [f for f in fields if f["label"] == "Job Title*"]
        companies = [f for f in fields if f["label"] == "Company*"]
        titles[0]["value"] = "Engineer Intern"       # the site put them the
        titles[1]["value"] = "Senior Engineer"       # other way round
        for c in companies:
            c["value"] = "Northwind Systems"
        self._with_jobs(fields)
        self.assertEqual(titles[0]["work_entry"], 1)
        self.assertEqual(titles[1]["work_entry"], 0)

    def test_more_entries_on_the_page_than_in_the_profile(self) -> None:
        fields = self._with_jobs(workday_fields() + [
            field(id=99, label="Job Title*", section="Work History (Optional) 3"),
        ])
        spare = next(f for f in fields if f.get("section", "").endswith("3"))
        self.assertIsNone(resolver.resolve(spare, {}))

    def test_without_jobs_in_the_profile_nothing_is_filled(self) -> None:
        fields = workday_fields()
        resolver.tag_work_entries(fields, [])
        self.assertTrue(all(resolver.resolve(f, {}) is None
                            for f in fields if f.get("work_entry") is not None))

    def test_an_entry_the_site_already_filled_is_not_overwritten(self) -> None:
        fields = workday_fields()
        for f in fields:
            if f["label"] == "Job Title*":
                f["value"] = "Senior Engineer"
            if f["label"] == "Company*":
                f["value"] = "Northwind Systems"
        self._with_jobs(fields)
        held = next(f for f in fields if f["label"] == "Job Title*")
        self.assertIsNone(resolver.resolve(held, {}))


class WorkSlotTests(ResolverTestCase):
    def test_the_checkbox_is_decided_before_anything_else(self) -> None:
        # "I currently work here" contains the word work; a looser rule
        # claimed it and the box was filled with a job title.
        box = field(type="checkbox", label="I currently work here", value="on")
        self.assertEqual(resolver.work_slot(box), "current")

    def test_a_day_box_is_left_alone(self) -> None:
        box = field(label="Day", section="From*")
        self.assertEqual(resolver.work_slot(box), "")

    def test_reason_to_leave_is_not_an_end_date(self) -> None:
        # The education rules search for the bare word "to"; next to a work
        # entry that would have written a date into a free-text box.
        self.assertEqual(resolver.work_slot(field(label="Reason to leave")), "")
        self.assertEqual(resolver.work_slot(field(label="To*")), "end")
        self.assertEqual(resolver.work_slot(field(label="Start Date")), "start")

    def test_a_description_beats_a_title(self) -> None:
        self.assertEqual(resolver.work_slot(field(label="Role description")), "description")
        self.assertEqual(resolver.work_slot(field(label="Job Title*")), "title")


class ContactCheckTests(ResolverTestCase):
    """A wrong e-mail means the employer cannot reply, so contact boxes are
    watched after every step - an ATS parsing the resume replaced one."""

    def test_which_boxes_hold_contact_details(self) -> None:
        self.assertEqual(resolver.contact_topic(field(label="Email address*")), "email")
        self.assertEqual(resolver.contact_topic(field(label="Enter Email address (Required)")), "email")
        self.assertEqual(resolver.contact_topic(field(autocomplete="email", label="")), "email")
        self.assertEqual(resolver.contact_topic(field(label="Phone number*", type="tel")), "phone")
        self.assertEqual(resolver.contact_topic(field(label="Mobile")), "phone")
        # Not the candidate's own contact details.
        self.assertEqual(resolver.contact_topic(field(label="Phone Extension")), "")
        self.assertEqual(resolver.contact_topic(field(label="Country Phone Code*")), "")
        self.assertEqual(resolver.contact_topic(field(label="Manager email")), "")
        self.assertEqual(resolver.contact_topic(field(label="City")), "")
        self.assertEqual(resolver.contact_topic(field(tag="select", label="Email address*")), "")

    def test_comparing_what_the_box_shows(self) -> None:
        # The exact failure: an ATS parsed the resume and dropped the underscore.
        self.assertFalse(resolver.same_contact(
            "email", "acandidate@example.invalid", "a_candidate@example.invalid"))
        self.assertTrue(resolver.same_contact(
            "email", "A_Candidate@Example.invalid", "a_candidate@example.invalid"))
        # Phone widgets reformat: spaces, a country code, a national zero.
        for shown in ("9000000000", "+91 90000 00000", "09000000000", "(900) 000-0000"):
            self.assertTrue(resolver.same_contact("phone", shown, "9000000000"), shown)
        self.assertFalse(resolver.same_contact("phone", "9000000001", "9000000000"))
        self.assertTrue(resolver.same_contact("email", "anything", ""))   # nothing to compare


class MaterialWidgetTests(ResolverTestCase):
    def test_mat_select_is_a_dropdown_that_takes_no_typing(self) -> None:
        # ALTEN's Angular Material dropdown: not a <select>, no inner input.
        mat = field(tag="mat-select", role="combobox", haspopup="true",
                    label="Current Salary Currency", text="Select none")
        self.assertTrue(resolver.is_listbox_button(mat))
        self.assertTrue(resolver.is_blank(mat))
        # A real input keeps its normal path, whatever role it carries.
        self.assertFalse(resolver.is_listbox_button(
            field(tag="input", role="combobox", haspopup="listbox", label="City")))
        self.assertFalse(resolver.is_listbox_button(field(tag="select", label="Country")))
        # A plain button without a listbox popup is not a dropdown.
        self.assertFalse(resolver.is_listbox_button(field(tag="button", text="Next")))

    def test_salary_currency_and_period_come_from_the_profile(self) -> None:
        profile.PROFILE_PATH.write_text(
            json.dumps({**DUMMY_PROFILE, "salary_currency": "INR", "salary_period": "Annual"}),
            encoding="utf-8")
        for label in ("Current Salary Currency", "Expected Salary Currency"):
            got = resolver.resolve(field(tag="mat-select", role="combobox", label=label, text="Select none"))
            self.assertEqual(got, ("INR", "profile"), label)
        for label in ("Current Salary Period", "Expected Salary Period"):
            got = resolver.resolve(field(tag="mat-select", role="combobox", label=label, text="Select none"))
            self.assertEqual(got, ("Annual", "profile"), label)
        # The amount box itself is not a currency or period field.
        self.assertNotEqual(
            resolver.resolve(field(label="Current Annual Salary", type="number")),
            ("INR", "profile"))

    def test_an_image_only_upload_is_not_the_resume_slot(self) -> None:
        photo = field(tag="input", type="file", label="", accept=".png, .jpeg, .jpg")
        self.assertFalse(resolver.wants_resume(photo))
        self.assertTrue(resolver.wants_resume(
            field(tag="input", type="file", label="", accept=".doc, .docx, .pdf, .rtf, .txt")))
        # No accept attribute at all: the usual single-upload form.
        self.assertTrue(resolver.wants_resume(field(tag="input", type="file", label="")))


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
