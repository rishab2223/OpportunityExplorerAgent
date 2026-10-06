from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import history
from src.resume import fit_policy as fp
from src.resume import one_page
from src.web import fit_chat, runs

RESUME = r"""\documentclass[11pt, letterpaper]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\titlespacing{\section}{0pt}{1ex}{1ex}
\begin{document}
\section*{Summary}
Engineer.
\section*{Experience}
\begin{itemize}
  \item Built a CCNA lab tool.
  \item Shipped APIs.
\end{itemize}
\section*{Certifications}
\begin{itemize}
  \item AWS Solutions Architect
  \item CCNA
\end{itemize}
\end{document}
"""


def step(**kw) -> fp.FitStep:
    return fp.FitStep.model_validate(kw)


def default(index: int) -> fp.FitStep:
    return fp.FitStep.model_validate(fp.DEFAULT_STEPS[index])


class StepTests(unittest.TestCase):
    def test_layout_steps_edit_the_preamble_in_place(self) -> None:
        out = fp.apply_step(RESUME, step(kind="layout", setting="margin", value=0.6))
        self.assertIn(r"\usepackage[margin=0.6in]{geometry}", out)
        out = fp.apply_step(RESUME, step(kind="layout", setting="font_size", value=10))
        self.assertIn(r"\documentclass[10pt, letterpaper]{article}", out)
        out = fp.apply_step(RESUME, step(kind="layout", setting="section_spacing", value=0.5))
        self.assertIn(r"\titlespacing{\section}{0pt}{0.5ex}{0.5ex}", out)
        out = fp.apply_step(RESUME, step(kind="layout", setting="item_spacing", value=1))
        again = fp.apply_step(out, step(kind="layout", setting="item_spacing", value=0))
        self.assertEqual(again.count(fp.ITEM_SPACING_MARK), 1)    # replaced, not stacked
        self.assertIn("itemsep=0pt", again)
        self.assertLess(again.index("setlist"), again.index(r"\begin{document}"))

    def test_a_layout_already_there_changes_nothing(self) -> None:
        self.assertEqual(fp.apply_step(RESUME, step(kind="layout", setting="margin", value=0.75)), "")

    def test_a_bullet_is_dropped_only_in_its_section(self) -> None:
        out = fp.apply_step(RESUME, step(kind="drop_item", section="Certifications", match="ccna"))
        self.assertIn("Built a CCNA lab tool", out)          # Experience untouched
        self.assertNotIn(r"\item CCNA", out)
        self.assertIn("AWS Solutions Architect", out)

    def test_sections_dropped_and_replaced(self) -> None:
        out = fp.apply_step(RESUME, step(kind="drop_section", section="certifications"))
        self.assertNotIn("Certifications", out)
        short = step(kind="replace_section", section="Certifications",
                     latex_body="AWS Solutions Architect; CCNA")
        out = fp.apply_step(RESUME, short)
        self.assertIn("AWS Solutions Architect; CCNA", out)
        self.assertNotIn(r"\item AWS", out)
        self.assertEqual(fp.apply_step(RESUME, step(kind="drop_section", section="Publications")), "")

    def test_steps_are_checked(self) -> None:
        for bad in ({"kind": "layout", "setting": "margin", "value": 0.2},
                    {"kind": "layout", "setting": "font_size", "value": 10.5},
                    {"kind": "layout", "value": 1},
                    {"kind": "drop_item", "match": " "},
                    {"kind": "drop_section"},
                    {"kind": "replace_section", "section": "Skills", "latex_body": r"\section{x}"},
                    {"kind": "replace_section", "section": "Skills", "latex_body": r"\begin{itemize}"}):
            steps, problems = fp.parse_steps([bad])
            self.assertEqual((steps, len(problems)), ([], 1), str(bad))

    def test_a_bullet_with_a_nested_list_goes_whole(self) -> None:
        nested = RESUME.replace(
            "  \\item Shipped APIs.\n",
            "  \\item Shipped APIs.\n  \\begin{itemize}\n    \\item[] Handled deadlocks.\n  \\end{itemize}\n")
        out = fp.apply_step(nested, step(kind="drop_item", section="Experience", match="Shipped"))
        self.assertNotIn("Shipped APIs", out)
        self.assertNotIn("deadlocks", out)                       # its nested list went with it
        self.assertEqual(out.count(r"\begin{itemize}"), out.count(r"\end{itemize}"))
        self.assertIn("Built a CCNA lab tool", out)

    def test_the_only_bullet_takes_its_list_with_it(self) -> None:
        lone = RESUME.replace("  \\item Shipped APIs.\n", "")
        lone = lone.replace("  \\item Built a CCNA lab tool.\n", "  \\item Only bullet here.\n")
        out = fp.apply_step(lone, step(kind="drop_item", match="only bullet"))
        self.assertNotIn("Only bullet", out)
        self.assertEqual(out.count(r"\begin{itemize}"), 1)      # Certifications' list only
        self.assertIn(r"\section*{Experience}", out)             # the section itself stays

    def test_layout_only_ever_tightens(self) -> None:
        for setting, looser in (("margin", 0.9), ("font_size", 12), ("section_spacing", 1.5)):
            self.assertEqual(fp.apply_step(RESUME, step(kind="layout", setting=setting, value=looser)), "",
                             setting)

    def test_the_geometry_command_form_and_a_starred_titlespacing(self) -> None:
        other = RESUME.replace(r"\usepackage[margin=0.75in]{geometry}",
                               "\\usepackage{geometry}\n\\geometry{margin=0.75in}")
        out = fp.apply_step(other, step(kind="layout", setting="margin", value=0.6))
        self.assertIn(r"\geometry{margin=0.6in}", out)
        self.assertEqual(out.count("geometry"), 2)              # no second \usepackage
        starred = RESUME.replace(r"\titlespacing{\section}", r"\titlespacing*{\section}")
        out = fp.apply_step(starred, step(kind="layout", setting="section_spacing", value=0.5))
        self.assertIn(r"\titlespacing*{\section}{0pt}{0.5ex}{0.5ex}", out)

    def test_item_spacing_reaches_lists_with_their_own_options(self) -> None:
        # A list's own [nosep] beats a global \setlist, so each list is told too.
        tight = RESUME.replace(r"\begin{itemize}", r"\begin{itemize}[leftmargin=0.15in, nosep]")
        out = fp.apply_step(tight, step(kind="layout", setting="item_spacing", value=2))
        self.assertIn("nosep, itemsep=2pt", out)
        self.assertIn(r"\setlist[itemize]{itemsep=2pt}", out)
        # Plain lists only need the global line.
        out = fp.apply_step(RESUME, step(kind="layout", setting="item_spacing", value=2))
        self.assertIn(r"\setlist[itemize]{itemsep=2pt}", out)
        self.assertIn(r"\begin{itemize}", out)

    def test_a_fenced_body_loses_its_fence(self) -> None:
        short = step(kind="replace_section", section="Certifications",
                     latex_body="```latex\nAWS; CCNA\n```")
        self.assertEqual(short.latex_body, "AWS; CCNA")

    def test_a_section_the_resume_lacks_is_named(self) -> None:
        problems = fp.missing_sections([step(kind="drop_section", section="Certification")], RESUME)
        self.assertEqual(len(problems), 1)
        self.assertIn("Certification", problems[0])
        self.assertEqual(fp.missing_sections([default(0)], RESUME), [])

    def test_labels_are_written_when_missing(self) -> None:
        self.assertEqual(step(kind="layout", setting="margin", value=0.6).label, "Margins to 0.6in")
        self.assertEqual(step(kind="drop_section", section="Projects").label, "Drop the Projects section")


