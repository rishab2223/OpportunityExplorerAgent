"""The apply session's folder under dumps/: transcript, summary, naming."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import dumps
from src.apply.session import ApplySession


def _lines(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "log.jsonl").read_text(encoding="utf-8").splitlines()]


class SessionDirTests(unittest.TestCase):
    def test_company_and_title_become_safe_folder_names(self):
        base = Path(tempfile.mkdtemp())
        path = dumps.session_dir("Acme / Cloud, Inc.", "Senior Backend Engineer (Node.js)", base)
        self.assertEqual(path.parent, base / "Acme-Cloud-Inc")
        self.assertTrue(path.name.endswith("_Senior-Backend-Engineer-Node-js"), path.name)

    def test_two_sessions_in_the_same_second_get_two_folders(self):
        base = Path(tempfile.mkdtemp())
        first = dumps.session_dir("Acme", "Engineer", base)
        first.mkdir(parents=True)
        second = dumps.session_dir("Acme", "Engineer", base)
        self.assertNotEqual(first, second)

    def test_no_company_still_has_a_folder(self):
        base = Path(tempfile.mkdtemp())
        self.assertEqual(dumps.session_dir("", "", base).parent.name, "unknown-company")


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / "Acme" / "20261015-113000_Engineer"
        self.sess = ApplySession("run1", "job1", "Acme Engineer")

    def test_lines_before_and_after_keep_log_are_both_written(self):
        self.sess.log("before")
        self.sess.keep_log(self.folder, {"company": "Acme"})
        self.sess.log("after")
        texts = [line["text"] for line in _lines(self.folder)]
        self.assertEqual(texts, ["before", "after"])
        self.assertIn("time", _lines(self.folder)[0])

    def test_reply_to_a_code_question_is_hidden(self):
        self.sess.keep_log(self.folder)
        self.sess.emit("question", "Enter the 6-digit code we emailed you")
        self.sess.emit("answer", "482913")
        self.sess.emit("question", "Notice period?")
        self.sess.emit("answer", "Immediate")
        answers = [line["text"] for line in _lines(self.folder) if line["type"] == "answer"]
        self.assertEqual(answers, ["[hidden]", "Immediate"])

    def test_postal_pin_code_is_not_hidden(self):
        self.sess.keep_log(self.folder)
        self.sess.emit("question", "PIN code of your current address?")
        self.sess.emit("answer", "122001")
        answers = [line["text"] for line in _lines(self.folder) if line["type"] == "answer"]
        self.assertEqual(answers, ["122001"])

    def test_memory_keeps_the_real_answer(self):
        """Only the file hides it: the live chat still shows what was typed."""
        self.sess.keep_log(self.folder)
        self.sess.emit("question", "Enter your password")
        self.sess.emit("answer", "hunter2")
        _queue, events = self.sess.subscribe()
        self.assertEqual(events[-1]["text"], "hunter2")

    def test_summary_written_at_finish(self):
        self.sess.keep_log(self.folder, {"company": "Acme", "ats": "workday"})
        self.sess.summary["model_calls"] = 7
        self.sess.emit("question", "Expected salary?")
        self.sess.finish("parked", "parked for later")
        summary = json.loads((self.folder / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcome"], "parked")
        self.assertEqual(summary["model_calls"], 7)
        self.assertEqual(summary["questions_asked"], 1)
        self.assertEqual(summary["ats"], "workday")
        self.assertEqual(_lines(self.folder)[-1]["type"], "done")

    def test_no_folder_writes_nothing(self):
        self.sess.log("in memory only")
        self.sess.finish("aborted")
        self.assertIsNone(self.sess.folder)
        self.assertFalse(self.folder.exists())

    def test_unwritable_folder_keeps_the_session_going(self):
        blocker = Path(tempfile.mkdtemp()) / "file"
        blocker.write_text("x")
        self.sess.keep_log(blocker / "sub")          # a file where a folder must go
        self.assertIsNone(self.sess.folder)
        self.sess.log("still fine")


if __name__ == "__main__":
    unittest.main()
