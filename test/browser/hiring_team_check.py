"""Who posted a job, read from a LinkedIn posting's "Meet the hiring team"
(the People page, #51), in a real browser on fixture pages.

The real reader opens the posting in the apply window's signed-in Chrome
profile; here a plain headless Chromium opens a fixture shaped like that
section, so no profile and no LinkedIn page is touched."""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import people  # noqa: E402

HERE = Path(__file__).resolve().parent
problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()

    page.goto((HERE / "fixture_hiring_team.html").as_uri())
    result, found = people.read_page_team(page, wait_s=2)
    check("the section is found", result == "found", result)
    check("two people, the photo link not counted twice", len(found) == 2, found)
    rita = found[0] if found else {}
    check("the name, without the degree badge", rita.get("name") == "Rita Sharma", rita)
    check("the headline is the line under the name",
          rita.get("headline") == "Engineering Manager at Deltek | Hiring for backend", rita)
    check("the profile link without its tracking query",
          rita.get("profile_url") == "https://www.linkedin.com/in/rita-sharma-12ab", rita)
    arjun = found[1] if len(found) > 1 else {}
    check("a relative link and a 3rd+ badge", (arjun.get("name"), arjun.get("headline"), arjun.get("profile_url"))
          == ("Arjun K.", "Talent Partner, Deltek India", "https://www.linkedin.com/in/arjun-k"), arjun)
    check("a profile elsewhere on the page is not the hiring team",
          all("someone-else" not in p["profile_url"] for p in found))

    # A posting without the section: none, after the wait.
    page.set_content("<h1>Backend Engineer</h1><p>About the job</p><a href='/in/x'>Someone</a>")
    result, found = people.read_page_team(page, wait_s=1)
    check("no section reads as none", (result, found) == ("none", []), result)

    # The section drawn late, as LinkedIn does after its first paint.
    page.set_content("<h1>Backend Engineer</h1><div id=late></div><script>setTimeout(() => {"
                     "document.getElementById('late').innerHTML = '<h2>Meet the hiring team</h2>"
                     "<div><a href=\"/in/late-one\">Late One</a><div>Recruiter</div></div>'; }, 1200);</script>")
    result, found = people.read_page_team(page, wait_s=5)
    check("a section drawn late is waited for", result == "found" and found and found[0]["name"] == "Late One", found)

    browser.close()

print(f"\n{'ALL PASS' if not problems else 'FAILURES: ' + ', '.join(problems)}")
sys.exit(1 if problems else 0)