class RestoreTests(unittest.TestCase):
    def test_what_the_old_rule_cut_comes_back(self) -> None:
        for cut in fp.DEFAULT_STEPS:
            trimmed = fp.apply_step(RESUME, fp.FitStep.model_validate(cut))
            self.assertEqual(fp.restore_trims(trimmed, RESUME), RESUME, cut["label"])

    def test_tailoring_is_kept(self) -> None:
        tailored = RESUME.replace("Engineer.", "Backend engineer, AWS.")
        trimmed = fp.apply_step(tailored, fp.FitStep.model_validate(fp.DEFAULT_STEPS[1]))
        back = fp.restore_trims(trimmed, RESUME)
        self.assertIn("Backend engineer, AWS.", back)
        self.assertIn(r"\section*{Certifications}", back)
        self.assertEqual(fp.restore_trims(tailored, RESUME), tailored)

    def test_a_section_only_the_tailored_resume_has_keeps_its_place(self) -> None:
        tailored = RESUME.replace(r"\section*{Experience}", "\\section*{Highlights}\nTwo things.\n\\section*{Experience}")
        trimmed = fp.apply_step(tailored, fp.FitStep.model_validate(fp.DEFAULT_STEPS[1]))
        back = fp.restore_trims(trimmed, RESUME)
        order = [h for h in ("Summary", "Highlights", "Experience", "Certifications")
                 if f"\\section*{{{h}}}" in back]
        self.assertEqual(order, ["Summary", "Highlights", "Experience", "Certifications"])
        self.assertLess(back.index("Highlights"), back.index("Experience"))


class PolicyFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = fp.POLICY_PATH
        fp.POLICY_PATH = Path(self._tmp.name) / "policy.json"

    def tearDown(self) -> None:
        fp.POLICY_PATH = self._path
        self._tmp.cleanup()

    def test_default_until_saved(self) -> None:
        self.assertIsNone(fp.saved_policy())
        self.assertEqual([s.label for s in fp.load().steps],
                         ["Drop the CCNA bullet", "Drop the Certifications section"])
        self.assertIn("1. Drop the CCNA bullet", fp.prompt_text())
        fp.save([step(kind="layout", setting="margin", value=0.6)], "Never drop AWS.")
        self.assertEqual([s.label for s in fp.load().steps], ["Margins to 0.6in"])
        text = fp.prompt_text()
        self.assertIn("Margins to 0.6in", text)
        self.assertIn("Never drop AWS.", text)
        fp.forget()
        self.assertIsNone(fp.saved_policy())

    def test_an_empty_policy_cuts_nothing(self) -> None:
        fp.save([], "")
        self.assertIn("nothing is cut", fp.prompt_text())

    def test_the_fitter_runs_the_saved_policy(self) -> None:
        fp.save([step(kind="layout", setting="margin", value=0.5)], "")
        pages = iter([2, 1])
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "r.tex"
            tex.write_text(RESUME, encoding="utf-8")
            with mock.patch.object(one_page, "compile_tex", return_value=(tex.with_suffix(".pdf"), "")), \
                 mock.patch.object(one_page, "page_count", side_effect=lambda _p: next(pages)):
                result = one_page.fit_to_one_page(tex)
            self.assertEqual(result.cuts, ["Margins to 0.5in"])
            self.assertEqual(result.trail, [("as written", 2), ("Margins to 0.5in", 1)])
            self.assertIn("margin=0.5in", tex.read_text(encoding="utf-8"))


