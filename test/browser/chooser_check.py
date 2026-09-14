"""File-chooser interception check: tile buttons with HIDDEN file inputs
(the SuccessFactors pattern). Uses the real _arm_file_chooser."""
import sys
import tempfile
from pathlib import Path

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright

from src.apply.worker import _arm_file_chooser

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_chooser_"))
RESUME = SCRATCH / "resume.pdf"
RESUME.write_bytes(b"%PDF-1.4 resume\n%%EOF\n")
LETTER = SCRATCH / "cover_X.pdf"
LETTER.write_bytes(b"%PDF-1.4 letter\n%%EOF\n")

PAGE = """
<input type="file" id="cv" name="resume_upload" style="display:none">
<button onclick="document.getElementById('cv').click()">Upload a CV</button>
<input type="file" id="cl" name="cover_letter_upload" style="display:none">
<button onclick="document.getElementById('cl').click()">Attach a Cover Letter</button>
"""


class StubAttach:
    resume_path = str(RESUME)
    letter_pdf = str(LETTER)


class StubSess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("  LOG", text)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(PAGE)
    sess = StubSess()
    _arm_file_chooser(page, StubAttach(), sess)

    page.click("text=Upload a CV")
    page.wait_for_timeout(300)
    got_cv = page.evaluate("document.getElementById('cv').files[0]?.name || ''")
    assert got_cv == "resume.pdf", f"cv slot got {got_cv!r}"

    page.click("text=Attach a Cover Letter")
    page.wait_for_timeout(300)
    got_cl = page.evaluate("document.getElementById('cl').files[0]?.name || ''")
    assert got_cl == "cover_X.pdf", f"letter slot got {got_cl!r}"

    browser.close()

print("CHOOSER CHECK PASSED: resume -> CV slot, letter PDF -> letter slot")
