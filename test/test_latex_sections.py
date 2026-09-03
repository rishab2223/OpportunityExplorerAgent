from __future__ import annotations

import unittest

from src.resume.latex_sections import (
    split_sections,
    strip_fences,
    tailor_latex,
    validate_body,
)

SOURCE = "\n".join(
    [
        r"\documentclass[11pt, letterpaper]{article}",
        r"\usepackage{enumitem}",
        "",
        r"\begin{document}",
        r"\pagestyle{empty}",
        "",
        r"\begin{center}",
        r"    {\Huge \textbf{NAME}}",
        r"\end{center}",
        "",
        r"\section*{Professional Summary}",
        r"\noindent Original summary text.",
        "",
        r"\section*{Skills}",
        r"\begin{itemize}[leftmargin=0.15in, nosep]",
        r"    \item \textbf{Languages:} Python",
        r"\end{itemize}",
        "",
        r"\section*{Education}",
        r"\noindent \textbf{Some University} \hfill 2019",
        "",
        r"\end{document}",
        "",
    ]
)


class SplitSectionsTests(unittest.TestCase):
    def test_finds_all_sections(self) -> None:
        parsed = split_sections(SOURCE)
        self.assertEqual(
            [s.heading for s in parsed.sections],
            ["Professional Summary", "Skills", "Education"],
        )
        self.assertIn(r"\documentclass", parsed.prologue)
        self.assertIn(r"\begin{center}", parsed.prologue)
        self.assertIn(r"\end{document}", parsed.epilogue)

    def test_no_sections(self) -> None:
        source = "\\documentclass{article}\\begin{document}hi\\end{document}"
        parsed = split_sections(source)
        self.assertEqual(parsed.sections, [])
        self.assertEqual(parsed.prologue, source)

    def test_ignores_sections_after_end_document(self) -> None:
        source = SOURCE + "\n\\section*{Ghost}\n"
        parsed = split_sections(source)
        self.assertEqual(len(parsed.sections), 3)


class TailorLatexTests(unittest.TestCase):
    def test_no_edits_returns_nothing(self) -> None:
        result = tailor_latex(SOURCE, [])
        self.assertEqual(result.applied, 0)
        self.assertEqual(result.latex, "")

    def test_replaces_only_named_section(self) -> None:
        body = r"\noindent Tailored summary text for this job posting role."
        result = tailor_latex(SOURCE, [("Professional Summary", body)])
        self.assertEqual(result.applied, 1)
        self.assertEqual(result.problems, [])
        self.assertIn("Tailored summary text", result.latex)
        self.assertNotIn("Original summary text", result.latex)
        # Everything the model did not touch is byte-identical.
        self.assertIn(r"\begin{itemize}[leftmargin=0.15in, nosep]", result.latex)
        self.assertIn(r"\noindent \textbf{Some University} \hfill 2019", result.latex)
        self.assertIn(r"\end{document}", result.latex)
        self.assertTrue(result.latex.startswith(r"\documentclass"))

    def test_heading_match_is_case_insensitive(self) -> None:
        result = tailor_latex(SOURCE, [("professional summary", r"\noindent New summary here.")])
        self.assertEqual(result.applied, 1)

    def test_unknown_heading_is_rejected(self) -> None:
        result = tailor_latex(SOURCE, [("Certifications", r"\noindent Something.")])
        self.assertEqual(result.applied, 0)
        self.assertIn("unknown section heading: 'Certifications'", result.problems[0])

    def test_bad_edit_keeps_that_section_original(self) -> None:
        good = r"\noindent A rewritten summary of comparable length to before."
        bad = r"\begin{itemize} \item unclosed"
        result = tailor_latex(
            SOURCE, [("Professional Summary", good), ("Skills", bad)]
        )
        self.assertEqual(result.applied, 1)
        self.assertEqual(len(result.problems), 1)
        self.assertIn(r"\textbf{Languages:} Python", result.latex)

    def test_length_blowup_is_rejected(self) -> None:
        result = tailor_latex(
            SOURCE, [("Professional Summary", r"\noindent " + "word " * 500)]
        )
        self.assertEqual(result.applied, 0)
        self.assertEqual(result.latex, "")
        self.assertIn("document length", result.problems[0])


class ValidateBodyTests(unittest.TestCase):
    def test_valid_body(self) -> None:
        body = "\\begin{itemize}[nosep]\n    \\item \\textbf{X:} y\n\\end{itemize}"
        self.assertEqual(validate_body(body), [])

    def test_empty_body(self) -> None:
        self.assertEqual(validate_body("   "), ["empty replacement body"])

    def test_forbidden_commands(self) -> None:
        problems = validate_body(r"\section*{Sneaky} text \usepackage{foo}")
        self.assertTrue(any("\\section" in p for p in problems))
        self.assertTrue(any("\\usepackage" in p for p in problems))

    def test_unbalanced_braces(self) -> None:
        self.assertIn("unbalanced braces", validate_body(r"\textbf{oops"))

    def test_escaped_braces_and_comments_ignored(self) -> None:
        self.assertEqual(validate_body(r"open \{ brace % comment with { brace"), [])

    def test_unbalanced_environment(self) -> None:
        problems = validate_body(r"\begin{itemize} \item x")
        self.assertTrue(any("itemize" in p for p in problems))


class StripFencesTests(unittest.TestCase):
    def test_strips_latex_fence(self) -> None:
        self.assertEqual(strip_fences("```latex\n\\item x\n```"), "\\item x")

    def test_leaves_plain_text(self) -> None:
        self.assertEqual(strip_fences("\\item x"), "\\item x")
