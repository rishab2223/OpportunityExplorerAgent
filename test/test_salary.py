from __future__ import annotations

import unittest

from src.apply import salary


def field(label: str, **kw) -> dict:
    base = {"tag": "input", "type": "text", "label": label, "name": "", "group": "", "value": ""}
    base.update(kw)
    return base


class ParseTests(unittest.TestCase):
    def test_common_spellings(self) -> None:
        cases = {
            "18 LPA": 1_800_000,
            "18 lpa": 1_800_000,
            "25.5 LPA": 2_550_000,
            "18 lakhs": 1_800_000,
            "22 lacs per annum": 2_200_000,
            "1800000": 1_800_000,
            "18,00,000": 1_800_000,
            "Rs. 18,00,000": 1_800_000,
            "₹1800000": 1_800_000,
            "1.2 cr": 12_000_000,
            "80k per month": 960_000,
            "18": 1_800_000,  # a bare small number is lakhs
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(salary.parse_annual_inr(text), want)

    def test_unit_hint_decides_bare_numbers(self) -> None:
        self.assertEqual(salary.parse_annual_inr("18", "lpa"), 1_800_000)
        # Rupees typed into an LPA box stay rupees (the real Madison Logic bug).
        self.assertEqual(salary.parse_annual_inr("1800000", "lpa"), 1_800_000)
        self.assertEqual(salary.parse_annual_inr("200000", "monthly"), 2_400_000)
        self.assertEqual(salary.parse_annual_inr("1800000", "annual"), 1_800_000)

    def test_no_number_is_none(self) -> None:
        self.assertIsNone(salary.parse_annual_inr("as per company standards"))
        self.assertIsNone(salary.parse_annual_inr(""))


class UnitTests(unittest.TestCase):
    def test_field_units(self) -> None:
        self.assertEqual(salary.unit_of(field("What is your current CTC? (in LPA)")), "lpa")
        self.assertEqual(salary.unit_of(field("Current salary (lakhs)")), "lpa")
        self.assertEqual(salary.unit_of(field("Monthly salary")), "monthly")
        self.assertEqual(salary.unit_of(field("Annual salary (INR)")), "annual")
        self.assertEqual(salary.unit_of(field("Current CTC")), "")

    def test_topic(self) -> None:
        self.assertEqual(salary.topic_of(field("What is your current CTC? (in LPA)*")), "current_ctc")
        self.assertEqual(salary.topic_of(field("Expected salary")), "expected_ctc")
        self.assertEqual(salary.topic_of(field("Notice period")), "")
        self.assertEqual(salary.topic_of(field("Annual salary (INR)")), "")
        self.assertTrue(salary.is_salary_field(field("Annual salary (INR)")))
        self.assertFalse(salary.is_salary_field(field("Employee ID")))


class ForFieldTests(unittest.TestCase):
    def test_lpa_field_gets_lakhs(self) -> None:
        # The bug: the bank's 1800000 landed in an "(in LPA)" box.
        self.assertEqual(salary.for_field("1800000", field("What is your current CTC? (in LPA)*")), "18")
        self.assertEqual(salary.for_field("22 lpa", field("Expected CTC (in lakhs)")), "22")

    def test_number_or_annual_field_gets_rupees(self) -> None:
        self.assertEqual(salary.for_field("18 LPA", field("Current CTC", type="number")), "1800000")
        self.assertEqual(salary.for_field("18 LPA", field("Annual salary in INR")), "1800000")

    def test_monthly_field(self) -> None:
        self.assertEqual(salary.for_field("24 LPA", field("Current monthly salary")), "200000")

    def test_unitless_text_field_keeps_the_words(self) -> None:
        self.assertEqual(salary.for_field("18 LPA", field("Current CTC")), "18 LPA")
        self.assertEqual(salary.for_field("1800000", field("Current CTC")), "1800000")

    def test_ranges_prose_and_other_fields_pass_through(self) -> None:
        self.assertEqual(salary.for_field("18-22 LPA", field("Expected CTC (in LPA)")), "18-22 LPA")
        self.assertEqual(salary.for_field("negotiable", field("Expected CTC (in LPA)")), "negotiable")
        self.assertEqual(salary.for_field("1800000", field("Employee ID")), "1800000")


class NormalizeAndEstimateTests(unittest.TestCase):
    def test_banked_form_is_canonical(self) -> None:
        self.assertEqual(salary.normalize("18", field("Current CTC (in LPA)")), "18 LPA")
        self.assertEqual(salary.normalize("1800000", field("Current CTC")), "18 LPA")
        self.assertEqual(salary.normalize("negotiable", field("Expected CTC")), "negotiable")
        self.assertEqual(salary.normalize("60 days", field("Notice period")), "60 days")

    def test_midpoint(self) -> None:
        est = salary.SalaryEstimate(low_lpa=40, high_lpa=42)
        self.assertEqual(salary.midpoint_annual(est), 4_100_000)
        self.assertEqual(salary.midpoint_annual(salary.SalaryEstimate(low_lpa=42, high_lpa=40)), 4_100_000)
        self.assertEqual(salary.midpoint_annual(salary.SalaryEstimate(low_lpa=0, high_lpa=40)), 4_000_000)
        self.assertIsNone(salary.midpoint_annual(salary.SalaryEstimate()))

    def test_prompt_carries_the_context(self) -> None:
        text = salary.estimate_prompt(
            {"title": "Sr. Backend Engineer", "company": "X Co", "description": "Pune, 5+ years"}, "6"
        )
        for needle in ("Sr. Backend Engineer at X Co", "Pune, 5+ years", "YEARS OF EXPERIENCE: 6"):
            self.assertIn(needle, text)
        # Current pay and the rest of the profile stay out: they anchor the estimate.
        self.assertNotIn("LPA", text)
        self.assertNotIn("PROFILE", text)


if __name__ == "__main__":
    unittest.main()


class AbbreviationTests(unittest.TestCase):
    """ECTC and CCTC are how Indian forms label these boxes.

    A box labelled just "ECTC" was not an expected-pay box at all, so the
    estimator - which quotes a band for the role and shows its working - never
    ran, and the figure came from the profile with nothing explained.
    """

    def _field(self, label: str) -> dict:
        return {"label": label, "name": "", "tag": "input", "type": "text",
                "value": "", "group": "", "section": "", "autocomplete": ""}

    def test_expected_abbreviations(self) -> None:
        for label in ("ECTC", "ECTC *", "E-CTC", "Exp CTC", "Expected CTC",
                      "Expected salary"):
            self.assertEqual(salary.topic_of(self._field(label)), "expected_ctc", label)

    def test_current_abbreviations(self) -> None:
        for label in ("CCTC", "Current CTC", "Current CTC (per annum)",
                      "Share your current annual fixed CTC (full number)"):
            self.assertEqual(salary.topic_of(self._field(label)), "current_ctc", label)

    def test_a_bare_ctc_is_left_to_the_model(self) -> None:
        # It usually means current pay, but a form that means expected by it
        # would get the candidate's current salary quoted as their
        # expectation, and guessing wrong that way costs them money.
        for label in ("CTC", "CTC *", "CTC (LPA)"):
            self.assertEqual(salary.topic_of(self._field(label)), "", label)

    def test_the_abbreviations_do_not_collide(self) -> None:
        self.assertNotEqual(salary.topic_of(self._field("ECTC")),
                            salary.topic_of(self._field("CCTC")))
