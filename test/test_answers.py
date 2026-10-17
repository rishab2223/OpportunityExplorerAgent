from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import answers, history


class BankTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._original_path
        self._tmp.cleanup()


class QuestionKeyTests(BankTestCase):
    def test_phrasings_collapse_to_one_topic(self) -> None:
        for phrasing in ("Notice period", "What is your notice period?",
                         "Notice Period (in days)", "notice"):
            self.assertEqual(answers.question_key(phrasing), "notice_period", phrasing)

    def test_pay_questions_that_name_the_noun_first(self) -> None:
        """Barracuda asked "What are your overall compensation expectations?".

        The pattern wanted the qualifier first ("expected salary"), so the
        commoner prose order matched nothing: no salary estimate ran for the
        job, and the saved figure was typed in whatever the role was worth.
        """
        for phrasing in ("What are your overall compensation expectations?",
                         "Salary expectations",
                         "What are your salary requirements?",
                         "Compensation expectation (annual)",
                         "Remuneration expected",
                         "Expected CTC", "Desired salary"):
            self.assertEqual(answers.question_key(phrasing), "expected_ctc", phrasing)
        # The mirror image, which missed the same way.
        for phrasing in ("Salary paid by your current employer",
                         "What is your compensation today?",
                         "Current CTC"):
            self.assertEqual(answers.question_key(phrasing), "current_ctc", phrasing)

    def test_pay_questions_stay_apart(self) -> None:
        # Widening one must not swallow the other, or an expected-pay answer
        # goes into a current-pay box.
        self.assertNotEqual(answers.question_key("Current salary"), "expected_ctc")
        self.assertNotEqual(answers.question_key("Expected salary"), "current_ctc")

    def test_work_authorization_variants(self) -> None:
        for phrasing in ("Are you legally authorized to work in India?",
                         "Are you legally entitled to work in the country you are applying for?",
                         "Work authorization status"):
            self.assertEqual(answers.question_key(phrasing), "work_authorization", phrasing)
        # Another country is another question (see QualifiedKeyTests).
        self.assertEqual(answers.question_key("Are you authorised to work in the United States?"),
                         "work_authorization:us")

    def test_group_carries_the_question_for_bare_options(self) -> None:
        # A radio labelled just "Yes" keys on its group text.
        self.assertEqual(
            answers.question_key("Yes", "Do you require visa sponsorship?"),
            "sponsorship",
        )

    def test_unknown_question_falls_back_to_normalized_text(self) -> None:
        self.assertEqual(
            answers.question_key("Favourite programming language?"),
            "favourite programming language",
        )

    def test_empty(self) -> None:
        self.assertEqual(answers.question_key(""), "")


class ClassifyTests(BankTestCase):
    def test_sensitive_topics(self) -> None:
        self.assertEqual(answers.classify("Do you require sponsorship?"), "sensitive")
        self.assertEqual(answers.classify("Gender"), "sensitive")
        self.assertEqual(answers.classify("Yes", "Any criminal convictions?"), "sensitive")

    def test_secret_wins(self) -> None:
        self.assertEqual(answers.classify("Enter the OTP"), "secret")
        self.assertEqual(answers.classify("Password"), "secret")

    def test_neutral(self) -> None:
        self.assertEqual(answers.classify("Notice period"), "neutral")
        self.assertEqual(answers.classify("Favourite language"), "neutral")


