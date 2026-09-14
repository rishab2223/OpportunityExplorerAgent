from __future__ import annotations

import unittest

from src.apply import sites
from src.apply.sites import linkedin


class ClosedDetectionTests(unittest.TestCase):
    JOB_URL = "https://in.linkedin.com/jobs/view/software-engineer-at-servify-4458960558"

    def test_banner_text(self) -> None:
        self.assertTrue(linkedin.is_closed(
            "Software Engineer\nServify\nNo longer accepting applications", self.JOB_URL))
        self.assertTrue(linkedin.is_closed("This job is no longer available", self.JOB_URL))

    def test_a_posting_that_was_taken_down(self) -> None:
        # LinkedIn shows this instead of a banner, and the page has no form
        # fields, so without it the candidate was asked to paste a URL for a
        # job that no longer exists.
        self.assertTrue(linkedin.is_closed(
            "Unable to load the page\nJob id provided may not be valid or the job "
            "posting has been removed.\nGo to Jobs", self.JOB_URL))

    def test_redirect_to_search_page(self) -> None:
        self.assertTrue(linkedin.is_closed(
            "", "https://www.linkedin.com/jobs/search/?keywords=software"))
        self.assertTrue(linkedin.is_closed(
            "", "https://www.linkedin.com/jobs/collections/recommended/"))

    def test_open_job_is_not_closed(self) -> None:
        self.assertFalse(linkedin.is_closed(
            "Software Engineer at Servify\nEasy Apply\nSave", self.JOB_URL))
        self.assertFalse(linkedin.is_closed("", "https://employer.example/apply/form"))


class PickApplyTests(unittest.TestCase):
    def test_top_card_apply_beats_similar_jobs_easy_apply(self) -> None:
        buttons = [
            {"id": 3, "tag": "button", "text": "Apply", "label": ""},          # top card
            {"id": 9, "tag": "button", "text": "Easy Apply", "label": ""},     # similar jobs rail
        ]
        target, kind = linkedin.pick_apply(buttons)
        self.assertEqual(target["id"], 3)
        self.assertEqual(kind, "external")

    def test_easy_apply_on_top_card(self) -> None:
        buttons = [
            {"id": 2, "tag": "button", "text": "Easy Apply", "label": ""},
            {"id": 8, "tag": "button", "text": "Apply", "label": ""},
        ]
        target, kind = linkedin.pick_apply(buttons)
        self.assertEqual(target["id"], 2)
        self.assertEqual(kind, "easy_apply")

    def test_no_apply_control(self) -> None:
        self.assertEqual(
            linkedin.pick_apply([{"id": 1, "tag": "button", "text": "Save", "label": ""}]),
            (None, ""),
        )


class PageStateTests(unittest.TestCase):
    """What the page is showing, which is what replaced asking the candidate
    "type done when it has loaded"."""

    JOB_URL = "https://in.linkedin.com/jobs/view/software-engineer-at-servify-4458960558"

    def state(self, fields, text="Software Engineer at Servify") -> str:
        return linkedin.page_state(fields, text, self.JOB_URL)

    def test_nothing_rendered_yet(self) -> None:
        # The shell is there, the job card is not: keep waiting.
        self.assertEqual(self.state([]), "")
        self.assertEqual(
            self.state([{"tag": "a", "text": "Jobs", "label": ""},
                        {"tag": "button", "text": "Skip to search", "label": ""}]),
            "",
        )

    def test_an_apply_button_is_ready(self) -> None:
        self.assertEqual(
            self.state([{"tag": "button", "text": "Easy Apply", "label": ""}]), "apply")
        self.assertEqual(
            self.state([{"tag": "button", "text": "Apply", "label": ""}]), "apply")

    def test_a_closed_banner_beats_a_leftover_apply_button(self) -> None:
        got = self.state([{"tag": "button", "text": "Apply", "label": ""}],
                         "Servify\nNo longer accepting applications")
        self.assertEqual(got, "closed")

    def test_login_beats_apply(self) -> None:
        # A logged-out page shows both; clicking Apply bounces to the authwall.
        got = self.state([{"tag": "button", "text": "Apply", "label": ""},
                          {"tag": "a", "text": "Sign in", "label": ""}])
        self.assertEqual(got, "login")

    def test_a_removed_posting_reads_as_closed(self) -> None:
        got = self.state([], "Unable to load the page\nJob id provided may not be valid "
                             "or the job posting has been removed.")
        self.assertEqual(got, "closed")


