"""Angular Material / ngx-file-drop widgets, shaped like the ALTEN
talentrecruit form (dumped Sep 11 2026):

- <mat-select role=combobox aria-haspopup=true> dropdowns that are not
  <select> at all and open an overlay of role=option rows;
- inputs whose visible label lives in <mat-label> while the placeholder is
  an example value ("daniel@gmail.com");
- a consent checkbox that is cdk-visually-hidden behind a <label> which
  swallows the click;
- a resume drop zone hiding its real file input, next to a photo input
  that takes images only.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, resolver, worker  # noqa: E402

# Never the real profile or history (standing rule).
TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "email": "test@example.invalid",
    "phone": "+91 00000 00000", "location": "Bangalore, India",
    "salary_currency": "INR", "salary_period": "Annual",
}), encoding="utf-8")

FIXTURE = WORK / "fixture_material.html"
FIXTURE.write_text("""<!doctype html><html><head><style>
  .cdk-visually-hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
  mat-form-field { display:block; margin:8px 0; }
  mat-label { display:block; color:#666; }
  mat-select { display:block; border:1px solid #999; padding:6px; width:260px; cursor:pointer; }
  .overlay { position:absolute; background:#fff; border:1px solid #333; z-index:9; }
  .mat-option { padding:6px 10px; cursor:pointer; }
  .drop { border:2px dashed #999; padding:20px; width:420px; }
  .file-hidden { display:none; }
  label.mat-checkbox-layout { display:inline-block; padding:6px; background:#eee; }
</style></head><body>
<h2>Personal details</h2>
<mat-form-field><mat-label>Email</mat-label>
  <input matinput placeholder="daniel@gmail.com" formcontrolname="email" id="mat-input-3"></mat-form-field>

<h2>Salary details</h2>
<mat-form-field><mat-label>Current Salary Currency</mat-label>
  <mat-select id="cur1" role="combobox" aria-haspopup="true" formcontrolname="currencycode1"
     data-options="INR - Indian Rupee|USD - US Dollar|EUR - Euro">Select none</mat-select></mat-form-field>
<mat-form-field><mat-label>Current Salary Period</mat-label>
  <mat-select id="per1" role="combobox" aria-haspopup="true" formcontrolname="currentsalaryperiod"
     data-options="Monthly|Annually|Weekly">Select none</mat-select></mat-form-field>
<mat-form-field><mat-label>Current Annual Salary</mat-label>
  <input matinput placeholder="Current Annual Salary" type="number" formcontrolname="currentannualsalary"></mat-form-field>

<h2>Resume Upload</h2>
<div class="drop">Drag and Drop Your Resume OR Browse File
  <input type="file" class="file-hidden" accept=".doc, .docx, .pdf, .rtf, .txt" multiple></div>
<div>Profile photo <input type="file" class="file-hidden" accept=".png, .jpeg, .jpg"></div>

<p>Please review the Privacy Policy</p>
<label class="mat-checkbox-layout" for="mat-checkbox-1-input">
  <input type="checkbox" class="mat-checkbox-input cdk-visually-hidden" id="mat-checkbox-1-input">
  I have read the terms &amp; conditions of the Privacy Policy and I hereby provide my consent towards.</label>
<script>
  // mat-select: opens an overlay of role=option rows appended to the body
  for (const sel of document.querySelectorAll('mat-select')) {
    sel.addEventListener('click', () => {
      document.querySelectorAll('.overlay').forEach(o => o.remove());
      const box = document.createElement('div');
      box.className = 'overlay'; box.setAttribute('role', 'listbox');
      const r = sel.getBoundingClientRect();
      box.style.left = (r.left + scrollX) + 'px'; box.style.top = (r.bottom + scrollY) + 'px';
      for (const o of sel.dataset.options.split('|')) {
        const row = document.createElement('div');
        row.className = 'mat-option'; row.setAttribute('role', 'option'); row.textContent = o;
        row.addEventListener('click', () => { sel.textContent = o; box.remove(); });
        box.appendChild(row);
      }
      document.body.appendChild(box);
    });
  }
  // the label swallows clicks aimed at the hidden input, like Material
  document.querySelector('label.mat-checkbox-layout').addEventListener('click', (e) => {
    if (e.target.tagName !== 'INPUT') {
      const box = document.getElementById('mat-checkbox-1-input');
      box.checked = !box.checked;
    }
  });
</script></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("  LOG", text)


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(300)
    fields = browser.snapshot(page)
    by_label = {}
    for f in fields:
        by_label.setdefault(f["label"], f)
        print(f"  field {f['id']:>2} {f['tag']:<10} {f['type']:<8} {f['label'][:46]!r} accept={f.get('accept','')!r}")

    # 1. the visible label wins over the example placeholder
    if "Email" not in by_label:
        failures.append(f"email box labelled {[f['label'] for f in fields]}")

    # 2. the mat-select dropdowns are seen at all, and read as empty
    for name in ("Current Salary Currency", "Current Salary Period"):
        field = by_label.get(name)
        if field is None:
            failures.append(f"{name}: not in the snapshot")
            continue
        if not resolver.is_listbox_button(field):
            failures.append(f"{name}: not recognised as a dropdown")
        if not resolver.is_blank(field):
            failures.append(f"{name}: 'Select none' read as a value")

    # 3. the profile answers them
    got = resolver.resolve(by_label.get("Current Salary Currency", {}))
    if not got or got[0] != "INR":
        failures.append(f"currency resolved to {got}")
    got = resolver.resolve(by_label.get("Current Salary Period", {}))
    if not got or got[0] != "Annual":
        failures.append(f"period resolved to {got}")

    # 4. picking in a mat-select overlay
    sess = Sess()
    for name, want, expect in (("Current Salary Currency", "INR", "INR - Indian Rupee"),
                               ("Current Salary Period", "Annual", "Annually")):
        field = by_label[name]
        try:
            worker._apply_value(page, field, want, "", sess, source="profile")
        except Exception as exc:
            failures.append(f"{name}: {str(exc).splitlines()[0][:120]}")
            continue
        shown = page.locator(f"#{'cur1' if 'Currency' in name else 'per1'}").inner_text().strip()
        if shown != expect:
            failures.append(f"{name}: widget shows {shown!r}, wanted {expect!r}")

    # 5. the consent box, whose label eats the click
    consent = next(f for f in fields if f["type"] == "checkbox")
    try:
        worker._apply_value(page, consent, "yes", "", sess, source="user")
    except Exception as exc:
        failures.append(f"consent: {str(exc).splitlines()[0][:140]}")
    if not page.locator("#mat-checkbox-1-input").is_checked():
        failures.append("consent: still unticked")

    # 6. the drop zone: hidden inputs stay out of the snapshot (the model must
    #    not see them), and the resume still reaches the document slot, not
    #    the photo one.
    if [f for f in fields if f["type"] == "file"]:
        failures.append("hidden file inputs leaked into the snapshot")
    resume = worker._lone_document_file_input(page)
    if resume is None:
        failures.append("the drop zone's file input was not found")
    else:
        accept = resume.get_attribute("accept") or ""
        print(f"  drop-zone input accepts: {accept!r}")
        if "pdf" not in accept:
            failures.append(f"the photo input was picked ({accept})")
        pdf = Path(TMP.name) / "resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 test\n%%EOF\n")
        resume.set_input_files(str(pdf), timeout=10000)
        names = page.evaluate(
            "() => Array.from(document.querySelectorAll('input[type=file]'))"
            ".map(e => (e.files[0] || {}).name || '')")
        print("  files on the page's inputs:", names)
        if names != ["resume.pdf", ""]:
            failures.append(f"upload landed as {names}")
    text = browser.page_text(page, 1500)
    if not (worker.DROPZONE_RE.search(text) and worker.RESUME_FIELD_RE.search(text)):
        failures.append("the drop zone's text was not recognised")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nMATERIAL CHECK PASSED")
