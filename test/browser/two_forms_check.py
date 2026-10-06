"""What the agent reads off a careers page carrying a second form.

CryptoMize (Oct 2 2026): the agent filled a hidden honeypot "company" box,
filled the footer "Contact us" form on every pass and had the model draft
its Message, and read the Gender and Marital status radio groups with each
option's question set to the option before it - so a reply of "single" was
banked under the question "Single Other".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


with sync_playwright() as pw:
    chromium = pw.chromium.launch(headless=True)
    page = chromium.new_page()
    page.goto((HERE / "fixture_two_forms.html").as_uri())
    fields = browser.snapshot(page)
    labels = [str(f.get("label") or f.get("name") or "").split("\n")[0].strip("* ") for f in fields]
    print("  read:", labels)

    print("\n== honeypots ==")
    check("no hidden 'company' box is read", "company" not in [f.get("name") for f in fields],
          str([f.get("name") for f in fields]))

    print("\n== the footer contact form ==")
    check("its Message box is not read", "Message" not in labels)
    check("nor its Send button", not any((f.get("text") or "").strip() == "Send" for f in fields))
    check("and the agent is told why", bool(browser.last_side_forms()) and
          "Message" in browser.last_side_forms()[0], str(browser.last_side_forms()))
    check("the application's own boxes all stay",
          all(name in labels for name in ("Full Name", "Resume", "Professional Details", "Qualifications")),
          str(labels))

    print("\n== radio groups named by aria-label ==")
    groups = {str(f.get("label")): f.get("group") for f in fields if f.get("role") == "radio"}
    print("  groups:", groups)
    check("Male answers Gender", groups.get("Male") == "Gender", repr(groups.get("Male")))
    check("Other answers Gender", groups.get("Other") == "Gender", repr(groups.get("Other")))
    check("Single answers Marital Status, not 'Other'",
          groups.get("Single") == "Marital Status", repr(groups.get("Single")))

    print("\n== a careers site's job search bar ==")
    # Intuit (Oct 2 2026): "[profile] Filled Location = Gurgaon, India" went
    # into "Search Jobs by Keyword / Location / Search Jobs".
    page.evaluate("""() => {
      const bar = document.createElement('form');
      bar.innerHTML = '<label for=kw>Search Jobs by Keyword</label><input id=kw>' +
                      '<label for=loc>Location</label><input id=loc>' +
                      '<button type=submit>Search Jobs</button>';
      document.body.prepend(bar);
    }""")
    with_bar = browser.snapshot(page)
    bar_labels = [str(f.get("label") or "").split("\n")[0].strip("* ") for f in with_bar]
    check("the search bar is set aside", "Location" not in bar_labels
          and "Search Jobs by Keyword" not in bar_labels, str(bar_labels))
    check("and said to be a job search", any("job search" in s for s in browser.last_side_forms()),
          str(browser.last_side_forms()))
    check("the application keeps every box",
          all(n in bar_labels for n in ("Full Name", "Professional Details", "Qualifications")),
          str(bar_labels))
    page.evaluate("() => document.forms[0].remove()")

    print("\n== the footer form alone, before the application renders ==")
    # On the first read the application had not appeared yet, the footer
    # form was the only one, and it was filled and its Message drafted.
    page.evaluate("() => document.forms[0].remove()")
    alone = browser.snapshot(page)
    alone_labels = [str(f.get("label") or "").split("\n")[0].strip("* ") for f in alone]
    check("a footer contact form is set aside even alone",
          "Message" not in alone_labels and "E-Mail Address" not in alone_labels, str(alone_labels))

    chromium.close()

print()
if problems:
    print("TWO FORMS CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("TWO FORMS CHECK PASSED")
