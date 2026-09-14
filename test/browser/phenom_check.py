"""The Esko/Veralto (Phenom) form, rebuilt from the dump of Sep 11 2026:

- "From*" / "To*" are ONE box each, showing MM/YYYY, backed by a
  react-datepicker whose month grid turned "Jul 2020" into July of the
  current year;
- "Field of study" is a <select> with 345 options, so the one that was
  wanted sat far past the 40 the snapshot keeps;
- the Languages section is opened by a button that reads "+ Add Language".
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

TMP = tempfile.TemporaryDirectory()          # never the real profile or bank
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "email": "test@example.invalid",
    "languages": "English - Intermediate; Hindi - Fluent",
    "skills": "JavaScript, Node.js, PostgreSQL, AWS, Python, C++",
    "education": "Example University - Bachelors, Computer and Information Science, 2015-2019",
    "not_employment": "Applied AI & LLM Agents",
}), encoding="utf-8")


def experience(title: str, company: str) -> str:
    """One Work Experience entry, as the site builds it from the resume."""
    return f"""<div class="exp">
  <label>Job Title*</label><input type="text" value="{title}">
  <label>Company*</label><input type="text" value="{company}">
  <label>Role description</label><textarea></textarea>
  <button class="rm" aria-label="Remove experience">Remove experience</button>
