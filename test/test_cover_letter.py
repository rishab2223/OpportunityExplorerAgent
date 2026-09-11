from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import pdf_compile
from src.apply import cover_letter

PROFILE = {"full_name": "Test User", "email": "test@example.invalid", "phone": "+91 00000"}
JOB = {"company": "DummyCo", "title": "Software Engineer"}


def field(**kw) -> dict:
    base = {"tag": "textarea", "type": "", "label": "", "name": "", "group": "", "text": ""}
    base.update(kw)
    return base


class DetectionTests(unittest.TestCase):
    def test_recognises_cover_letter_fields(self) -> None:
        for f in (
            field(label="Cover letter"),
            field(label="Cover Letter (optional)"),
            field(name="cover_letter"),
            field(label="Upload your covering letter", type="file", tag="input"),
            field(label="Motivation letter"),
            field(group="Motivation statement", label="Text"),
        ):
            self.assertTrue(cover_letter.is_cover_letter(f), f)

    def test_leaves_other_fields_alone(self) -> None:
        for f in (
            field(label="Why do you want this role?"),
            field(label="Upload resume", type="file", tag="input"),
            field(label="Additional information"),
            field(label="Letter grade"),
            field(label="Full name", tag="input"),
        ):
            self.assertFalse(cover_letter.is_cover_letter(f), f)


class EscapeTests(unittest.TestCase):
    def test_every_special_character(self) -> None:
        self.assertEqual(cover_letter.escape_latex("100% & more"), r"100\% \& more")
        self.assertEqual(cover_letter.escape_latex("a_b"), r"a\_b")
        self.assertEqual(cover_letter.escape_latex("$5 #1"), r"\$5 \#1")
        self.assertEqual(cover_letter.escape_latex("{x}"), r"\{x\}")
        self.assertEqual(cover_letter.escape_latex("~^"), r"\textasciitilde{}\textasciicircum{}")

    def test_backslash_escaped_first_not_doubled(self) -> None:
        # If the backslash were escaped last, the replacements above would be
        # mangled into \textbackslash{}% and friends.
        self.assertEqual(cover_letter.escape_latex("a\\b"), r"a\textbackslash{}b")
        self.assertEqual(cover_letter.escape_latex("50% \\ x"), r"50\% \textbackslash{} x")

    def test_plain_text_untouched(self) -> None:
        self.assertEqual(cover_letter.escape_latex("Hello there."), "Hello there.")


class FrameTests(unittest.TestCase):
    def test_bare_body_gets_greeting_and_signoff(self) -> None:
        framed = cover_letter.frame("First.\n\nSecond.", JOB, PROFILE)
        self.assertEqual(
            framed, "Dear DummyCo team,\n\nFirst.\n\nSecond.\n\nRegards,\nTest User"
        )

    def test_existing_greeting_and_signoff_kept_untouched(self) -> None:
        text = "Dear DummyCo folks,\n\nBody.\n\nSincerely,\nMe"
        self.assertEqual(cover_letter.frame(text, JOB, PROFILE), text)

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(cover_letter.frame("   ", JOB, PROFILE), "")


class BuildPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_letter_rejected(self) -> None:
        path, error = cover_letter.render_tex("   ", self.dir, JOB, PROFILE)
        self.assertIsNone(path)
        self.assertIn("empty", error)

    def test_writes_tex_with_name_and_paragraphs(self) -> None:
        cover_letter.render_tex("First para.\n\nSecond para.", self.dir, JOB, PROFILE)
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn("Test User", tex)
        self.assertIn("First para.", tex)
        self.assertIn("Second para.", tex)
        self.assertIn(r"\end{document}", tex)
        self.assertIn("Regards", tex)

    def test_letter_is_addressed_to_the_company(self) -> None:
        cover_letter.render_tex("Body.", self.dir, JOB, PROFILE)
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn(f"Dear {JOB['company']} team,", tex)

    def test_salutation_without_a_company_name(self) -> None:
        cover_letter.render_tex("Body.", self.dir, {"company": "", "title": "X"}, PROFILE)
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn("Dear Hiring Team,", tex)

    def test_user_typed_greeting_wins_over_the_template(self) -> None:
        # The user often addresses the company themselves in the modal; the
        # template must not add a second "Dear ..." on top.
        cover_letter.render_tex("Dear Autter folks,\n\nBody.", self.dir, JOB, PROFILE)
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn("Dear Autter folks,", tex)
        self.assertEqual(tex.lower().count("dear "), 1)

    def test_user_typed_signoff_wins_over_the_template(self) -> None:
        cover_letter.render_tex(
            "Body.\n\nSincerely,\nTest User", self.dir, JOB, PROFILE
        )
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn("Sincerely,", tex)
        self.assertNotIn("Regards", tex)

    def test_special_characters_do_not_break_the_source(self) -> None:
        cover_letter.render_tex("I cut costs by 30% & improved a_b.", self.dir, JOB, PROFILE)
        tex = next(self.dir.glob("cover_*.tex")).read_text(encoding="utf-8")
        self.assertIn(r"30\%", tex)
        self.assertIn(r"\&", tex)
        self.assertIn(r"a\_b", tex)

    def test_compiles_when_a_toolchain_exists(self) -> None:
        path, error = cover_letter.build_pdf(
            "First para.\n\nSecond para with 100% & symbols.", self.dir, JOB, PROFILE
        )
        if pdf_compile.toolchain():
            self.assertIsNotNone(path, error)
            self.assertTrue(path.exists() and path.suffix == ".pdf")
        else:
            self.assertIsNone(path)
            self.assertIn("toolchain", error)

    def test_filename_is_sanitised(self) -> None:
        job = {"company": "A/B: Co", "title": "Eng?"}
        cover_letter.render_tex("Body.", self.dir, job, PROFILE)
        name = next(self.dir.glob("cover_*.tex")).name
        for bad in '/\\:*?"<>|':
            self.assertNotIn(bad, name)