class WaitForPageTests(unittest.TestCase):
    class FakePage:
        """Renders its job card after `ticks` polls, like LinkedIn does after
        domcontentloaded."""

        def __init__(self, ticks: int, fields=None, text: str = "") -> None:
            self.ticks = ticks
            self.polls = 0
            self.waited = 0
            self.url = "https://www.linkedin.com/jobs/view/123"
            self._fields = fields if fields is not None else [
                {"tag": "button", "text": "Easy Apply", "label": ""}]
            self._text = text

        def wait_for_timeout(self, ms: int) -> None:
            self.waited += ms

    def _patch(self, page):
        """Stand in for browser.snapshot / page_text against the fake page."""
        from src.apply import browser

        def snapshot(target):
            target.polls += 1
            return target._fields if target.polls > target.ticks else []

        def page_text(target, limit=2500):
            return target._text if target.polls > target.ticks else ""

        self.addCleanup(setattr, browser, "snapshot", browser.snapshot)
        self.addCleanup(setattr, browser, "page_text", browser.page_text)
        browser.snapshot = snapshot
        browser.page_text = page_text

    def test_a_page_that_is_ready_at_once_does_not_wait(self) -> None:
        page = self.FakePage(ticks=0)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "apply")
        self.assertEqual(page.waited, 0)      # no sleep at all

    def test_a_slow_card_is_waited_for(self) -> None:
        page = self.FakePage(ticks=3)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "apply")
        self.assertEqual(page.waited, 3 * linkedin.READY_STEP)

    def test_a_page_that_never_renders_gives_up_and_says_so(self) -> None:
        # The caller then hands back to the candidate rather than guessing.
        page = self.FakePage(ticks=10**6)
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page, timeout=1000), "")
        self.assertLessEqual(page.waited, 1000)

    def test_a_closed_posting_is_recognised_without_a_keystroke(self) -> None:
        page = self.FakePage(ticks=1, fields=[], text="No longer accepting applications")
        self._patch(page)
        self.assertEqual(linkedin.wait_for_page(page), "closed")


class FakeDialog:
    """A dialog element: what it is CALLED is what tells the apply modal apart
    from LinkedIn's messaging bubble - never what it contains, since the
    messaging bubble contains recruiters' messages and those say "apply" in
    nearly every one. Whether it was stamped is what tells one the click
    opened from one that was already there."""

    def __init__(self, label: str, visible: bool = True, body: str = ""):
        self.label = label
        self.visible = visible
        self.body = body
        self.stamped = False

    def get_attribute(self, name: str) -> str:
        return self.label if name == "aria-label" else ""

    def inner_text(self) -> str:
        return f"{self.label} {self.body}"

    def locator(self, selector: str):
        # Headings repeat the label; the body is not a heading.
        return FakeLocator([self.label]) if "h1" in selector else FakeLocator([])


class FakeLocator:
    def __init__(self, matches):
        self.matches = matches

    def count(self) -> int:
        return len(self.matches)

    def nth(self, index: int):
        return self.matches[index]

    def all_inner_texts(self) -> list:
        return [m if isinstance(m, str) else m.inner_text() for m in self.matches]

    def evaluate_all(self, script: str) -> None:
        for dialog in self.matches:
            dialog.stamped = True


def _tab():
    return type("P", (), {"is_closed": staticmethod(lambda: False)})()


