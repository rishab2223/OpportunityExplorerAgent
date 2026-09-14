"""Combobox commit check against two replicas, running the real _apply_value:

1. SF-style: options appear on input, Enter is swallowed - the option must
   be clicked.
2. react-select-style (Greenhouse): the menu opens only on CLICK, the page
   keeps a HIDDEN list of every option ("India+91") earlier in the DOM,
   filtered options carry extras ("India +91"), and Enter takes the first
   filtered option ("British Indian Ocean Territory +246" - the +246 bug).
"""
import sys

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright

from src.apply.worker import _apply_value

SF_PAGE = """
<label for="emp">Have you been employed before?</label>
<input id="emp" data-oea-id="1" role="combobox" aria-haspopup="listbox" autocomplete="off">
<ul id="list" role="listbox" style="display:none">
  <li role="option" onclick="pick('Yes')">Yes</li>
  <li role="option" onclick="pick('No')">No</li>
</ul>
<script>
  const inp = document.getElementById('emp');
  const list = document.getElementById('list');
  inp.addEventListener('input', () => { list.style.display = 'block'; });
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') e.preventDefault(); });
  window.committed = '';
  function pick(v) { inp.value = v; window.committed = v; list.style.display = 'none'; }
</script>
"""

RS_PAGE = """
<div style="display:none">
  <div role="option">Afghanistan+93</div><div role="option">India+91</div>
</div>
<label id="country-label" for="country">Country</label>
<div class="control"><span id="shown"></span>
<input id="country" data-oea-id="2" role="combobox" aria-haspopup="true" aria-expanded="false"
       aria-labelledby="country-label" autocomplete="off"></div>
<div id="menu" style="display:none"><div id="country-listbox" role="listbox"></div></div>
<script>
  const ALL = ['United States +1', 'Afghanistan +93', 'British Indian Ocean Territory +246', 'India +91', 'Indonesia +62'];
  const inp = document.getElementById('country');
  const menu = document.getElementById('menu');
  const lb = document.getElementById('country-listbox');
  let open = false, focused = 0;
  window.committed = '';
  function render() {
    const q = inp.value.toLowerCase();
    const items = ALL.filter(o => o.toLowerCase().includes(q));
    lb.innerHTML = '';
    items.forEach((o, i) => {
      const d = document.createElement('div'); d.setAttribute('role', 'option');
      d.textContent = o; if (i === focused) d.setAttribute('aria-selected', 'true');
      d.addEventListener('click', () => commit(o)); lb.appendChild(d);
    });
    return items;
  }
  function setOpen(v) {
    open = v; menu.style.display = v ? 'block' : 'none';
    inp.setAttribute('aria-expanded', String(v));
    if (v) inp.setAttribute('aria-controls', 'country-listbox'); else inp.removeAttribute('aria-controls');
    if (v) render();
  }
  function commit(o) { window.committed = o; document.getElementById('shown').textContent = o; inp.value = ''; setOpen(false); }
  // react-select: opens on mousedown/click, NOT on typing into a closed widget
  inp.addEventListener('mousedown', () => { focused = 0; setOpen(true); });
  inp.addEventListener('input', () => { if (open) { focused = 0; render(); } });
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && open) { const items = render(); if (items[focused]) commit(items[focused]); e.preventDefault(); }
    if (e.key === 'ArrowDown' && open) { focused++; render(); }
  });
  inp.addEventListener('blur', () => { inp.value = ''; setOpen(false); });
  // like react-select: a mousedown on the menu must not blur the input
  lb.addEventListener('mousedown', (e) => e.preventDefault());
</script>
"""


class StubSess:
    def log(self, text):
        print("  LOG", text)


sf_field = {"id": 1, "tag": "input", "type": "text", "label": "Have you been employed before?",
            "role": "combobox", "haspopup": "listbox", "name": "emp", "group": "", "text": ""}
rs_field = {"id": 2, "tag": "input", "type": "text", "label": "Country",
            "role": "combobox", "haspopup": "true", "name": "", "elid": "country", "group": "", "text": ""}

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(SF_PAGE)
    _apply_value(page, sf_field, "no", "", StubSess())
    committed = page.evaluate("window.committed")
    assert committed == "No", f"SF combobox committed {committed!r}, wanted 'No'"

    # set_content keeps the same window: top-level consts would collide, so a
    # fresh page per replica.
    page = b.new_page()
    page.set_content(RS_PAGE)
    _apply_value(page, rs_field, "India", "", StubSess())
    page.keyboard.press("Tab")
    committed = page.evaluate("window.committed")
    assert committed == "India +91", f"react-select committed {committed!r}, wanted 'India +91'"

    # No start-match among several visible options: refuse loudly, never Enter.
    page = b.new_page()
    page.set_content(RS_PAGE)
    try:
        _apply_value(page, rs_field, "Ind", "", StubSess())
    except ValueError as exc:
        assert "pick one of" in str(exc), str(exc)
        print("  refused:", str(exc)[:90])
    else:
        raise AssertionError("ambiguous 'Ind' should have been refused")
    assert page.evaluate("window.committed") == "", "ambiguous value committed something"
    # Location typeahead: the list filters on the typed name, and the
    # candidate's city is listed under its new name in the right state.
    page = b.new_page()
    page.set_content(RS_PAGE.replace("'United States +1', 'Afghanistan +93', 'British Indian Ocean Territory +246', 'India +91', 'Indonesia +62'",
                                     "'Bangalore, Kerala, India', 'Bengaluru, Karnataka, India', 'Bhagalpur, Odisha, India'"))
    from unittest import mock
    loc_field = dict(rs_field, label="Location (City)")
    with mock.patch("src.apply.profile.load_profile", return_value={"state": "Karnataka", "location": "Bangalore, India"}):
        _apply_value(page, loc_field, "Bangalore", "", StubSess())
    page.keyboard.press("Tab")
    committed = page.evaluate("window.committed")
    assert committed == "Bengaluru, Karnataka, India", f"location committed {committed!r}"
    b.close()

print("COMBO CHECK PASSED: SF click-commit, react-select click-to-open + scoped visible option, ambiguity refused")