</div>"""

STUDIES = ["Please Select", "Accounting", "Actuarial Science", "Advertising",
           "Aerospace Engineering", "Applied Informatics", "Application Development"]
STUDIES += [f"Filler Subject {n}" for n in range(1, 300)]
STUDIES += ["Computer Applications", "Computer Engineering", "Computer and Information Science",
            "Information Systems", "Zoology"]
OPTIONS = "".join(f"<option>{s}</option>" for s in STUDIES)

FIXTURE = WORK / "fixture_phenom.html"
FIXTURE.write_text(f"""<!doctype html><html><head><style>
  .grid {{ position:absolute; background:#fff; border:1px solid #333; z-index:5; width:220px; }}
  .grid div {{ display:inline-block; width:60px; padding:4px; cursor:pointer; }}
  label {{ display:block; margin-top:8px; }}
</style></head><body>
<h3>Work Experience :</h3>
{experience("Applied AI &amp; LLM Agents", "")}
{experience("Software Engineer", "Initech Systems")}
{experience("Software Engineer Intern", "Initech Systems")}
<label for="d1">From*</label>
<input type="text" id="experienceData[0].fromTo.startDate" aria-label="From" value="07/2026">
<label for="d2">To*</label>
<input type="text" id="experienceData[0].fromTo.endDate" aria-label="To" value="">
<h3>Education :</h3>
<label for="school">School or University*</label>
<input type="text" id="school">
<label for="fos">Field of study*</label>
<select id="fos">{OPTIONS}</select>
<h3 style="margin-top:300px">Skills :</h3>
<label for="sk">Separate each skill with a comma.</label>
<textarea id="sk" rows="3" cols="60"></textarea>
<h3>Languages :</h3>
<div id="langs"></div>
<button class="array-button-add" id="array-button-add-languageData" aria-label="Add language"><span>+</span> Add Language</button>
<script>
  // react-datepicker: focusing the box opens a month grid; clicking a month
  // sets THAT month and keeps the year the box already had.
  for (const box of document.querySelectorAll('input[id*="Date"]')) {{
    const open = () => {{
      if (document.querySelector('.grid')) return;
      const g = document.createElement('div');
      g.className = 'grid'; g.setAttribute('role', 'listbox');
      const r = box.getBoundingClientRect();
      g.style.left = (r.left + scrollX) + 'px'; g.style.top = (r.bottom + scrollY) + 'px';
      ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].forEach((m, i) => {{
        const d = document.createElement('div');
        d.setAttribute('role', 'option'); d.textContent = m;
        d.addEventListener('mousedown', (e) => e.preventDefault());
        d.addEventListener('click', () => {{
          const year = (box.value.match(/(19|20)\\d{{2}}/) || [new Date().getFullYear()])[0];
          box.value = String(i + 1).padStart(2, '0') + '/' + year;
          g.remove();
        }});
        g.appendChild(d);
      }});
      document.body.appendChild(g);
    }};
    box.addEventListener('focus', open);
    box.addEventListener('click', open);
    box.addEventListener('keydown', (e) => {{
      if (e.key === 'Escape') {{ const g = document.querySelector('.grid'); if (g) g.remove(); }}
    }});
  }}
  for (const b of document.querySelectorAll('.rm')) {{
    b.addEventListener('click', () => b.closest('.exp').remove());
  }}
  let langs = 0;
  document.getElementById('array-button-add-languageData').addEventListener('click', () => {{
    langs += 1;
    const wrap = document.createElement('div');
    wrap.innerHTML = '<label for="lang' + langs + '">Language</label>' +
      '<select id="lang' + langs + '"><option></option><option>English</option><option>Hindi</option><option>French</option></select>' +
      '<label for="prof' + langs + '">Proficiency</label>' +
      '<select id="prof' + langs + '"><option></option><option>Beginner</option><option>Intermediate</option><option>Fluent</option></select>';
    document.getElementById('langs').appendChild(wrap);
  }});
</script></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(300)
    sess = Sess()
    fields = browser.snapshot(page)
    by_label = {f["label"]: f for f in fields}
    print("  labels:", [f["label"] for f in fields])

    # 1. the date boxes: the model's "Jul 2020" must land as 07/2020, and the
    #    month grid must never be used (it would keep the year 2026).
    for label, value, want in (("From", "Jul 2020", "07/2020"), ("To", "01/2026", "01/2026")):
        field = by_label.get(label)
        if field is None:
            failures.append(f"{label}: not in the snapshot")
            continue
        try:
            worker._apply_value(page, field, value, "", sess, source="model")
        except Exception as exc:
            failures.append(f"{label}: {str(exc).splitlines()[0][:120]}")
            continue
        shown = page.locator(f'[data-oea-id="{field["id"]}"]').input_value()
        if shown != want:
            failures.append(f"{label}: box shows {shown!r}, wanted {want!r}")
    if page.locator(".grid").count():
        failures.append("the date picker was left open")

    # 2. the long select: the wanted option is past the snapshot's cut, and
    #    the profile - not the model - says which subject it is.
    fos = by_label["Field of study*"]
    print(f"  snapshot kept {len(fos.get('options') or [])} of {len(STUDIES)} options")
    resolved = resolver.resolve(fos)
    print("  profile resolves Field of study* to:", resolved)
    if not resolved or resolved[0] != "Computer and Information Science":
        failures.append(f"field of study resolved to {resolved}")
    try:
        worker._apply_value(page, fos, "Computer and Information Science", "", sess, source="profile")
    except Exception as exc:
        failures.append(f"field of study: {str(exc).splitlines()[0][:140]}")
    picked = page.locator("#fos").input_value()
    if picked != "Computer and Information Science":
        failures.append(f"field of study selected {picked!r}")
    # ... and a value that really is absent names the nearby options, not
    #     the alphabetical first twelve.
    page.select_option("#fos", index=0)
    try:
        worker._apply_value(page, fos, "Computer Science", "", sess, source="model")
        failures.append("an absent option was accepted")
    except Exception as exc:
        message = str(exc)
        print("   refusal:", message[:200])
        # What the model was offered last time: "Application Development" and
        # "Applied Informatics", because the list started at the letter A.
        if "Computer and Information Science" not in message:
            failures.append("the refusal does not name the option that fits")
        if "Accounting" in message:
            failures.append("the refusal still lists alphabetical noise")

    # 2b. the skills box is a textarea, and a date picker is open elsewhere on
    #     the page: its month grid must not be read as skill suggestions (that
    #     typed and cleared the box seven times and left it empty).
    page.locator('[aria-label="From"]').click()
    page.wait_for_timeout(200)
    grid_open = page.locator(".grid").count() > 0
    snapshot = browser.snapshot(page)
    skills_field = next(f for f in snapshot if f["tag"] == "textarea" and "skill" in f["label"].lower())
    if not worker._is_skills_box(skills_field):
        failures.append("the skills textarea was not recognised")
    # A role description is a textarea in a section too: never a skills box.
    for f in snapshot:
        if f["tag"] == "textarea" and "role description" in f["label"].lower() and worker._is_skills_box(f):
            failures.append("a role description was taken for a skills box")
    rows = worker._visible_options(page, page.locator("#sk"))[1]
    print(f"  date grid open: {grid_open} | lists the skills box can see: {rows}")
    if rows:
        failures.append(f"a far-away list was read as the skills suggestions: {rows[:4]}")
    before = len(sess.logs)
    worker._fill_skills(page, skills_field, ["JavaScript", "Node.js", "Python"], sess)
    written = page.locator("#sk").input_value()
    print("  skills box holds:", repr(written))
    if written != "JavaScript, Node.js, Python":
        failures.append(f"skills box holds {written!r}")
    if any("Could not add the skill" in line for line in sess.logs[before:]):
        failures.append("the skills box was searched skill by skill")
    page.keyboard.press("Escape")

    # 2c. the site parsed the resume and added the candidate's own project as
    #     a job: that entry, and only that one, is taken out again.
    titles = lambda: page.locator(".exp input").evaluate_all(
        "els => els.filter((_, i) => i % 2 === 0).map(e => e.value)")
    print("  experience entries before:", titles())
    removed = 0
    for _ in range(4):
        if not worker._remove_excluded_entries(page, browser.snapshot(page), set(), sess):
            break
        removed += 1
    after = titles()
    print(f"  removed {removed}; entries now: {after}")
    if after != ["Software Engineer", "Software Engineer Intern"]:
        failures.append(f"work experience left as {after}")

    # 3. the Languages section opens itself, once per profile language
    clicks = 0
    for _ in range(5):
        fields = browser.snapshot(page)
        if not worker._open_profile_sections(page, fields, set(), sess):
            break
        clicks += 1
    rows = page.locator("#langs select").count()
    print(f"  Add Language clicked {clicks}x, language rows: {rows // 2}")
    if clicks != 2 or rows != 4:
        failures.append(f"languages: {clicks} clicks, {rows} selects")
    fields = browser.snapshot(page)
    for want, label in (("English", "Language"), ("Intermediate", "Proficiency")):
        entry = next((f for f in fields if f["label"] == label), None)
        got = resolver.resolve(entry) if entry else None
        if not got or got[0] != want:
            failures.append(f"first {label} entry resolved to {got}")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nPHENOM CHECK PASSED")
