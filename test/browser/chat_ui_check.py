"""The answer box in the dashboard: a drafted paragraph must be readable and
editable without dragging a cursor along a one-line input."""
import re
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = ROOT / "src" / "web"


def serve(directory: Path) -> str:
    """The page asks for /static/app.js, so it has to come over HTTP."""
    import functools
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"

DRAFT = ("Over the past year I went deep on building LLM-powered agents, something that wasn't on my "
         "radar before. It started as a self-directed deep dive after I saw how much AI-assisted coding "
         "was changing my day-to-day work, and it grew into two open-sourced projects: a Stock Discovery "
         "Agent and an Opportunity Explorer Agent.")

# The page is served straight from disk; the apply endpoints are never called,
# so the harness drives the same functions the SSE stream would.
failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    # The Dump button copies the dump's folder to the clipboard; without this
    # the write is refused and the check would pass on a stub.
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    # The dashboard's data endpoints are not running here; only script errors
    # from the page itself count.
    page.on("pageerror", lambda e: failures.append(f"page error: {str(e)[:120]}")
            if "not found" not in str(e).lower() and "fetch" not in str(e).lower() else None)
    page.goto(f"{serve(WEB)}/static/index.html")
    page.wait_for_function("() => typeof setChatEnabled === 'function'", timeout=10000)

    box = page.locator("#chat")
    print("  chat control is a:", box.evaluate("el => el.tagName"))
    if box.evaluate("el => el.tagName") != "TEXTAREA":
        failures.append("the answer box is not a textarea")

    page.evaluate("() => { setChatEnabled(true); }")
    one_line = box.bounding_box()["height"]
    page.evaluate("(text) => fillChat(text, text)", DRAFT)
    page.wait_for_timeout(150)
    grown = box.bounding_box()["height"]
    print(f"  height empty: {one_line:.0f}px -> with the draft: {grown:.0f}px")
    if grown <= one_line:
        failures.append(f"the box did not grow ({one_line:.0f} -> {grown:.0f})")

    # A long answer (the cover-letter length) grows further and then caps,
    # scrolling down rather than sideways.
    page.evaluate("(text) => fillChat(text, text)", DRAFT * 4)
    page.wait_for_timeout(150)
    tall = box.bounding_box()["height"]
    print(f"  a {len(DRAFT) * 4}-character answer: {tall:.0f}px tall (cap 260)")
    if tall <= grown or tall > 262:
        failures.append(f"the long answer box is {tall:.0f}px")
    if box.evaluate("el => el.scrollWidth - el.clientWidth") > 2:
        failures.append("the long answer runs off sideways")
    page.evaluate("(text) => fillChat(text, text)", DRAFT)
    page.wait_for_timeout(100)

    # the whole draft is visible without scrolling the box sideways
    overflow = box.evaluate("el => el.scrollWidth - el.clientWidth")
    hidden = box.evaluate("el => el.scrollHeight - el.clientHeight")
    print(f"  sideways overflow: {overflow}px, hidden below: {hidden}px")
    if overflow > 2:
        failures.append(f"the text still runs off sideways ({overflow}px)")
    if hidden > 2:
        failures.append(f"{hidden}px of the draft is out of sight")
    if box.input_value() != DRAFT:
        failures.append("the draft is not in the box")

    counter = page.locator("#chatcount").inner_text()
    print("  counter:", counter)
    if str(len(DRAFT)) not in counter:
        failures.append(f"the length is not shown ({counter!r})")

    # the draft tools appear only while there is a draft
    for tool in ("#redraft", "#restoredraft"):
        if page.locator(tool).is_hidden():
            failures.append(f"{tool} is hidden while a draft is loaded")
    page.click("#redraft")
    print("  after 'Ask for changes':", repr(box.input_value()[:40]))
    if not box.input_value().lower().startswith("llm:"):
        failures.append(f"'Ask for changes' did not prefix llm: ({box.input_value()[:30]!r})")
    page.click("#restoredraft")
    if box.input_value() != DRAFT:
        failures.append("'Restore draft' did not put the draft back")

    # Shift+Enter writes a new line; Enter sends (and clears the box)
    page.evaluate("() => { window.__sent = []; window.postJSON = async (u, p) => { window.__sent.push(p.text); return {}; }; applySessionId = 'x'; }")
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("first line")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("second line")
    two = box.input_value()
    print("  Shift+Enter gives:", repr(two))
    if "\n" not in two:
        failures.append("Shift+Enter did not start a new line")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    sent = page.evaluate("() => window.__sent")
    print("  Enter sent:", [s[:30] for s in sent], "| box now:", repr(box.input_value()))
    if not sent or "second line" not in sent[0]:
        failures.append(f"Enter did not send the answer ({sent})")
    if box.input_value():
        failures.append("the box was not cleared after sending")
    if page.locator("#redraft").is_visible():
        failures.append("the draft tools stayed up after sending")
    # the transcript: the source tag and the action word are set in capitals,
    # and the text itself is never rewritten
    SAMPLE = [
        "[profile] Filled Email address* = a_candidate@example.invalid",
        "Selected 'LinkedIn' for How did you first hear about this opportunity?",
        "Could not fill Field of study*: no option matched",
        "Asking the model about 22 field(s)…",
        "AGENT ASKS: This step is filled in.",
    ]
    page.evaluate("(lines) => { resetApply(''); lines.forEach(line => appendApply(line)); }", SAMPLE)
    page.wait_for_timeout(150)
    shown = page.locator("#applylog .logline").all_text_contents()
    caps = page.evaluate("""() => Array.from(document.querySelectorAll('#applylog .tag, #applylog .verb'))
        .map(el => getComputedStyle(el).textTransform + ':' + el.textContent)""")
    print("  capitalised heads:", caps)
    for original, rendered in zip(SAMPLE, shown):
        if original != rendered:
            failures.append(f"the line was rewritten: {rendered!r}")
    if not any(c.startswith("uppercase:[profile]") for c in caps):
        failures.append("the source tag is not capitalised")
    if not any(c.startswith("uppercase:Filled") for c in caps):
        failures.append("the action word is not capitalised")
    if not any("Could not fill" in c for c in caps):
        failures.append("a failure line has no capitalised head")
    # ---- the Dump button ----
    # One path through the worker: the button sends the same word the chat
    # accepts, so it cannot drift from what typing `dump` does.
    page.evaluate("() => { window.__sent = []; applySessionId = 'x'; setChatEnabled(true); }")
    page.click("#dump")
    page.wait_for_timeout(150)
    sent = page.evaluate("() => window.__sent")
    print("\n  Dump sent:", sent)
    if sent != ["dump"]:
        failures.append(f"the Dump button did not send 'dump' ({sent})")

    # The worker names the folder in its own log line, so the button never has
    # to be told separately where the dump went - which is also why typing
    # `dump` gets the path copied, for free.
    FOLDER = "/home/x/OpportunityExplorerAgent/outputs/dom/20260101T000000_20260918-0042"
    page.evaluate(
        "(folder) => copyDumpPath({type: 'step', text: 'Page dumped to ' + folder +"
        " ' (page.html, fields.json, screenshot.png). Still waiting for your answer.'})",
        FOLDER)
    page.wait_for_timeout(250)
    copied = page.evaluate("() => navigator.clipboard.readText()")
    print("  clipboard:", repr(copied))
    if copied != FOLDER:
        failures.append(f"the folder path was not copied cleanly ({copied!r})")
    if "on your clipboard" not in page.locator("#applylog").inner_text():
        failures.append("the user was not told the path had been copied")

    # A step line that is not a dump must leave the clipboard alone.
    page.evaluate("() => navigator.clipboard.writeText('untouched')")
    page.evaluate("() => copyDumpPath({type: 'step', text: 'Filled Full name = Test User'})")
    page.wait_for_timeout(150)
    if page.evaluate("() => navigator.clipboard.readText()") != "untouched":
        failures.append("an ordinary log line overwrote the clipboard")

    b.close()

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nCHAT UI CHECK PASSED")