class FakePage:
    def __init__(self, dialogs=(), tabs: int = 1,
                 url: str = "https://www.linkedin.com/jobs/view/1"):
        self.url = url
        self.dialogs = [FakeDialog(name) for name in dialogs]
        self.waited = 0
        self.context = type("C", (), {"pages": [_tab() for _ in range(tabs)]})()

    def wait_for_timeout(self, ms: int) -> None:
        self.waited += ms

    def locator(self, selector: str):
        found = [d for d in self.dialogs if d.visible or ":visible" not in selector]
        if f"not([{linkedin.PRE_DIALOG_ATTR}])" in selector:
            found = [d for d in found if not d.stamped]
        return FakeLocator(found)

    # --- things the page does, which the checks have to notice or ignore ---
    def open_dialog(self, label: str, body: str = "") -> None:
        self.dialogs.append(FakeDialog(label, body=body))

    def reveal(self, label: str) -> None:
        """Show a dialog that was in the markup all along, hidden."""
        for dialog in self.dialogs:
            if dialog.label == label:
                dialog.visible = True

    def open_tab(self) -> None:
        self.context.pages.append(_tab())


class ClickApplyTests(unittest.TestCase):
    """A real application died here: LinkedIn's card animates while its panels
    load and is then rebuilt, so Playwright's click waited on an element that
    never held still and was then detached, and timed out with the Easy Apply
    button plainly on screen."""

    EASY = [{"tag": "a", "text": "Easy Apply", "label": "", "id": 1, "elid": ""}]
    MODAL = "Apply to UbiqEdge"

    class FakeSession:
        def __init__(self):
            self.logs: list[str] = []

        def log(self, text: str) -> None:
            self.logs.append(text)

    def _patch(self, fields, click, page):
        """Stand in for the browser layer. `click` is called per attempt."""
        from src.apply import browser

        self.snapshots = 0

        def snapshot(_page):
            self.snapshots += 1
            return list(fields)

        for name, value in (("snapshot", snapshot), ("locate", lambda *a, **k: object()),
                            ("click", click)):
            self.addCleanup(setattr, browser, name, getattr(browser, name))
            setattr(browser, name, value)

    def test_a_click_that_opens_the_flow_happens_once(self) -> None:
        page = FakePage()
        calls = []

        def click(loc, timeout=0, **kw):
            calls.append(1)
            page.open_dialog(self.MODAL)

        self._patch(self.EASY, click, page)
        target, kind, opened = linkedin.click_apply(page, self.FakeSession())
        self.assertEqual(kind, "easy_apply")
        self.assertEqual(opened, "dialog")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.snapshots, 1)

    def test_a_click_that_raises_is_retried_from_a_fresh_snapshot(self) -> None:
        page = FakePage()
        calls = []

        def click(loc, timeout=0, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("element was detached from the DOM")
            page.open_dialog(self.MODAL)

        sess = self.FakeSession()
        self._patch(self.EASY, click, page)
        target, kind, opened = linkedin.click_apply(page, sess)
        self.assertEqual((kind, opened), ("easy_apply", "dialog"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.snapshots, 2)     # re-read, not waited on
        self.assertTrue(any("rendering" in line for line in sess.logs), sess.logs)

    def test_a_click_that_quietly_does_nothing_is_retried(self) -> None:
        # The regression this exists for: browser.click's fallback dispatches
        # the click on the element itself, which succeeds on a node React has
        # already thrown away. Nothing opens and nothing raises, and the old
        # code called that a success - then waited ten seconds for a dialog
        # and spent a model call being told to press the button again.
        page = FakePage()
        calls = []

        def click(loc, timeout=0, **kw):
            calls.append(1)
            if len(calls) >= 2:
                page.open_dialog(self.MODAL)

        sess = self.FakeSession()
        self._patch(self.EASY, click, page)
        target, kind, opened = linkedin.click_apply(page, sess)
        self.assertEqual((kind, opened), ("easy_apply", "dialog"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("did not open anything" in line for line in sess.logs), sess.logs)

    def test_a_click_that_landed_is_never_sent_twice(self) -> None:
        # The click worked, opened the modal, and the card then swallowed its
        # own button - so Playwright still raised. Clicking again would open a
        # second apply flow.
        page = FakePage()
        calls = []

        def click(loc, timeout=0, **kw):
            calls.append(1)
            page.open_dialog(self.MODAL)   # it landed...
            raise RuntimeError("element was detached from the DOM")   # ...then vanished

        self._patch(self.EASY, click, page)
        target, kind, opened = linkedin.click_apply(page, self.FakeSession())
        self.assertEqual((kind, opened), ("easy_apply", "dialog"))
        self.assertEqual(len(calls), 1)

    def test_an_unconfirmed_click_is_never_called_an_open_form(self) -> None:
        # Whatever these checks miss, the transcript must not claim a form
        # that is not there: that claim is what sent the model a job page and
        # had it press Easy Apply itself, the slowest way to click a button.
        page = FakePage()
        sess = self.FakeSession()
        self._patch(self.EASY, lambda loc, **kw: None, page)
        self.assertEqual(linkedin.click_apply(page, sess)[2], "")

    def test_what_counts_as_an_open_flow(self) -> None:
        # Every signal is a CHANGE from before the click, and every one is
        # narrowed to a change only a click can cause.
        page = FakePage(dialogs=["Messaging"])
        before = linkedin._baseline(page)
        self.assertEqual(linkedin._flow_opened(page, before), "")

        page.open_dialog("Apply to UbiqEdge")
        self.assertEqual(linkedin._flow_opened(page, before), "dialog")

        tabbed = FakePage()
        before = linkedin._baseline(tabbed)
        tabbed.open_tab()
        self.assertEqual(linkedin._flow_opened(tabbed, before), "new tab",
                         "apply on the company website")

        moved = FakePage()
        before = linkedin._baseline(moved)
        moved.url = "https://www.linkedin.com/jobs/openSDUIApplyFlow"
        self.assertEqual(linkedin._flow_opened(moved, before), "navigated")

    def test_a_job_page_that_already_has_a_dialog_is_not_an_open_flow(self) -> None:
        # LinkedIn job pages carry visible overlays of their own. Testing for
        # a dialog's PRESENCE called that success, so the agent announced the
        # form was open and then paid for a model call to press Easy Apply.
        page = FakePage(dialogs=["Messaging", "Cookie preferences"])
        before = linkedin._baseline(page)
        self.assertEqual(linkedin._flow_opened(page, before), "")

    def test_an_overlay_that_mounts_after_the_baseline_is_not_an_open_flow(self) -> None:
        # Counting dialogs by INCREASE was still wrong: LinkedIn mounts its
        # own overlays late, so the count rose with no click involved. A new
        # dialog now has to name itself an apply form.
        page = FakePage()
        before = linkedin._baseline(page)
        page.open_dialog("Messaging")
        self.assertEqual(linkedin._flow_opened(page, before), "")

    def test_a_modal_shipped_hidden_in_the_markup_still_counts_when_shown(self) -> None:
        # Stamping every [role=dialog] in the document, rather than only the
        # ones actually open, buried the apply modal before it was opened: the
        # usual way to build one is hidden markup revealed on click, so the
        # click could then never be confirmed and was retried until it raised.
        page = FakePage(dialogs=[])
        page.dialogs.append(FakeDialog("Easy Apply", visible=False))
        before = linkedin._baseline(page)
        self.assertEqual(linkedin._flow_opened(page, before), "")
        page.reveal("Easy Apply")
        self.assertEqual(linkedin._flow_opened(page, before), "dialog")

    def test_a_recruiter_message_mentioning_apply_is_not_an_apply_dialog(self) -> None:
        # The messaging overlay mounts late and its body is recruiters'
        # messages: "please apply through our portal and attach your resume".
        # Reading the dialog's whole text called that the apply modal. Only
        # what the dialog calls itself - its label and headings - is read.
        page = FakePage()
        before = linkedin._baseline(page)
        page.open_dialog("Messaging",
                         body="Hi Rishab, please apply on our portal and attach your resume.")
        self.assertEqual(linkedin._flow_opened(page, before), "")

    def test_linkedins_own_tracking_parameters_are_not_a_navigation(self) -> None:
        # The job card rewrites its own query string as it hydrates. Comparing
        # whole URLs read that as the apply flow navigating.
        page = FakePage(url="https://www.linkedin.com/jobs/view/1")
        before = linkedin._baseline(page)
        page.url = "https://www.linkedin.com/jobs/view/1/?refId=abc&trackingId=def"
        self.assertEqual(linkedin._flow_opened(page, before), "")

    def test_the_first_attempt_does_not_wait_for_a_card_that_is_animating(self) -> None:
        # Playwright's click waits for the element to hold still, and the job
        # card is animating exactly when we first reach it - so attempt one is
        # the one most likely to fail and the least worth waiting on. Six
        # seconds of it, plus three more of fallback, cost ten seconds before
        # the retry that actually worked.
        page = FakePage()
        seen = []

        def click(loc, timeout=0, **kw):
            seen.append((timeout, kw.get("fallback_timeout")))
            if len(seen) >= 2:
                page.open_dialog(self.MODAL)

        self._patch(self.EASY, click, page)
        linkedin.click_apply(page, self.FakeSession())
        self.assertEqual([t for t, _ in seen],
                         list(linkedin.APPLY_CLICK_TIMEOUTS[:2]))
        self.assertLess(seen[0][0], seen[1][0], "patience is spent later, not first")
        self.assertTrue(all(f == linkedin.APPLY_CLICK_FALLBACK for _, f in seen))

    def test_no_apply_button_is_not_an_error(self) -> None:
        page = FakePage()
        self._patch([], lambda loc, **kw: None, page)
        self.assertEqual(linkedin.click_apply(page, self.FakeSession()), (None, "", ""))

    def test_a_button_that_never_takes_a_click_raises(self) -> None:
        page = FakePage()

        def click(loc, timeout=0, **kw):
            raise RuntimeError("element is not stable")

        self._patch(self.EASY, click, page)
        with self.assertRaises(RuntimeError):
            linkedin.click_apply(page, self.FakeSession())
        self.assertEqual(self.snapshots, linkedin.APPLY_CLICK_TRIES)


class LoginDetectionTests(unittest.TestCase):
    def test_authwall_urls(self) -> None:
        for url in ("https://www.linkedin.com/authwall?trk=x",
                    "https://www.linkedin.com/uas/login?session_redirect=y",
                    "https://www.linkedin.com/checkpoint/lg/login"):
            self.assertTrue(linkedin.LOGIN_URL_RE.search(url), url)
        self.assertFalse(
            linkedin.LOGIN_URL_RE.search("https://in.linkedin.com/jobs/view/swe-123")
        )


class DetectTests(unittest.TestCase):
    def test_linkedin_hosts(self) -> None:
        self.assertEqual(sites.detect("https://in.linkedin.com/jobs/view/swe-at-x-123"), "linkedin")
        self.assertEqual(sites.detect("https://www.linkedin.com/jobs/view/456"), "linkedin")

    def test_everything_else_is_generic(self) -> None:
        self.assertEqual(sites.detect("https://www.capgemini.com/jobs/528304"), "")
        self.assertEqual(sites.detect("https://nvidia.wd5.myworkdayjobs.com/x"), "")
        self.assertEqual(sites.detect("https://notlinkedin.com.evil.example/x"), "")
        self.assertEqual(sites.detect(""), "")


class NotCurrentlyAcceptingTests(unittest.TestCase):
    def test_linkedins_newer_wording_is_closed(self) -> None:
        from src.apply.sites import linkedin
        from src.apply.worker import _looks_closed

        text = "Fullstack - Software Engineer II. Pune Division. Not currently accepting applications"
        self.assertTrue(linkedin.is_closed(text, "https://www.linkedin.com/jobs/view/123/"))
        self.assertTrue(_looks_closed(text))
        self.assertFalse(_looks_closed("Over 100 people clicked apply. Easy Apply"))