class RememberRecallTests(BankTestCase):
    def test_round_trip_across_phrasings(self) -> None:
        answers.remember("What is your notice period?", "60 days")
        entry = answers.recall("Notice Period (in days)")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["answer"], "60 days")
        self.assertEqual(entry["kind"], "neutral")

    def test_sensitive_kind_recorded(self) -> None:
        answers.remember("Do you require visa sponsorship?", "No")
        entry = answers.recall("Will you need sponsorship?")
        self.assertEqual(entry["kind"], "sensitive")

    def test_secrets_never_stored(self) -> None:
        self.assertEqual(answers.remember("Enter the OTP", "123456"), "")
        self.assertEqual(answers.remember("Notice period", "the password is hunter2"), "")
        self.assertIsNone(answers.recall("Enter the OTP"))

    def test_company_specific_never_stored(self) -> None:
        self.assertEqual(
            answers.remember("Why do you want to work at Capgemini?",
                             "Because...", company="Capgemini"),
            "",
        )
        # Same question without the company mention is fine.
        self.assertNotEqual(
            answers.remember("Why this role?", "Because...", company="Capgemini"), ""
        )

    def test_recall_miss_and_forget(self) -> None:
        self.assertIsNone(answers.recall("Never asked"))
        key = answers.remember("Notice period", "60 days")
        self.assertTrue(answers.forget(key))
        self.assertIsNone(answers.recall("Notice period"))
        self.assertFalse(answers.forget(key))

    def test_usage_counter_orders_entries(self) -> None:
        answers.remember("Notice period", "60 days")
        answers.remember("GitHub profile", "https://github.com/x")
        for _ in range(3):
            answers.recall("Notice period")
        rows = answers.entries()
        self.assertEqual(rows[0]["question_key"], "notice_period")
        self.assertEqual(len(rows), 2)


class PhoneTopicScopeTests(unittest.TestCase):
    def test_phone_topic_is_the_plain_question_only(self) -> None:
        from src.answers import question_key

        self.assertEqual(question_key("Phone number"), "phone")
        self.assertEqual(question_key("Mobile"), "phone")
        self.assertEqual(question_key("Contact Number"), "phone")
        # Workday's neighbours must NOT share the key: "Mobile" (a device
        # type) was replayed as the country code and the extension.
        self.assertNotEqual(question_key("Phone Device Type"), "phone")
        self.assertNotEqual(question_key("Country Phone Code"), "phone")
        self.assertNotEqual(question_key("Phone Extension"), "phone")
        self.assertEqual(question_key("Email address"), "email")
        self.assertNotEqual(question_key("Email preferences"), "email")


class TotalExperienceScopeTests(unittest.TestCase):
    """One skill's experience must never be stored as the whole career's.

    LinkedIn Easy Apply asks the same question per skill. "How many years of
    Travel Arrangements experience do you have?", answered 0 - correctly - was
    saved under the total_experience topic and replayed into nineteen later
    applications, including a "Years of work experience *" box on a profile
    that says six years.
    """

    def test_a_question_about_the_whole_career(self) -> None:
        from src.answers import question_key

        for label in ("Years of work experience *",
                      "How many years of work experience do you have?",
                      "Total experience in years",
                      "Experience (in years)",
                      "Total years of professional experience",
                      "How much work experience do you have in years"):
            self.assertEqual(question_key(label), "total_experience", label)

    def test_a_question_about_one_skill_is_its_own_question(self) -> None:
        from src.answers import question_key

        for label in ("How many years of Travel Arrangements experience do you have?",
                      "How many years of work experience do you have with Python?",
                      "How many years of work experience do you have with Kubernetes?",
                      "Years of experience in embedded systems"):
            self.assertNotEqual(question_key(label), "total_experience", label)

    def test_two_skills_do_not_share_an_answer(self) -> None:
        from src.answers import question_key

        self.assertNotEqual(
            question_key("How many years of work experience do you have with Python?"),
            question_key("How many years of work experience do you have with Java?"),
        )

    def test_relevant_experience_is_not_total_experience(self) -> None:
        # A different question, and it must not overwrite the total.
        from src.answers import question_key

        self.assertNotEqual(question_key("Years of relevant experience"),
                            "total_experience")


class TotalExperienceLabelShapesTests(unittest.TestCase):
    def test_common_decorations_still_mean_the_whole_career(self) -> None:
        from src.answers import question_key

        for label in ("Years of experience (required)",
                      "Total experience till date",
                      "Total work experience, including internships",
                      "Years of full time experience *"):
            self.assertEqual(question_key(label), "total_experience", label)


