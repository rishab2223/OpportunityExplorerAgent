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
            "25 LPA": 2_500_000,
            "25 lpa": 2_500_000,
            "25.5 LPA": 2_550_000,
            "25 lakhs": 2_500_000,
            "30 lacs per annum": 3_000_000,
            "2500000": 2_500_000,
            "25,00,000": 2_500_000,
            "Rs. 25,00,000": 2_500_000,
            "₹2500000": 2_500_000,
            "1.2 cr": 12_000_000,
            "80k per month": 960_000,
            "25": 2_500_000,  # a bare small number is lakhs
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(salary.parse_annual_inr(text), want)

    def test_unit_hint_decides_bare_numbers(self) -> None:
        self.assertEqual(salary.parse_annual_inr("25", "lpa"), 2_500_000)
        # Rupees typed into an LPA box stay rupees (the real Madison Logic bug).
        self.assertEqual(salary.parse_annual_inr("2500000", "lpa"), 2_500_000)
        self.assertEqual(salary.parse_annual_inr("200000", "monthly"), 2_400_000)
        self.assertEqual(salary.parse_annual_inr("2500000", "annual"), 2_500_000)

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
        # The bug: the bank's 2500000 landed in an "(in LPA)" box.
        self.assertEqual(salary.for_field("2500000", field("What is your current CTC? (in LPA)*")), "25")
        self.assertEqual(salary.for_field("30 lpa", field("Expected CTC (in lakhs)")), "30")

    def test_number_or_annual_field_gets_rupees(self) -> None:
        self.assertEqual(salary.for_field("25 LPA", field("Current CTC", type="number")), "2500000")
        self.assertEqual(salary.for_field("25 LPA", field("Annual salary in INR")), "2500000")

    def test_monthly_field(self) -> None:
        self.assertEqual(salary.for_field("24 LPA", field("Current monthly salary")), "200000")

    def test_unitless_text_field_keeps_the_words(self) -> None:
        self.assertEqual(salary.for_field("25 LPA", field("Current CTC")), "25 LPA")
        self.assertEqual(salary.for_field("2500000", field("Current CTC")), "2500000")

    def test_ranges_prose_and_other_fields_pass_through(self) -> None:
        self.assertEqual(salary.for_field("25-30 LPA", field("Expected CTC (in LPA)")), "25-30 LPA")
        self.assertEqual(salary.for_field("negotiable", field("Expected CTC (in LPA)")), "negotiable")
        self.assertEqual(salary.for_field("2500000", field("Employee ID")), "2500000")


class NormalizeAndEstimateTests(unittest.TestCase):
    def test_banked_form_is_canonical(self) -> None:
        self.assertEqual(salary.normalize("25", field("Current CTC (in LPA)")), "25 LPA")
        self.assertEqual(salary.normalize("2500000", field("Current CTC")), "25 LPA")
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
