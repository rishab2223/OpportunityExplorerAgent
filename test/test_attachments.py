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

    def test_greenhouse_inputs_identified_by_id_or_heading(self) -> None:
        # Both Greenhouse file inputs are labelled "Attach"; the element id
        # ("resume" / "cover_letter") or the heading above them decides.
        self.assertTrue(is_resume_field(field(label="Attach", elid="resume")))
        self.assertFalse(is_resume_field(field(label="Attach", elid="cover_letter")))
        self.assertTrue(is_resume_field(field(label="Attach", group="Resume/CV *")))
        self.assertFalse(is_resume_field(field(label="Attach", group="Cover Letter")))
        # A labelled non-resume upload is neither.
        self.assertFalse(is_resume_field(field(label="Attach", group="Portfolio")))

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

    def test_greenhouse_tiles_use_the_section_heading(self) -> None:
        # "Attach" alone names nothing; the snapshot's group carries the
        # heading above the tile.
        from src.apply.worker import _upload_tile_kind

        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Attach", "label": "", "group": "Resume/CV"}), "resume")
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Attach", "label": "", "group": "Cover Letter"}), "letter")
        # The sibling tiles under the same heading are not upload verbs.
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Dropbox", "label": "", "group": "Resume/CV"}), "")
        self.assertEqual(_upload_tile_kind(
            {"tag": "button", "text": "Enter manually", "label": "", "group": "Resume/CV"}), "")

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


class LetterReuseTests(unittest.TestCase):
    """A letter drafted for a job survives the session: the next session for
    the SAME job reopens the modal on it (no draft call); the draft, every
    revision and the accepted edit are all kept."""

    def setUp(self) -> None:
        from src import history

        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._original_db = history.DB_PATH
        history.DB_PATH = self.dir / "job_history.db"
        patcher = mock.patch(
            "src.apply.profile.load_profile", return_value={"full_name": "Test User"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = 0

    def tearDown(self) -> None:
        from src import history

        history.DB_PATH = self._original_db
        self._tmp.cleanup()

    def _invoke(self, system, user, schema):
        self.calls += 1
        return schema(text="Revised letter." if "revise" in system.lower() else "Drafted letter.")

    def _attach(self, sess, job_id="test:reuse"):
        job = {"job_id": job_id, "company": "X", "title": "Y"}
        return Attachments(sess, job, "resume text", self._invoke, None, self.dir)

    def test_second_session_reopens_the_same_letter_without_a_call(self) -> None:
        first = ScriptedSession(["__use__\nDrafted letter, hand-edited."])
        self.assertEqual(self._attach(first).cover_letter(for_upload=False), "Drafted letter, hand-edited.")
        self.assertEqual(self.calls, 1)

        second = ScriptedSession(["__use__\nDrafted letter, hand-edited."])
        attach = self._attach(second)
        self.assertEqual(attach.cover_letter(for_upload=False), "Drafted letter, hand-edited.")
        self.assertEqual(self.calls, 1)  # no second draft
        self.assertEqual(second.choices[0][1]["text"], "Drafted letter, hand-edited.")
        self.assertTrue(any("Reusing the cover letter" in line for line in second.logs), second.logs)

    def test_abort_right_after_the_draft_still_reuses_it(self) -> None:
        from src.apply.session import Aborted

        class AbortingSession(ScriptedSession):
            def ask_choice(self, kind, text, meta=None):
                self.choices.append((kind, meta or {}))
                raise Aborted("user aborted")

        with self.assertRaises(Aborted):
            self._attach(AbortingSession([])).cover_letter(for_upload=False)
        self.assertEqual(self.calls, 1)

        second = ScriptedSession(["skip"])
        self._attach(second).cover_letter(for_upload=False)
        self.assertEqual(self.calls, 1)
        self.assertEqual(second.choices[0][1]["text"], "Dear X team,\n\nDrafted letter.\n\nRegards,\nTest User")

    def test_revision_is_kept_and_other_jobs_are_untouched(self) -> None:
        sess = ScriptedSession(["__revise__ shorter", "__use__\nRevised letter."])
        self._attach(sess).cover_letter(for_upload=False)
        self.assertEqual(self.calls, 2)

        again = ScriptedSession(["skip"])
        self._attach(again).cover_letter(for_upload=False)
        self.assertEqual(again.choices[0][1]["text"], "Revised letter.")
        self.assertEqual(self.calls, 2)

        other = ScriptedSession(["skip"])
        self._attach(other, job_id="test:other").cover_letter(for_upload=False)
        self.assertEqual(self.calls, 3)  # a different job drafts its own

    def test_no_job_id_means_nothing_is_stored(self) -> None:
        from src.apply import cover_letter

        sess = ScriptedSession(["__use__\nDrafted letter."])
        Attachments(sess, {"company": "X", "title": "Y"}, "r", self._invoke, None, self.dir).cover_letter(for_upload=False)
        self.assertEqual(cover_letter.load_saved(""), "")


class GenericPickerTests(unittest.TestCase):
    def test_bare_select_file_uses_the_page_text(self) -> None:
        # Workday's resume step: a "Select file" button, the input hidden, the
        # only clue the step's own wording.
        from src.apply.worker import _upload_tile_kind

        tile = {"tag": "button", "text": "Select file", "label": "Select file", "group": ""}
        self.assertEqual(_upload_tile_kind(tile), "")
        self.assertEqual(_upload_tile_kind(tile, "Autofill with Resume. Upload your resume. Next"), "resume")
        self.assertEqual(_upload_tile_kind(tile, "Add your cover letter here"), "letter")
        self.assertEqual(_upload_tile_kind(tile, "Upload a photo of your certificate"), "")
        # A heading of its own still wins over the page text.
        self.assertEqual(_upload_tile_kind({**tile, "group": "Cover Letter"}, "Upload your resume"), "letter")


class BareAddIsNotAPickerTests(unittest.TestCase):
    def test_add_alone_never_uploads(self) -> None:
        # Workday's My Experience: "Add" under Work Experience was taken for a
        # picker and the resume went in "via 'Add'".
        from src.apply.worker import _is_picker_button, _upload_tile_kind

        add = {"tag": "button", "text": "Add", "label": "Add", "group": "Work Experience"}
        self.assertFalse(_is_picker_button(add))
        self.assertEqual(_upload_tile_kind(add, "Resume/CV Upload a file (5MB max)"), "")
        self.assertTrue(_is_picker_button({"tag": "button", "text": "Add files", "label": ""}))
        self.assertTrue(_is_picker_button({"tag": "button", "text": "Select files", "label": ""}))
