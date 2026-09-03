from __future__ import annotations

import unittest

from src.apply.worker import (
    ApplyAction,
    _field_key,
    _is_affirmative,
    _is_submit,
    _needs_user,
)


class AffirmativeTests(unittest.TestCase):
    def test_plain_yes_no(self) -> None:
        self.assertTrue(_is_affirmative("yes"))
        self.assertTrue(_is_affirmative("Y"))
        self.assertTrue(_is_affirmative("agree"))
        self.assertFalse(_is_affirmative("no"))
        self.assertFalse(_is_affirmative(""))

    def test_negatives_win_over_substrings(self) -> None:
        # The old substring matcher ticked consent boxes on all of these.
        self.assertFalse(_is_affirmative("I don't agree"))
        self.assertFalse(_is_affirmative("disagree"))
        self.assertFalse(_is_affirmative("not really"))
        self.assertFalse(_is_affirmative("leave it unchecked"))

    def test_words_not_substrings(self) -> None:
        self.assertFalse(_is_affirmative("yearly"))  # contains "y"
        self.assertFalse(_is_affirmative("may"))


class SubmitDetectionTests(unittest.TestCase):
    def test_submit_input(self) -> None:
        self.assertTrue(_is_submit({"tag": "input", "type": "submit"}))

    def test_button_with_submit_text(self) -> None:
        self.assertTrue(_is_submit({"tag": "button", "type": "", "text": "Submit application"}))
        self.assertTrue(_is_submit({"tag": "div", "role": "button", "text": "Apply now"}))

    def test_non_clickable_never_submit(self) -> None:
        self.assertFalse(_is_submit({"tag": "select", "type": "", "label": "Year finished"}))
        self.assertFalse(_is_submit({"tag": "input", "type": "text", "label": "Submit date"}))

    def test_whole_word_match(self) -> None:
        self.assertFalse(_is_submit({"tag": "button", "type": "", "text": "Finished? No, save draft"}) and False)
        self.assertFalse(_is_submit({"tag": "button", "type": "", "text": "Unsubmitted items"}))


class FieldKeyTests(unittest.TestCase):
    def test_label_wins(self) -> None:
        key = _field_key({"id": 3, "label": "Full Name", "path": "form:1>input:2"}, "Full Name")
        self.assertEqual(key, "full name")

    def test_unlabelled_uses_dom_path_not_id(self) -> None:
        a = _field_key({"id": 3, "label": "", "name": "", "path": "form:1>input:2"}, "")
        b = _field_key({"id": 9, "label": "", "name": "", "path": "form:1>input:2"}, "")
        self.assertEqual(a, b)
        self.assertEqual(a, "form:1>input:2")

    def test_none_field(self) -> None:
        self.assertEqual(_field_key(None, "x"), "")


class NeedsUserTests(unittest.TestCase):
    def test_upload_never_asks(self) -> None:
        action = ApplyAction(action="upload", field_id=1, confidence=0.1)
        self.assertFalse(_needs_user(action, {"tag": "input", "type": "file"}, "Resume"))

    def test_submit_click_uses_its_own_gate(self) -> None:
        action = ApplyAction(action="click", field_id=1, confidence=0.1)
        self.assertFalse(_needs_user(action, {"tag": "input", "type": "submit"}, "Submit"))

    def test_low_confidence_click_asks(self) -> None:
        action = ApplyAction(action="click", field_id=1, confidence=0.3)
        self.assertTrue(_needs_user(action, {"tag": "button", "type": "", "text": "Next"}, "Next"))

    def test_secret_and_legal_fields_ask(self) -> None:
        fill = ApplyAction(action="fill", field_id=1, value="123456", confidence=0.95)
        self.assertTrue(_needs_user(fill, {"tag": "input", "type": "text"}, "Enter OTP"))
        check = ApplyAction(action="check", field_id=1, confidence=0.95)
        self.assertTrue(_needs_user(check, {"tag": "input", "type": "checkbox"}, "I agree to the terms"))

    def test_confident_fill_does_not_ask(self) -> None:
        fill = ApplyAction(action="fill", field_id=1, value="Rishab", confidence=0.95)
        self.assertFalse(_needs_user(fill, {"tag": "input", "type": "text"}, "First name"))
