from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from src import pdf_compile
from src.web.app import default_resume_pdf


def cfg_with(path: str) -> SimpleNamespace:
    return SimpleNamespace(resume=SimpleNamespace(local_path=path))


class DefaultResumePdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pdf_resume_served_directly(self) -> None:
        pdf = self.dir / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 x")
        path, error, warning = default_resume_pdf(cfg_with(str(pdf)))
        self.assertEqual(path, pdf)
        self.assertEqual((error, warning), ("", ""))

    def test_missing_file(self) -> None:
        path, error, _ = default_resume_pdf(cfg_with(str(self.dir / "nope.tex")))
        self.assertIsNone(path)
        self.assertIn("not found", error)

    def test_unset_path(self) -> None:
        path, error, _ = default_resume_pdf(cfg_with(""))
        self.assertIsNone(path)
        self.assertIn("not set", error)

    def test_tex_resume_compiles_or_reports_toolchain(self) -> None:
        tex = self.dir / "resume.tex"
        tex.write_text(
            "\\documentclass{article}\\begin{document}hello\\end{document}",
            encoding="utf-8",
        )
        path, error, _ = default_resume_pdf(cfg_with(str(tex)))
        if pdf_compile.toolchain():
            self.assertIsNotNone(path, error)
            self.assertTrue(path.suffix == ".pdf" and path.exists())
        else:
            self.assertIsNone(path)
            self.assertIn("toolchain", error)

    def test_falls_back_to_an_existing_pdf_when_compiling_fails(self) -> None:
        """The real case: you edit the .tex, so the old PDF is stale, but the
        recompile cannot run. A stale resume beats no resume - but say so."""
        stale = self.dir / "resume.pdf"
        stale.write_bytes(b"%PDF-1.4 previously compiled")
        tex = self.dir / "resume.tex"
        tex.write_text("\\this is not valid latex at all", encoding="utf-8")
        os.utime(tex, (time.time() + 10, time.time() + 10))  # edited after the PDF
        path, error, warning = default_resume_pdf(cfg_with(str(tex)))
        self.assertEqual(path, stale)
        self.assertEqual(error, "")
        self.assertIn("previously compiled", warning)

    def test_up_to_date_pdf_is_used_without_recompiling(self) -> None:
        tex = self.dir / "resume.tex"
        tex.write_text("\\this is not valid latex at all", encoding="utf-8")
        fresh = self.dir / "resume.pdf"
        fresh.write_bytes(b"%PDF-1.4 current")
        path, error, warning = default_resume_pdf(cfg_with(str(tex)))
        self.assertEqual(path, fresh)
        self.assertEqual((error, warning), ("", ""))

    def test_no_pdf_and_failed_compile_reports_the_error(self) -> None:
        tex = self.dir / "resume.tex"
        tex.write_text("\\this is not valid latex at all", encoding="utf-8")
        path, error, _ = default_resume_pdf(cfg_with(str(tex)))
        self.assertIsNone(path)
        self.assertTrue(error)
