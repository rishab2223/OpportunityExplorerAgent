from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.resume import fit_policy, one_page

RESUME = r"""\documentclass[11pt]{article}
\begin{document}
\section*{Experience}
\noindent \textbf{Initech} \hfill 2020
\begin{itemize}[nosep]
    \item Built things.
\end{itemize}

\section*{Education}
\noindent \textbf{Example University} \hfill 2019

\section*{Certifications}
\begin{itemize}[leftmargin=0.15in, nosep]
    \item \textbf{Lean Six Sigma Yellow Belt} -- American Society for Quality (ASQ).
    \item \textbf{Cisco Certified Network Associate (CCNA)} -- Routing and Switching.
\end{itemize}

\end{document}
"""


def default(index: int) -> fit_policy.FitStep:
    return fit_policy.FitStep.model_validate(fit_policy.DEFAULT_STEPS[index])


class DefaultRuleTests(unittest.TestCase):
    def test_drops_only_the_ccna_bullet(self) -> None:
        out = fit_policy.apply_step(RESUME, default(0))
        self.assertNotIn("CCNA", out)
        self.assertIn("Lean Six Sigma", out)          # the other cert survives
        self.assertIn("Built things.", out)           # experience untouched
        self.assertIn(r"\end{itemize}", out)          # list still closed
        self.assertIn(r"\section*{Certifications}", out)
        self.assertEqual(out.count(r"\end{itemize}"), RESUME.count(r"\end{itemize}"))

    def test_then_the_whole_section(self) -> None:
        out = fit_policy.apply_step(RESUME, default(1))
        self.assertNotIn("Certifications", out)
        self.assertNotIn("Lean Six Sigma", out)
        self.assertIn(r"\section*{Experience}", out)
        self.assertIn(r"\end{document}", out)

    def test_ccna_is_cut_before_the_whole_section(self) -> None:
        labels = [s.label for s in fit_policy.default_policy().steps]
        self.assertEqual(labels, ["Drop the CCNA bullet", "Drop the Certifications section"])


class FitterTests(unittest.TestCase):
    """Compiles are faked: what matters is which steps run and what is left."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tex = Path(self._tmp.name) / "r.tex"
        self.tex.write_text(RESUME, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fit(self, pages: list[int], steps, compile_ok=None):
        counts = iter(pages)
        calls = []

        def fake_compile(path, timeout=0):
            calls.append(Path(path).read_text(encoding="utf-8"))
            if compile_ok is not None and not compile_ok(calls[-1]):
                return None, "! Something's wrong--perhaps a missing \\item."
            return Path(path).with_suffix(".pdf"), ""

        with mock.patch.object(one_page, "compile_tex", side_effect=fake_compile), \
             mock.patch.object(one_page, "page_count", side_effect=lambda _p: next(counts)):
            return one_page.fit_to_one_page(self.tex, steps=steps), calls

    def test_stops_as_soon_as_it_fits(self) -> None:
        result, _ = self._fit([2, 1], [default(0), default(1)])
        self.assertEqual(result.cuts, ["Drop the CCNA bullet"])
        self.assertEqual(result.trail, [("as written", 2), ("Drop the CCNA bullet", 1)])
        self.assertIn("Lean Six Sigma", self.tex.read_text(encoding="utf-8"))

    def test_a_step_that_breaks_the_compile_is_skipped_not_the_policy(self) -> None:
        # The fake compile refuses any source without "Initech", so dropping
        # Experience is the step that "breaks" the document.
        gone = fit_policy.FitStep(kind="drop_section", section="Experience")
        result, calls = self._fit([2, 1], [gone, default(0)],
                                  compile_ok=lambda src: "Initech" in src)
        self.assertEqual(result.cuts, ["Drop the CCNA bullet"])
        self.assertIn("was skipped", result.note)
        final = self.tex.read_text(encoding="utf-8")
        self.assertIn("Initech", final)           # the broken step was undone
        self.assertNotIn("CCNA", final)           # the next step still ran

    def test_still_long_keeps_the_cuts_and_says_so(self) -> None:
        result, _ = self._fit([2, 2, 2], [default(0), default(1)])
        self.assertEqual(len(result.cuts), 2)
        self.assertIn("still 2 pages", result.note)


class PageCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_the_page_tree_count(self) -> None:
        pdf = self.dir / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Pages /Kids [] /Count 2 >>\nendobj\n")
        self.assertEqual(one_page.page_count(pdf), 2)

    def test_falls_back_to_counting_page_objects(self) -> None:
        pdf = self.dir / "y.pdf"
        pdf.write_bytes(b"%PDF-1.4\n<< /Type /Page >>\n<< /Type /Page >>\n")
        self.assertEqual(one_page.page_count(pdf), 2)

    def test_unreadable_file_is_zero(self) -> None:
        self.assertEqual(one_page.page_count(self.dir / "missing.pdf"), 0)