class QualifiedKeyTests(BankTestCase):
    """Questions that share a topic but not an answer (code review, Oct 2026).
    Each pair used to share one saved answer, which beat the model's."""

    def setUp(self) -> None:
        super().setUp()
        from src.apply import profile
        self._profile = profile
        self._original_profile = profile.PROFILE_PATH
        profile.PROFILE_PATH = Path(self._tmp.name) / "apply_profile.json"   # no profile: India

    def tearDown(self) -> None:
        self._profile.PROFILE_PATH = self._original_profile
        super().tearDown()

    def assertApart(self, a: str, b: str) -> None:
        self.assertNotEqual(answers.question_key(a), answers.question_key(b), f"{a} / {b}")

    def test_another_country_is_another_question(self) -> None:
        self.assertApart("Are you authorized to work in India?", "Are you authorized to work in the United States?")
        self.assertApart("Will you require visa sponsorship?", "Will you require sponsorship to work in the UK?")
        # The candidate's own country is the plain question.
        self.assertEqual(answers.question_key("Are you legally authorized to work in India?"),
                         answers.question_key("Are you legally entitled to work in the country you are applying for?"))

    def test_a_check_is_not_a_conviction(self) -> None:
        self.assertEqual(answers.question_key("Are you willing to undergo a background check?"), "background_check")
        self.assertEqual(answers.question_key("Have you ever been convicted of a crime?"), "criminal_record")

    def test_holding_a_visa_is_not_needing_a_sponsor(self) -> None:
        self.assertEqual(answers.question_key("Do you hold a valid passport and visa?"), "visa_held")
        self.assertEqual(answers.question_key("Will you now or in the future require visa sponsorship?"),
                         "sponsorship")

    def test_moving_is_not_help_with_moving(self) -> None:
        self.assertApart("Are you willing to relocate?", "Do you require relocation assistance?")

    def test_each_way_of_working_is_its_own_question(self) -> None:
        self.assertApart("Are you open to remote work?", "Are you comfortable working onsite 5 days a week?")

    def test_serving_notice_is_not_the_notice_period(self) -> None:
        self.assertApart("Are you currently serving your notice period?", "Notice period")

    def test_how_you_heard_is_not_your_linkedin(self) -> None:
        self.assertEqual(answers.question_key("How did you hear about us? (LinkedIn, Naukri, Referral)"),
                         "referral_source")
        self.assertNotEqual(answers.question_key(
            "I agree to allow my application statuses to be visible inside LinkedIn Recruiter"), "linkedin_url")
        self.assertEqual(answers.question_key("LinkedIn Profile"), "linkedin_url")
        self.assertNotEqual(answers.question_key("Do you have experience with GitHub Actions?"), "github_url")

    def test_expected_pay_that_mentions_current_pay_is_expected(self) -> None:
        for label in ("Expected CTC (Current CTC + hike %)", "Expected salary compared to your current salary"):
            self.assertEqual(answers.question_key(label), "expected_ctc", label)

    def test_an_answer_saved_before_the_split_stays_with_its_question(self) -> None:
        # Saved under the plain key by an older version: the US answer.
        answers.remember("Are you authorized to work in the United States?", "No")
        from src import db
        conn = db.connect()
        with conn:
            conn.execute("UPDATE known_answers SET question_key = 'work_authorization'")
        conn.close()
        answers._invalidate()
        self.assertIsNone(answers.recall("Are you authorized to work in India?"))
        self.assertEqual(answers.recall("Are you authorised to work in the US?")["answer"], "No")

    def test_a_yes_never_answers_an_open_question(self) -> None:
        answers.remember("Are you open to commuting into our Pune office 3 days a week? "
                         "(on-site during probation, hybrid after)", "yes")
        self.assertIsNone(answers.saved_for("Preferred work mode"))
        # And a link topic never answers with a word.
        answers.remember("LinkedIn", "yes")
        self.assertIsNone(answers.saved_for("LinkedIn Profile URL"))
