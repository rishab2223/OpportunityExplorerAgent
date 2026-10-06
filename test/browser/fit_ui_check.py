"""The Resume fit tab, in a real browser.

A made-up run in a scratch outputs folder, a scratch policy and draft, a fake
model that answers instantly and a fake compile: the candidate's resume,
their saved policy and their runs are never read or written, and no model is
called.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_fit_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import history, llm  # noqa: E402
from src.config import AppConfig  # noqa: E402
from src.resume import fit_policy, one_page  # noqa: E402
from src.web import fit_chat, runs  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
runs.OUTPUT_DIR = SCRATCH / "outputs"
fit_policy.POLICY_PATH = SCRATCH / "policy.json"
fit_chat.DRAFT_PATH = SCRATCH / "draft.json"
fit_chat.PREVIEW_DIR = SCRATCH / "preview"

RESUME = r"""\documentclass[11pt]{article}
\usepackage[margin=0.75in]{geometry}
\begin{document}
\section*{Summary}
Engineer.
\section*{Certifications}
\begin{itemize}
  \item AWS Solutions Architect
  \item CCNA
\end{itemize}
\end{document}
"""
BASE = SCRATCH / "resume.tex"
BASE.write_text(RESUME, encoding="utf-8")
_CFG = AppConfig.model_validate({"resume": {"local_path": str(BASE)}})
fit_chat.run_options.effective_config = lambda base=None: _CFG

STAMP = "20261006T103501"
RUN = runs.OUTPUT_DIR / STAMP
RUN.mkdir(parents=True)
(RUN / "run.json").write_text(json.dumps({"status": "ok", "match_count": 2}), encoding="utf-8")
(RUN / "long.tex").write_text(
    fit_policy.apply_step(RESUME, fit_policy.FitStep.model_validate(fit_policy.DEFAULT_STEPS[1])),
    encoding="utf-8")
(RUN / "short.tex").write_text(RESUME, encoding="utf-8")
(RUN / "shortlisted.json").write_text(json.dumps([
    {"source": "indeed", "job_id": "short", "company": "Beta", "title": "SDE", "relevance": 8,
     "resume_tex_file": "short.tex"},
    {"source": "indeed", "job_id": "long", "company": "Acme", "title": "Backend SDE", "relevance": 9,
     "resume_tex_file": "long.tex"}]), encoding="utf-8")

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>"
       b"endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
fits: list[list[str]] = []


def fake_fit(tex, steps=None, max_pages=1):
    labels = [s.label for s in steps or []]
    fits.append(labels)
    Path(tex).with_suffix(".pdf").write_bytes(PDF)
    if not labels:
        return one_page.TrimResult(pages=2, trail=[("as written", 2)], note="2 pages and nothing left that may be cut")
    return one_page.TrimResult(pages=1, cuts=labels[:1], trail=[("as written", 2), (labels[0], 1)])


fit_chat.fit_to_one_page = fake_fit
asked: list[str] = []


def fake_invoker(_cfg, _env, _purpose):
    def invoke(system, user, schema):
        asked.append(user)
        time.sleep(0.3)   # long enough to see the thinking bubble
        return schema(reply="Let's tighten the margins first and keep both certifications.",
                      steps=[{"kind": "layout", "setting": "margin", "value": 0.6},
                             {"kind": "drop_item", "section": "Certifications", "match": "CCNA"}],
                      guidance="Never drop the AWS certification.", ready_to_save=True)
    return invoke


llm.make_invoker = fake_invoker

PORT = 8776   # answers_ui_check has 8775; check.py runs them side by side

import uvicorn  # noqa: E402
from src.web.app import app  # noqa: E402

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    probe = socket.socket()
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        probe.close()
        break
    probe.close()
    time.sleep(0.1)
else:
    raise SystemExit("server did not start")

from playwright.sync_api import sync_playwright  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


def steps(page):
    return page.eval_on_selector_all("#fitsteps .fitsteplabel", "s => s.map(x => x.textContent)")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

    print("== opening the tab ==")
    page.click("#nav-fit")
    page.wait_for_selector("#fitmessages .fitmsg")
    check("the tab shows", page.locator("#view-fit").is_visible())
    check("the assistant opens the conversation",
          "something has to give" in (page.text_content("#fitmessages") or ""))
    check("runs use the default rule until something is saved",
          page.text_content("#fitstate") == "Runs use the default rule")
    check("the draft starts from the default rule",
          steps(page) == ["Drop the CCNA bullet", "Drop the Certifications section"], str(steps(page)))
    subject = page.input_value("#fitsubject")
    check("a resume that ran long is offered first", subject == f"{STAMP}/long", subject)
    page.wait_for_function("document.querySelector('#fitpdf').src.includes('/api/fit/preview.pdf')")
    check("the preview is built on open", fits and fits[-1][0] == "Drop the CCNA bullet", str(fits[-1:]))
    check("the trail says what happened",
          "2 p" in (page.text_content("#fittrail") or "") and "1 p" in (page.text_content("#fittrail") or ""))
    check("the page count shows one page", page.text_content("#fitpages") == "1 page")
    if os.environ.get("OEA_SCREENSHOT"):
        page.screenshot(path=os.environ["OEA_SCREENSHOT"])

    print("== a conversation turn ==")
    page.fill("#fitinput", "Keep my certifications if you can")
    page.press("#fitinput", "Enter")
    page.wait_for_selector(".fitthinking")
    check("your message shows at once, with a thinking bubble",
          "Keep my certifications" in (page.text_content("#fitmessages") or ""))
    page.wait_for_selector(".fitthinking", state="detached")
    check("the model got the full resume and what you said",
          asked and r"\section*{Certifications}" in asked[-1] and "Keep my certifications" in asked[-1])
    check("the reply shows", "tighten the margins" in (page.text_content("#fitmessages") or ""))
    check("the policy is the model's revision",
          steps(page) == ["Margins to 0.6in", "Drop the bullet mentioning 'CCNA' in Certifications"],
          str(steps(page)))
    check("the guidance shows", "Never drop the AWS" in (page.text_content("#fitguidance") or ""))
    check("agreement is suggested, not assumed", page.locator("#fitready").is_visible()
          and fit_policy.saved_policy() is None)
    check("the preview tried the new policy", fits[-1] == steps(page), str(fits[-1]))

    print("== editing by hand ==")
    with page.expect_response("**/api/fit/preview"):
        page.click("#fitsteps button[data-move=down][data-i='0']")
    check("reordering re-runs the preview in the new order", fits[-1][0].startswith("Drop"), str(fits[-1]))
    with page.expect_response("**/api/fit/preview"):
        page.click("#fitsteps button[data-remove='0']")
    check("removing a step leaves the rest", steps(page) == ["Margins to 0.6in"], str(steps(page)))

    print("== saving ==")
    page.click("#fitsave")
    page.wait_for_function("document.querySelector('#fitstate').textContent.startsWith('Runs use your policy')")
    saved = fit_policy.saved_policy()
    check("Save writes the policy runs use", saved is not None and [s.label for s in saved.steps] == ["Margins to 0.6in"])
    check("with the guidance", saved is not None and "AWS" in saved.guidance)
    check("the conversation says so", "Saved." in (page.text_content("#fitmessages") or ""))

    print("== it survives a reload ==")
    page.reload(wait_until="networkidle")
    page.click("#nav-fit")
    page.wait_for_selector("#fitmessages .fitmsg-you")
    check("the conversation comes back", "Keep my certifications" in (page.text_content("#fitmessages") or ""))

    print("== start over / default ==")
    page.click("#fitrestart")
    page.wait_for_function("document.querySelectorAll('#fitmessages .fitmsg').length === 1")
    check("start over keeps the saved policy as the starting point", steps(page) == ["Margins to 0.6in"])
    page.click("#fitdefault")
    page.wait_for_function("document.querySelector('#fitstate').textContent === 'Runs use the default rule'")
    check("back to the default forgets the saved policy", fit_policy.saved_policy() is None)

    check("no script errors", not errors, "; ".join(errors)[:200])
    browser.close()

server.should_exit = True
print()
if problems:
    print("FIT UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("FIT UI CHECK PASSED")
