"""A PrimeNG (Angular) profile form, as MyCareernet's (Oct 2026).

Four things went wrong on the real one, each replicated here:

  - the form is a modal that scrolls inside itself, and once the candidate
    had scrolled down, the boxes above the viewport were dropped from the
    scan as "off the page";
  - the date of birth, typed key by key, was re-read by the picker as it
    went and ended up "10 Jan, 2003"; set in one go it stays;
  - the city dropdown has no search box: typing at it jumps the list, and
    "Gurgaon" is listed under its new name "Gurugram";
  - the multiselects (skills, preferred locations) keep their combobox at
    1x1 behind a clipped wrapper, so they were not read at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


CITIES = ["Agra", "Ahmedabad", "Ajmer", "Aligarh", "Allahabad", "Ambala", "Amritsar", "Bengaluru",
          "Bhopal", "Bhubaneswar", "Chandigarh", "Chennai", "Coimbatore", "Dehradun", "Delhi",
          "Faridabad", "Gandhinagar", "Ghaziabad", "Goa", "Gurugram", "Guwahati", "Gwalior",
          "Hyderabad", "Indore", "Injambakkam", "Irinjalakuda", "Jabalpur", "Jaipur", "Jalandhar",
          "Jammu", "Jodhpur", "Kanpur", "Kochi", "Kolkata", "Lucknow", "Ludhiana", "Madurai",
          "Mangaluru", "Meerut", "Mohali", "Mumbai", "Mysuru", "Nagpur", "Nashik", "Noida",
          "Patna", "Pune", "Raipur", "Rajkot", "Ranchi", "Surat", "Thane", "Trichy",
          "Thiruvananthapuram", "Udaipur", "Vadodara", "Varanasi", "Vijayawada", "Visakhapatnam"]
SKILLS = ["Angular", "AWS - Cloud", "C++", "Docker", "Java", "Node JS", "PostgreSQL", "Python",
          "React", "RESTful API", "Spring Boot"]


def options_html(items):
    return "".join(f'<li role="option" data-v="{c}"><input type="checkbox" tabindex="-1"> {c}</li>' for c in items)


PAGE = """<!doctype html><html><head><style>
  body { margin: 0; font-family: sans-serif; }
  #modal { position: fixed; top: 0; left: 100px; width: 700px; height: 480px; overflow: auto;
           background: #fff; border: 1px solid #999; padding: 12px; }
  .row { margin: 6px 0; } label { display: block; font-size: 12px; }
  input[type=text], input:not([type]) { width: 300px; }
  ul[role=listbox] { list-style: none; margin: 0; padding: 0; border: 1px solid #888; height: 160px;
                     overflow: auto; background: #fff; }
  ul[role=listbox] li { padding: 2px 6px; } ul[role=listbox] li.hi { background: #cde; }
  .p-hidden-accessible { position: absolute; width: 1px; height: 1px; overflow: hidden;
                         clip: rect(0 0 0 0); }
  .p-multiselect { border: 1px solid #888; padding: 6px; width: 320px; cursor: pointer; }
</style></head><body>
<div id="modal" role="dialog" aria-modal="true">
  <h3>Just One Step Left - Complete Your Profile!</h3>
  ROWS
  <div class="row"><label for="dateOfBirth">*Date Of Birth</label>
    <input id="dateOfBirth" bsdatepicker placeholder="Please Select your DOB" autocomplete="off"></div>
  <div class="row"><label>*Current Location</label>
    <div class="p-dropdown">
      <span id="loc" role="combobox" aria-haspopup="listbox" aria-expanded="false" tabindex="0"
            aria-label="Select your current location">Select your current location</span>
      <ul id="locpanel" role="listbox" hidden>CITY_OPTIONS</ul>
    </div></div>
  <div class="row"><label for="skills-name">*Primary Skills</label>
    <div class="p-multiselect" id="skills">
      <div class="p-hidden-accessible"><input role="combobox" aria-haspopup="listbox" aria-expanded="false" tabindex="0" value=""></div>
      <div class="p-multiselect-label">Select Skills</div>
      <ul role="listbox" class="p-multiselect-panel" hidden>SKILL_OPTIONS</ul>
    </div></div>
  <div class="row"><label for="cities-name">*Preferred Location</label>
    <div class="p-multiselect" id="cities">
      <div class="p-hidden-accessible"><input role="combobox" aria-haspopup="listbox" aria-expanded="false" tabindex="0" value=""></div>
      <div class="p-multiselect-label">Search and select preferred Location</div>
      <ul role="listbox" class="p-multiselect-panel" hidden>CITY_OPTIONS</ul>
    </div></div>
  <button type="submit">Save</button>
</div>
<script>
  // The date picker reads the box as it is typed and rewrites it (ngx-bootstrap).
  const dob = document.getElementById('dateOfBirth');
  dob.addEventListener('keyup', () => { if (dob.value.length < 10) dob.value = '10 Jan, 2003'; });
  // The dropdown: click toggles; letters jump to the first option starting with them.
  const loc = document.getElementById('loc'), panel = document.getElementById('locpanel');
  let buffer = '', timer = null;
  loc.addEventListener('click', () => { panel.hidden = !panel.hidden; loc.setAttribute('aria-expanded', String(!panel.hidden)); loc.focus(); });
  loc.addEventListener('keydown', (ev) => {
    if (ev.key.length !== 1) return;
    buffer += ev.key.toLowerCase(); clearTimeout(timer); timer = setTimeout(() => { buffer = ''; }, 500);
    const hit = Array.from(panel.children).find(li => li.dataset.v.toLowerCase().startsWith(buffer));
    panel.querySelectorAll('li.hi').forEach(li => li.classList.remove('hi'));
    if (hit) { hit.classList.add('hi'); hit.scrollIntoView({ block: 'start' }); }
  });
  panel.addEventListener('click', (ev) => {
    const li = ev.target.closest('li'); if (!li) return;
    loc.textContent = li.dataset.v; panel.hidden = true; loc.setAttribute('aria-expanded', 'false');
  });
  // The multiselects: click toggles the panel; a row toggles its tick.
  for (const ms of document.querySelectorAll('.p-multiselect')) {
    const input = ms.querySelector('input'), label = ms.querySelector('.p-multiselect-label'),
          list = ms.querySelector('ul'), placeholder = label.textContent;
    ms.addEventListener('click', (ev) => {
      if (ev.target.closest('ul')) return;
      list.hidden = !list.hidden; input.setAttribute('aria-expanded', String(!list.hidden));
    });
    list.addEventListener('click', (ev) => {
      const li = ev.target.closest('li'); if (!li) return;
      const box = li.querySelector('input'); if (ev.target !== box) box.checked = !box.checked;
      const picked = Array.from(list.querySelectorAll('input:checked')).map(b => b.closest('li').dataset.v);
      input.value = picked.join(', '); label.textContent = picked.join(', ') || placeholder;
    });
  }
</script></body></html>"""
PAGE = (PAGE.replace("ROWS", "".join(
            f'<div class="row"><label for="f{i}">Field {i}</label><input id="f{i}" name="f{i}"></div>'
            for i in range(1, 26)))
        .replace("CITY_OPTIONS", options_html(CITIES)).replace("SKILL_OPTIONS", options_html(SKILLS)))


class Sess:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, line):
        self.lines.append(line)


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1000, "height": 600})
    page.set_content(PAGE)
    page.eval_on_selector("#modal", "m => m.scrollTop = m.scrollHeight")   # the candidate scrolled down

    print("== a form that scrolls inside itself ==")
    fields = browser.snapshot(page)
    labels = [str(f.get("label") or "").strip() for f in fields]
    check("the boxes scrolled above the viewport are still read",
          all(f"Field {i}" in labels for i in (1, 2, 25)), str(labels[:4]))
    by = {lab: f for lab, f in zip(labels, fields)}
    skills = next((f for f in fields if f.get("multi") and "Skills" in str(f.get("label"))), None)
    cities = next((f for f in fields if f.get("multi") and "Preferred Location" in str(f.get("label"))), None)
    check("a PrimeNG multiselect is read as a multi-choice list, under the label above it",
          skills is not None and cities is not None and skills.get("role") == "combobox"
          and skills.get("haspopup") == "listbox", str([(f.get("label"), f.get("multi")) for f in fields[-6:]]))
    check("  and its 1x1 twin is not a second field",
          sum(1 for lab in labels if "Preferred Location" in lab) == 1, str(labels[-6:]))
    from src.apply import resolver
    check("its placeholder reads as empty", cities is not None and resolver.is_blank(cities))

    sess = Sess()
    print("== the date of birth ==")
    dob = by["*Date Of Birth"]
    check("the box is read as a date box", worker._is_date_box(dob) and dob.get("datepicker") is True)
    worker._apply_value(page, dob, "05/11/1990", "", sess, source="profile")
    check("5 November 1990 goes in month-first, as the box wants, in one go",
          page.input_value("#dateOfBirth") == "11/05/1990", page.input_value("#dateOfBirth"))

    print("== the city dropdown ==")
    loc = by["Select your current location"]
    worker._apply_value(page, loc, "Gurgaon", "", sess, source="profile")
    check("'Gurgaon' finds 'Gurugram' by typing at the list",
          page.text_content("#loc") == "Gurugram", page.text_content("#loc"))
    check("  and says what it chose", any("Selected 'Gurugram'" in ln for ln in sess.lines), str(sess.lines[-2:]))

    print("== the multiselects ==")
    worker._apply_value(page, cities, "Bengaluru, Hyderabad, Pune", "", sess, source="profile")
    check("three preferred locations are ticked in one open list",
          page.input_value("#cities input") == "Bengaluru, Hyderabad, Pune", page.input_value("#cities input"))
    check("  and the list is closed again", page.is_hidden("#cities ul"))
    check("'*Primary Skills' is a skills box despite the star", worker._is_skills_box(skills))
    worker._fill_skills(page, skills, ["PostgreSQL", "Docker", "Kafka"], Sess())
    check("the skills the list offers are ticked, the rest named",
          page.input_value("#skills input") == "Docker, PostgreSQL", page.input_value("#skills input"))

    print("== the plan order ==")
    acts = [worker.ApplyAction(action="check", field_id=1, value="yes", confidence=0.9),
            worker.ApplyAction(action="fill", field_id=2, value="x", confidence=0.9),
            worker.ApplyAction(action="ask", field_id=3, value="", confidence=0.9)]
    consent = [{"id": 1, "label": "I consent to WhatsApp messages", "tag": "input", "type": "checkbox"},
               {"id": 2, "label": "City", "tag": "input", "type": "text"}, {"id": 3, "label": "Q", "tag": "input"}]
    order = [a.field_id for a in sorted(acts, key=lambda a: worker._pauses_for_user(a, consent))]
    check("fills come before anything that asks the candidate", order == [2, 1, 3], str(order))
    b.close()

print()
if problems:
    print("PRIMENG CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("PRIMENG CHECK PASSED")