class FitChatTests(unittest.TestCase):
    """The tab's server side, with a fake model and a fake compile."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (fp.POLICY_PATH, fit_chat.DRAFT_PATH, fit_chat.PREVIEW_DIR, runs.OUTPUT_DIR,
                       history.DB_PATH)
        fp.POLICY_PATH = root / "policy.json"
        fit_chat.DRAFT_PATH = root / "draft.json"
        fit_chat.PREVIEW_DIR = root / "preview"
        runs.OUTPUT_DIR = root / "outputs"
        history.DB_PATH = root / "history.db"
        base = root / "resume.tex"
        base.write_text(RESUME, encoding="utf-8")
        cfg = mock.MagicMock()
        cfg.resume.local_path = str(base)
        self._patches = [mock.patch.object(fit_chat.run_options, "effective_config", return_value=cfg),
                         mock.patch.object(fit_chat, "fit_to_one_page", side_effect=self.fake_fit)]
        for p in self._patches:
            p.start()
        # A run whose tailored resume lost its Certifications to the old rule.
        stamp = "20261006T103501"
        run = runs.OUTPUT_DIR / stamp
        run.mkdir(parents=True)
        (run / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        trimmed = fp.apply_step(RESUME, fp.FitStep.model_validate(fp.DEFAULT_STEPS[1]))
        (run / "a.tex").write_text(trimmed, encoding="utf-8")
        (run / "b.tex").write_text(RESUME, encoding="utf-8")
        (run / "shortlisted.json").write_text(json.dumps([
            {"source": "indeed", "job_id": "a", "company": "Acme", "title": "SDE", "relevance": 8,
             "resume_tex_file": "a.tex"},
            {"source": "indeed", "job_id": "b", "company": "Beta", "title": "SDE", "relevance": 8,
             "resume_tex_file": "b.tex"}]), encoding="utf-8")
        self.subject = f"{stamp}/a"
        self.fits: list[tuple[str, list]] = []

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        (fp.POLICY_PATH, fit_chat.DRAFT_PATH, fit_chat.PREVIEW_DIR, runs.OUTPUT_DIR,
         history.DB_PATH) = self._saved
        self._tmp.cleanup()

    def fake_fit(self, tex, steps=None):
        self.fits.append((Path(tex).read_text(encoding="utf-8"), list(steps or [])))
        Path(tex).with_suffix(".pdf").write_bytes(b"%PDF-1.4")
        labels = [s.label for s in steps or []]
        return one_page.TrimResult(pages=1, cuts=labels[:1],
                                   trail=[("as written", 2)] + [(labels[0], 1)] if labels else [])

    def test_overflowed_resumes_come_first(self) -> None:
        listed = fit_chat.subjects()
        self.assertEqual([s["id"] for s in listed], [self.subject, "20261006T103501/b", "base"])
        self.assertTrue(listed[0]["overflowed"])
        self.assertIn(r"\section*{Certifications}", fit_chat.subject_source(self.subject))

    def test_a_turn_revises_the_policy_and_previews_it(self) -> None:
        seen = {}

        def invoke(system, user, schema):
            seen["user"] = user
            return schema(reply="Margins first, then the CCNA bullet.", ready_to_save=True,
                          guidance="Never drop AWS.",
                          steps=[{"kind": "layout", "setting": "margin", "value": 0.6},
                                 {"kind": "drop_item", "section": "Certifications", "match": "CCNA"},
                                 {"kind": "layout", "setting": "margin", "value": 0.1}])

        draft = fit_chat.chat("Keep my certifications if you can", self.subject, invoke=invoke)
        self.assertIn("Keep my certifications", seen["user"])
        self.assertIn(r"\section*{Certifications}", seen["user"])   # the full version
        self.assertEqual([s["label"] for s in draft["steps"]],
                         ["Margins to 0.6in", "Drop the bullet mentioning 'CCNA' in Certifications"])
        self.assertIn("could not be used", draft["messages"][-1]["text"])   # the 0.1in margin
        self.assertTrue(draft["ready_to_save"])
        self.assertEqual(draft["last_preview"]["applied"], ["Margins to 0.6in"])
        self.assertTrue(draft["last_preview"]["pdf"].startswith("/api/fit/preview.pdf"))
        # Nothing reaches runs until it is saved.
        self.assertIsNone(fp.saved_policy())
        saved = fit_chat.agree(draft["steps"], draft["guidance"])
        self.assertEqual(len(fp.load().steps), 2)
        self.assertEqual(fp.load().guidance, "Never drop AWS.")
        self.assertIn("Saved.", saved["draft"]["messages"][-1]["text"])
        # A reload brings the conversation back.
        self.assertEqual(fit_chat.load_draft()["messages"][-1]["text"], saved["draft"]["messages"][-1]["text"])

    def test_hand_edits_are_checked_and_previewed(self) -> None:
        draft = fit_chat.edit_steps([fp.DEFAULT_STEPS[1]], "", self.subject)
        self.assertEqual(draft["last_preview"]["applied"], ["Drop the Certifications section"])
        self.assertFalse(draft["ready_to_save"])
        with self.assertRaises(ValueError):
            fit_chat.edit_steps([{"kind": "layout", "setting": "margin", "value": 3}], "", self.subject)
        # A typo in a section name is said, not skipped in silence.
        draft = fit_chat.edit_steps([{"kind": "drop_section", "section": "Certification"}], "", self.subject)
        self.assertTrue(any("Certification" in p for p in draft["last_preview"]["problems"]))

    def test_the_same_preview_is_not_compiled_twice(self) -> None:
        fit_chat.edit_steps([fp.DEFAULT_STEPS[1]], "", self.subject)
        before = len(self.fits)
        fit_chat.edit_steps([fp.DEFAULT_STEPS[1]], "", self.subject)
        self.assertEqual(len(self.fits), before)

    def test_a_reorder_during_a_slow_reply_is_not_lost(self) -> None:
        def slow_invoke(system, user, schema):
            # The candidate removes a step while the model thinks.
            fit_chat.edit_steps([fp.DEFAULT_STEPS[0]], "", self.subject)
            return schema(reply="ok", steps=[{"kind": "layout", "setting": "margin", "value": 0.6}])

        draft = fit_chat.chat("hello", self.subject, invoke=slow_invoke)
        # The model's policy wins (it is the newer decision), but nothing else
        # of the on-disk draft was thrown away.
        self.assertEqual([s["label"] for s in draft["steps"]], ["Margins to 0.6in"])
        self.assertEqual([m["text"] for m in draft["messages"]][-2:], ["hello", "ok"])

    def test_start_over_and_back_to_default(self) -> None:
        fit_chat.agree([fp.DEFAULT_STEPS[0]], "")
        fit_chat.chat("hello", "base", invoke=lambda s, u, schema: schema(reply="hi"))
        fresh = fit_chat.reset_draft()
        self.assertEqual(len(fresh["messages"]), 1)
        self.assertEqual(len(fresh["steps"]), 1)       # starts from the saved policy
        fit_chat.back_to_default()
        self.assertIsNone(fp.saved_policy())


if __name__ == "__main__":
    unittest.main()
