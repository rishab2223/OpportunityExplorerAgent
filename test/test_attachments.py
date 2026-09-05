from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.apply.worker import Attachments, is_resume_field, validate_document

VALID_TEX = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\section*{Skills}\nPython\n\\end{document}\n"
)


def field(**kw) -> dict:
    base = {"tag": "input", "type": "file", "label": "", "name": "", "group": "", "text": ""}
    base.update(kw)
    return base


class ScriptedSession:
    """Answers ask_choice from a queue and records what was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.logs: list[str] = []
        self.choices: list[tuple[str, dict]] = []

    def log(self, text):
        self.logs.append(text)

    def ask_choice(self, kind, text, meta=None):
        self.choices.append((kind, meta or {}))
        if not self.replies:
            raise AssertionError(f"unexpected extra choice: {kind}")
        return self.replies.pop(0)


class ResumeFieldTests(unittest.TestCase):
    def test_named_resume_inputs(self) -> None:
        self.assertTrue(is_resume_field(field(label="Upload resume")))
        self.assertTrue(is_resume_field(field(name="cv_file")))
        self.assertTrue(is_resume_field(field(label="Attach your CV")))
        self.assertTrue(is_resume_field(field(label="Curriculum vitae")))

    def test_unlabelled_file_input_defaults_to_resume(self) -> None:
        self.assertTrue(is_resume_field(field()))

    def test_cover_letter_upload_is_not_a_resume(self) -> None:
        self.assertFalse(is_resume_field(field(label="Upload cover letter")))

    def test_non_file_inputs_are_never_resumes(self) -> None:
        self.assertFalse(is_resume_field(field(type="text", label="Resume headline")))
        self.assertFalse(is_resume_field(field(tag="textarea", type="", label="Resume summary")))


class UploadTileTests(unittest.TestCase):
    def test_tile_buttons_are_classified(self) -> None:
        from src.apply.worker import _upload_tile_kind

        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Upload a CV", "label": ""}), "resume")
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Attach a Cover Letter", "label": ""}), "letter")
        self.assertEqual(_upload_tile_kind(
            {"tag": "div", "role": "button", "text": "Add resume", "label": ""}), "resume")

    def test_non_tiles_are_ignored(self) -> None:
        from src.apply.worker import _upload_tile_kind

        # No upload verb, wrong noun, or not clickable.
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Upload portfolio", "label": ""}), "")
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Review application", "label": ""}), "")
        self.assertEqual(_upload_tile_kind(
            {"tag": "input", "type": "file", "text": "", "label": "Upload resume"}), "")


class ValidateDocumentTests(unittest.TestCase):
    def test_accepts_a_sound_document(self) -> None:
        self.assertEqual(validate_document(VALID_TEX), [])

    def test_rejects_empty_and_truncated(self) -> None:
        self.assertIn("the document is empty", validate_document("   "))
        problems = validate_document("\\documentclass{article}\n\\begin{document}\nx\n")
        self.assertTrue(any("end{document}" in p for p in problems))

    def test_rejects_unbalanced_braces_and_environments(self) -> None:
        broken = VALID_TEX.replace("Python", "\\textbf{Python")
        self.assertIn("unbalanced braces", validate_document(broken))
        broken_env = VALID_TEX.replace(
            "Python", "\\begin{itemize}\\item a"
        )
        self.assertTrue(any("begin/\\end" in p for p in validate_document(broken_env)))


class ResumeChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.tex = self.dir / "job.tex"
        self.tex.write_text(VALID_TEX, encoding="utf-8")
        self.pdf = self.dir / "job.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 tailored")
        self.default = self.dir / "default.pdf"
        self.default.write_bytes(b"%PDF-1.4 default")
        self.options = {
            "tailored": {"path": str(self.pdf), "error": "", "changelog": "did things",
                         "source": VALID_TEX, "pages": 1},
            "default": {"path": str(self.default), "error": ""},
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _attach(self, sess):
        return Attachments(sess, {"company": "X", "title": "Y"}, "resume text",
                           None, lambda: self.options, self.dir)

    def test_tailored_choice(self) -> None:
        sess = ScriptedSession(["tailored"])
        self.assertEqual(self._attach(sess).resume(), str(self.pdf))
        kind, meta = sess.choices[0]
        self.assertEqual(kind, "resume")
        self.assertEqual(meta["changelog"], "did things")
        self.assertEqual(meta["tailored_source"], VALID_TEX)

    def test_default_choice(self) -> None:
        sess = ScriptedSession(["default"])
        self.assertEqual(self._attach(sess).resume(), str(self.default))

    def test_skip_returns_nothing(self) -> None:
        sess = ScriptedSession(["skip"])
        self.assertEqual(self._attach(sess).resume(), "")

    def test_choice_is_remembered_for_later_fields(self) -> None:
        sess = ScriptedSession(["default"])
        attach = self._attach(sess)
        self.assertEqual(attach.resume(), str(self.default))
        self.assertEqual(attach.resume(), str(self.default))  # no second ask
        self.assertEqual(len(sess.choices), 1)

    def test_unrecognised_reply_reasks_instead_of_guessing(self) -> None:
        # A mistyped reply must never silently attach a file.
        sess = ScriptedSession(["definitely the good one", "default"])
        self.assertEqual(self._attach(sess).resume(), str(self.default))
        self.assertEqual(len(sess.choices), 2)
        self.assertIn("tailored, default or skip", sess.choices[1][1]["tailored_error"])

    def test_invalid_edit_is_rejected_and_source_restored(self) -> None:
        broken = VALID_TEX.replace("\\end{document}", "")
        sess = ScriptedSession(["__use__\n" + broken, "tailored"])
        attach = self._attach(sess)
        self.assertEqual(attach.resume(), str(self.pdf))
        # The modal was reopened with the reason, and the file is untouched.
        self.assertEqual(len(sess.choices), 2)
        self.assertIn("end{document}", sess.choices[1][1]["tailored_error"])
        self.assertEqual(self.tex.read_text(encoding="utf-8"), VALID_TEX)


class CoverLetterFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.calls: list[str] = []
        # frame() reads the profile for the sign-off name; never the real file.
        patcher = mock.patch(
            "src.apply.profile.load_profile", return_value={"full_name": "Test User"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _invoke(self, system, user, schema):
        self.calls.append(system[:20])
        return schema(text="Drafted letter." if "revise" not in system.lower() else "Revised letter.")

    def _attach(self, sess):
        return Attachments(sess, {"company": "X", "title": "Y"}, "resume text",
                           self._invoke, None, self.dir)

    def test_draft_then_accept_costs_one_call(self) -> None:
        sess = ScriptedSession(["__use__\nDrafted letter."])
        attach = self._attach(sess)
        self.assertEqual(attach.cover_letter(for_upload=False), "Drafted letter.")
        self.assertEqual(attach.calls, 1)
        self.assertEqual(sess.choices[0][0], "cover_letter")
        # The modal shows the COMPLETE letter - greeting and sign-off included,
        # nothing appended invisibly at build time.
        shown = sess.choices[0][1]["text"]
        self.assertEqual(
            shown, "Dear X team,\n\nDrafted letter.\n\nRegards,\nTest User"
        )

    def test_direct_edit_costs_nothing_extra(self) -> None:
        sess = ScriptedSession(["__use__\nMy own wording."])
        attach = self._attach(sess)
        self.assertEqual(attach.cover_letter(for_upload=False), "My own wording.")
        self.assertEqual(attach.calls, 1)  # the draft only

    def test_revision_costs_one_more_call(self) -> None:
        sess = ScriptedSession(["__revise__ make it shorter", "__use__\nRevised letter."])
        attach = self._attach(sess)
        self.assertEqual(attach.cover_letter(for_upload=False), "Revised letter.")
        self.assertEqual(attach.calls, 2)
        self.assertEqual(sess.choices[1][1]["text"], "Revised letter.")

    def test_skip_returns_nothing(self) -> None:
        sess = ScriptedSession(["skip"])
        self.assertEqual(self._attach(sess).cover_letter(for_upload=False), "")

    def test_textarea_letter_leaves_a_pdf_on_disk(self) -> None:
        # A pasted (non-upload) letter must still produce the run folder's PDF
        # copy; before this the text existed nowhere but the session transcript.
        from src import pdf_compile

        sess = ScriptedSession(["__use__\nMy final letter."])
        attach = self._attach(sess)
        self.assertEqual(attach.cover_letter(for_upload=False), "My final letter.")
        tex = list(self.dir.glob("cover_*.tex"))
        self.assertEqual(len(tex), 1)
        self.assertIn("My final letter.", tex[0].read_text(encoding="utf-8"))
        if pdf_compile.toolchain():
            pdfs = list(self.dir.glob("cover_*.pdf"))
            self.assertEqual(len(pdfs), 1)
            self.assertEqual(attach.letter_pdf, str(pdfs[0]))

    def test_empty_submission_is_refused(self) -> None:
        sess = ScriptedSession(["__use__\n   ", "skip"])
        attach = self._attach(sess)
        self.assertEqual(attach.cover_letter(for_upload=False), "")
        self.assertIn("empty", sess.choices[1][1]["error"])
