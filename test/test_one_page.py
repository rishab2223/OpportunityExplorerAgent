from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.resume import one_page

RESUME = r"""\documentclass[11pt]{article}
\begin{document}
\section*{Experience}
\noindent \textbf{Cadence} \hfill 2020
\begin{itemize}[nosep]
    \item Built things.
\end{itemize}

\section*{Education}
\noindent \textbf{NorthCap University} \hfill 2019

\section*{Certifications}
\begin{itemize}[leftmargin=0.15in, nosep]
    \item \textbf{Lean Six Sigma Yellow Belt} -- American Society for Quality (ASQ).
    \item \textbf{Cisco Certified Network Associate (CCNA)} -- Routing and Switching.
\end{itemize}

\end{document}
"""


class DropItemTests(unittest.TestCase):
    def test_drops_only_the_ccna_bullet(self) -> None:
        out = one_page.drop_item(RESUME, "ccna")
        self.assertNotIn("CCNA", out)
        self.assertIn("Lean Six Sigma", out)          # the other cert survives
        self.assertIn("Built things.", out)           # experience untouched
        self.assertIn(r"\end{itemize}", out)          # list still closed
        self.assertIn(r"\section*{Certifications}", out)

    def test_absent_needle_returns_empty(self) -> None:
        self.assertEqual(one_page.drop_item(RESUME, "kubernetes"), "")

    def test_last_item_does_not_swallow_the_list_end(self) -> None:
        out = one_page.drop_item(RESUME, "ccna")
        self.assertEqual(out.count(r"\end{itemize}"), RESUME.count(r"\end{itemize}"))
        self.assertEqual(out.count(r"\begin{itemize}"), RESUME.count(r"\begin{itemize}"))


class DropSectionTests(unittest.TestCase):
    def test_removes_heading_and_body(self) -> None:
        out = one_page.drop_section(RESUME, "certifications")
        self.assertNotIn("Certifications", out)
        self.assertNotIn("CCNA", out)
        self.assertNotIn("Lean Six Sigma", out)
        self.assertIn(r"\section*{Experience}", out)
        self.assertIn(r"\section*{Education}", out)
        self.assertIn(r"\end{document}", out)

    def test_absent_section_returns_empty(self) -> None:
        self.assertEqual(one_page.drop_section(RESUME, "publications"), "")

    def test_heading_match_is_case_insensitive(self) -> None:
        self.assertTrue(one_page.drop_section(RESUME, "CERTIFICATIONS"))


class CutOrderTests(unittest.TestCase):
    def test_ccna_is_cut_before_the_whole_section(self) -> None:
        labels = [c[0] for c in one_page.CUTS]
        self.assertEqual(labels.index("CCNA certification"), 0)
        self.assertLess(
            labels.index("CCNA certification"), labels.index("Certifications section")
        )

    def test_nothing_else_is_ever_cut(self) -> None:
        targets = {c[2] for c in one_page.CUTS}
        self.assertEqual(targets, {"ccna", "certifications"})


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
