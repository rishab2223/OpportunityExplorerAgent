from __future__ import annotations

import re
from typing import Any

from src.config import ROOT

CHROME_PROFILE_DIR = ROOT / "localData" / "chrome-profile"
MAX_FIELDS = 60

# Tags each interactive element with data-oea-id so the LLM can address it by number.
SNAPSHOT_JS = """
() => {
  const out = [];
  // querySelectorAll stops at shadow boundaries, and LinkedIn's Easy Apply
  // modal (the whole thing: dialog, fields, buttons) lives inside an open
  // shadow root on div#interop-outlet - so the scan saw only the page BEHIND
  // the modal and the model clicked the blocked background "Easy Apply"
  // button. deepAll walks open shadow roots too, in document order.
  // Playwright's own locators pierce them, so data-oea-id keeps working.
  const deepAll = (start, sel) => {
    const found = [];
    const walk = (node) => {
      for (const el of node.querySelectorAll('*')) {
        if (el.matches(sel)) found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(start);
    return found;
  };
  // Clear ids from earlier snapshots first: a hidden wizard step keeps its old
  // attributes, and a stale [data-oea-id] match would act on the wrong element.
  for (const el of deepAll(document, '[data-oea-id]')) {
    el.removeAttribute('data-oea-id');
  }
  // [role=combobox] / aria-haspopup catch dropdowns that are not <select> at
  // all: ALTEN's Angular Material <mat-select> (Salary Currency, Salary
  // Period) was invisible to the scan, so those boxes were never filled.
  // Not [role=listbox] itself: that is the option container (and Workday's
  // list of chosen chips), which read as a phantom dropdown field.
  const selector = 'input, textarea, select, button, [role=button], [role=checkbox], a[href],' +
    ' [role=combobox], [aria-haspopup=listbox]';
  const actionable = /apply|easy apply|continue|next|start|submit|review|sign in|log in|upload|attach|resume|\bcv\b|cover letter/i;
  // An open modal (LinkedIn Easy Apply, ATS popups) owns the page: scope the
  // scan to it. Without this the background page's dozens of buttons filled
  // the MAX_FIELDS budget and the dialog's own fields - appended at the END
  // of the DOM - were truncated away, so the form looked invisible.
  let root = document;
  let sawDialog = false;
  for (const d of deepAll(document,
      '[role=dialog], [role=alertdialog], dialog[open], [aria-modal="true"]')) {
    const ds = window.getComputedStyle(d);
    const dr = d.getBoundingClientRect();
    if (ds.display === 'none' || ds.visibility === 'hidden') continue;
    if (dr.width < 260 || dr.height < 120) continue;
    sawDialog = true;
    // Cookie-consent wrappers are role=dialog shells whose real content sits
    // in an iframe: scoping to one hid a whole SuccessFactors form. Only a
    // dialog that itself holds interactive elements may own the scan.
    if (!deepAll(d, selector).length) continue;
    root = d;  // dialogs stack in DOM order; the last visible one is on top
  }
  // A modal whose form is still loading (LinkedIn Easy Apply shows its shell
  // and close button first, the fields a moment later) must not hand the
  // scan to the page BEHIND it: the model then clicks background buttons the
  // overlay blocks. The worker re-reads while this flag is set.
  window.__oeaDialogPending = sawDialog &&
    (root === document || !deepAll(root, 'input, textarea, select').length);
  let i = 0;
  // Repeated entries (Languages 1 and 2, two jobs) carry the same label and
  // section: number same-named fields in page order so each has its own
  // identity. Without this the second entry looked "already handled".
  const dupCounts = new Map();
  const FORM_TAGS = ['INPUT', 'TEXTAREA', 'SELECT'];
  for (const el of deepAll(root, selector)) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (rect.width === 0 || rect.height === 0) continue;
    // A custom dropdown that WRAPS a real control is not the field; the
    // control inside it is (react-select puts role=combobox on its input).
    if (!FORM_TAGS.includes(el.tagName) && el.tagName !== 'BUTTON' && el.tagName !== 'A' &&
        el.querySelector('input:not([type=hidden]):not(.cdk-visually-hidden), textarea, select')) continue;
    if (el.tagName === 'A') {
      // Pages carry hundreds of links; only apply/continue-style ones matter,
      // and MAX_FIELDS would drown in the rest.
      const looksButton = el.getAttribute('role') === 'button' ||
        /\\bbtn|button\\b/i.test(el.className || '');
      if (!looksButton && !actionable.test(el.innerText || '')) continue;
    }
    i += 1;
    el.setAttribute('data-oea-id', String(i));
    let label = '';
    if (el.labels && el.labels.length) label = el.labels[0].innerText;
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label && el.id) {
      // Inside a shadow root the label lives in that root, not the document.
      const forLabel = el.getRootNode().querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (forLabel) label = forLabel.innerText;
    }
    if (!label) {
      const wrapper = el.closest('label');
      if (wrapper) label = wrapper.innerText;
    }
    if (!label) {
      // Angular Material keeps the real label in <mat-label> inside the
      // field wrapper; the input's placeholder is an EXAMPLE value, so the
      // email box was labelled "daniel@gmail.com".
      // The wrapper may be a whole row of fields (ALTEN's currency / period
      // pair): take the last label BEFORE the control, not the row's first,
      // or Salary Period is read as Salary Currency and State as Country.
      const wrap = el.closest('mat-form-field, .mat-form-field, [class*="form-field"]');
      let own = null;
      for (const cand of (wrap ? wrap.querySelectorAll('mat-label, .mat-form-field-label') : [])) {
        if (cand.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) own = cand;
        else break;
      }
      if (own) label = own.innerText || '';
    }
    if (!label && el.getAttribute('aria-labelledby')) {
      const root = el.getRootNode();
      label = el.getAttribute('aria-labelledby').split(/\\s+/).map(id => {
        const n = root.getElementById ? root.getElementById(id) : document.getElementById(id);
        return n ? (n.innerText || '') : '';
      }).join(' ').trim();
    }
    if (!label) {
      label = el.getAttribute('placeholder') || el.getAttribute('name') || el.innerText || '';
    }
    const typeable = el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' ||
      (el.tagName === 'INPUT' && !/^(checkbox|radio|file|submit|button|image|reset)$/.test(type));
    if (!label && typeable) {
      // Workday questionnaires: the question is a plain text block above
      // the box, tied to it by nothing - the nearest short text before the
      // control within its enclosing blocks names it (it was "#11"). Boxes
      // only: a bare checkbox (the SMS opt-in) would take the heading of
      // the box above it.
      let box = el.parentElement;
      for (let depth = 0; box && depth < 5 && !label; depth++, box = box.parentElement) {
        let best = '';
        for (const h of box.querySelectorAll('label, legend, p, span, div, h1, h2, h3, h4, h5, h6')) {
          if (h === el || h.contains(el)) continue;
          if (h.querySelector('input, select, textarea, button')) continue;
          if (!(h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
          const t = (h.innerText || '').trim();
          if (t.length >= 3 && t.length <= 200) best = t;
        }
        label = best;
      }
    }
    // A chip-style prompt (Workday's phone code, skills, "how did you hear")
    // keeps its choice as pills beside an EMPTY search box: the pills are
    // the value, or every pass would fill the box again.
    let value = (el.value || '').toString();
    if (!value && el.tagName === 'INPUT') {
      let n = el.parentElement;
      for (let d = 0; n && d < 5; d++, n = n.parentElement) {
        if (n.querySelectorAll('input, select, textarea').length > 1) break;   // beyond this widget
        const chips = n.querySelectorAll('[data-automation-id=selectedItem], [data-automation-id=selectedItemList] [role=option]');
        if (chips.length) {
          value = Array.from(chips).map(c => (c.innerText || '').trim()).filter(Boolean).join('; ');
          break;
        }
      }
    }
    // Stable identity across snapshots: ids are renumbered as the DOM changes,
    // so key unlabelled controls on a short ancestor path instead.
    let path = '';
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 6; depth++) {
      const tag = node.tagName.toLowerCase();
      let idx = 1;
      let sib = node;
      while ((sib = sib.previousElementSibling)) { if (sib.tagName === node.tagName) idx++; }
      path = tag + (node.id ? '#' + node.id : ':' + idx) + (path ? '>' + path : '');
      if (node.id) break;
      node = node.parentElement;
    }
    // Radios and checkboxes are often labelled just "Yes"/"No"; the question
    // lives in a legend or the surrounding block, so carry that along.
    let group = '';
    if (type === 'radio' || type === 'checkbox') {
      const fs = el.closest('fieldset');
      const legend = fs ? fs.querySelector('legend') : null;
      if (legend) group = legend.innerText;
      if (!group) {
        const block = el.closest('fieldset, div, li, p, tr, section');
        if (block) group = (block.innerText || '').split(String.fromCharCode(10))[0];
      }
    }
    // Tile buttons ("Attach", "Upload") and file inputs labelled only
    // "Attach" say nothing about WHAT they attach; the nearest heading above
    // them does ("Resume/CV", "Cover Letter") - Greenhouse renders that heading
    // as a div.label, so class names count as headings too.
    const clickable = el.tagName === 'BUTTON' || el.tagName === 'A' ||
      el.getAttribute('role') === 'button';
    if ((clickable || type === 'file') && !group) {
      let box = el.parentElement;
      for (let depth = 0; box && depth < 5 && !group; depth++, box = box.parentElement) {
        for (const h of box.querySelectorAll(
            'label, legend, h1, h2, h3, h4, h5, h6, [role=heading], strong, b, [class*="label" i]')) {
          if (h === el || h.contains(el)) continue;
          if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
            const t = (h.innerText || '').trim();
            if (t && t.length < 120) group = t;  // the last heading BEFORE the element is the nearest
          }
        }
      }
    }
    // Every field: the nearest heading above it names its section ("Work
    // Experience", "Education") - a repeating entry the model fills from the
    // resume, and where the profile's location/company must NOT be applied.
    let section = '';
    {
      let box = el.parentElement;
      for (let depth = 0; box && depth < 10 && !section; depth++, box = box.parentElement) {
        for (const h of box.querySelectorAll(
            'h1, h2, h3, h4, h5, h6, [role=heading], legend, ' +
            '[class*="heading" i], [class*="sectiontitle" i], [class*="section-title" i], ' +
            '[data-automation-id*="title" i], [data-automation-id*="heading" i]')) {
          if (h === el || h.contains(el)) continue;
          if (h.querySelector('input, select, textarea, button')) continue;  // a wrapper, not a title
          if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
            const t = (h.innerText || '').trim();
            if (t && t.length < 80) section = t;
          }
        }
      }
    }
    // Date parts labelled only "Month" / "Year" (Workday's From / To
    // widgets): carry the enclosing group's name so From and To differ.
    if (!group && /^(month|year|day|mm|yyyy|dd)$/i.test((label || '').trim())) {
      const g = el.closest('[role=group], fieldset');
      if (g) {
        const named = g.querySelector('legend, label');
        const gl = g.getAttribute('aria-label') || (named ? named.innerText : '') ||
          (g.getAttribute('aria-labelledby') && document.getElementById(g.getAttribute('aria-labelledby'))
            ? document.getElementById(g.getAttribute('aria-labelledby')).innerText : '');
        if (gl) group = gl.trim();
      }
    }
    // Workday titles entries "Languages 1", "Languages 2": the number IS the
    // position; otherwise count same-named fields under the same title.
    const numbered = section.match(/(\\d+)\\s*$/);
    const baseSection = section.replace(/\\s*\\d+\\s*$/, '').slice(0, 80);
    const dupKey = baseSection + '|' + (label || '').trim().slice(0, 200) + '|' + type;
    let ordinal;
    if (numbered) {
      ordinal = Math.max(0, parseInt(numbered[1], 10) - 1);
    } else {
      ordinal = dupCounts.get(dupKey) || 0;
      dupCounts.set(dupKey, ordinal + 1);
    }
    const item = {
      id: i,
      tag: el.tagName.toLowerCase(),
      type: type,
      role: el.getAttribute('role') || '',
      haspopup: el.getAttribute('aria-haspopup') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      path: path,
      section: section.slice(0, 80),
      ordinal: ordinal,
      group: (group || '').trim().slice(0, 160),
      label: (label || '').trim().slice(0, 200),
      name: el.getAttribute('name') || el.getAttribute('formcontrolname') || '',
      accept: (el.getAttribute('accept') || '').slice(0, 120),
      elid: el.id || '',  // Greenhouse names its file inputs by id ("resume", "cover_letter")
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      value: value.slice(0, 200),
      text: (el.innerText || '').trim().slice(0, 80),
    };
    if (el.tagName.toLowerCase() === 'select') {
      item.options = Array.from(el.options).map(o => o.label || o.value).slice(0, 40);
      item.multiple = el.multiple === true;
    }
    if (type === 'checkbox' || type === 'radio') item.checked = el.checked === true;
    out.push(item);
  }
  return out;
}
"""


class BrowserUnavailable(Exception):
    pass


def launch(url: str, headless: bool = False):
    """Open a visible Chrome on `url` using a persistent profile the user owns."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable(
            "playwright is not installed; run: pip install playwright"
        ) from exc

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    context = None
    errors = []
    for channel in ("chrome", None):
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                headless=headless,
                channel=channel,
                accept_downloads=True,
                # Form-filling needs no GPU. Career pages shipping WebGL/three.js
                # scenes (autter.dev) plus a wake-from-sleep triggered an NVIDIA
                # TDR reset on a 4GB card; software rendering makes this window
                # contribute zero GPU load. Autoplay off skips video decode too.
                args=[
                    "--start-maximized",
                    "--disable-gpu",
                    "--autoplay-policy=user-gesture-required",
                ],
                no_viewport=True,
                # Ctrl+C in the server console belongs to the server. With the
                # defaults, Playwright's driver grabs it too: the browser dies
                # mid-application and the interrupt often never stops uvicorn.
                handle_sigint=False,
                handle_sigterm=False,
                handle_sighup=False,
            )
            break
        except Exception as exc:
            message = str(exc)
            if "existing browser session" in message or "already in use" in message:
                pw.stop()
                raise BrowserUnavailable(
                    f"a browser is already using {CHROME_PROFILE_DIR}; "
                    "close that window and start apply again"
                ) from exc
            errors.append(f"{channel or 'chromium'}: {message.splitlines()[0]}")
    if context is None:
        pw.stop()
        raise BrowserUnavailable(
            "could not launch a visible browser (" + " | ".join(errors) + "); "
            "if chromium is missing run: playwright install chromium"
        )

    page = context.pages[0] if context.pages else context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    return pw, context, page


def current_page(context, previous):
    """The tab the agent should be reading: prefer the visible one, else the
    newest open tab, else the previous page if it still exists.

    Apply links routinely open the real form in a new tab; a fixed page handle
    would keep the agent staring at the job description forever.
    """
    pages = [p for p in getattr(context, "pages", []) if not p.is_closed()]
    if not pages:
        return previous
    for page in reversed(pages):
        try:
            if page.evaluate("document.visibilityState") == "visible":
                return page
        except Exception:
            continue
    if previous is not None and not previous.is_closed():
        return previous
    return pages[-1]


def close(pw, context) -> None:
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass


_last_snapshot_error = ""

# Visible form controls in a document - the measure of which frame holds
# the application form.
CONTROL_COUNT_JS = """
() => Array.from(document.querySelectorAll('input:not([type=hidden]), textarea, select'))
  .filter(e => e.getClientRects().length).length
"""
# Anything worth acting on: a wizard's last step ("Review your application")
# has buttons and no fields, and the form's frame must not be dropped there.
INTERACTIVE_COUNT_JS = """
() => Array.from(document.querySelectorAll(
  'input:not([type=hidden]), textarea, select, button, [role=button]'))
  .filter(e => e.getClientRects().length).length
"""
_TARGET_CACHE: dict[int, Any] = {}
# The page keeps the scan unless it has no form of its own and a child frame
# clearly holds one. Both guards matter: a cookie-consent iframe's six
# checkboxes (and even a 1x1 tracking iframe) otherwise stole the whole scan
# from a form sitting in the page.
PAGE_HAS_FORM = 3          # controls in the top document = the form is there
FRAME_MARGIN = 3           # a frame must carry this many more to take over
FRAME_MIN_WIDTH = 320      # ... and be a real panel, not a banner or a pixel
FRAME_MIN_VIEWPORT_SHARE = 0.2
# Frames that are never the application form, however many controls they show.
NOT_A_FORM_RE = re.compile(
    r"recaptcha|hcaptcha|captcha|onetrust|cookielaw|cookiebot|consent|trustarc"
    r"|doubleclick|googletagmanager|google-analytics|facebook\.com/(tr|plugins)"
    r"|hotjar|intercom|drift|zendesk|livechat|youtube\.com/embed|player\.vimeo",
    re.IGNORECASE,
)


def _frame_is_a_panel(page, frame) -> bool:
    """Is this frame a large, visible region of the page? The ALTEN form fills
    its page; consent banners and tracking pixels do not."""
    try:
        element = frame.frame_element()
        box = element.bounding_box()
    except Exception:
        return False
    if not box or box["width"] < FRAME_MIN_WIDTH or box["height"] < 200:
        return False
    size = page.viewport_size or {"width": 1280, "height": 800}
    area = max(1, int(size.get("width", 1280)) * int(size.get("height", 800)))
    return (box["width"] * box["height"]) / area >= FRAME_MIN_VIEWPORT_SHARE


def target(page, refresh: bool = False):
    """The document to read and drive: the page itself, unless it holds no
    form and a big child frame does - ALTEN's talentrecruit career page
    embeds the whole application form as an iframe from another origin, so a
    scan of the top document saw no fields at all.

    The choice is remembered and only recomputed when a snapshot is taken
    (refresh=True), so every lookup addresses the same document the snapshot
    marked with data-oea-id."""
    try:
        frames = list(page.frames)
    except Exception:
        return page
    if len(frames) <= 1:
        _TARGET_CACHE.pop(id(page), None)
        return page
    if not refresh:
        chosen = _TARGET_CACHE.get(id(page))
        if chosen is not None:
            try:
                if chosen is page or not chosen.is_detached():
                    return chosen
            except Exception:
                pass
    def controls(doc) -> int:
        try:
            return int(doc.evaluate(CONTROL_COUNT_JS) or 0)
        except Exception:
            return 0

    def usable(frame) -> bool:
        """Still the form's frame: attached, big, and showing something to
        act on (the review step has only buttons)."""
        try:
            if frame is page or frame.is_detached() or NOT_A_FORM_RE.search(frame.url or ""):
                return False
            if int(frame.evaluate(INTERACTIVE_COUNT_JS) or 0) < 1:
                return False
        except Exception:
            return False
        return _frame_is_a_panel(page, frame)

    previous = _TARGET_CACHE.get(id(page))
    best, main_count = page, controls(page)
    if main_count < PAGE_HAS_FORM:
        # An empty shell hands the form to any frame that has one; a page with
        # a control or two of its own (a header search box) only to a frame
        # that clearly carries the form.
        needed = 1 if main_count == 0 else main_count + FRAME_MARGIN
        best_count = 0
        for frame in frames:
            if frame == page.main_frame or NOT_A_FORM_RE.search(frame.url or ""):
                continue
            count = controls(frame)
            if count < needed or not _frame_is_a_panel(page, frame):
                continue
            # The frame already in use wins ties, so a wizard step with one
            # field does not hand the scan to some other frame mid-form.
            if count > best_count or (frame is previous and count == best_count):
                best, best_count = frame, count
        if best is page and main_count == 0 and previous is not None and usable(previous):
            best = previous   # a step with no fields is still that form's frame
    _TARGET_CACHE[id(page)] = best
    return best


def snapshot(page) -> list[dict[str, Any]]:
    global _last_snapshot_error
    try:
        fields = target(page, refresh=True).evaluate(SNAPSHOT_JS)
        _last_snapshot_error = ""
    except Exception as exc:
        _last_snapshot_error = str(exc).splitlines()[0][:300]
        return []
    return fields[:MAX_FIELDS]


def last_snapshot_error() -> str:
    return _last_snapshot_error


def dialog_pending(page) -> bool:
    """True when the last snapshot saw an open modal that has no form
    controls yet (still loading), so its fields are not in the snapshot."""
    try:
        return bool(target(page).evaluate("() => !!window.__oeaDialogPending"))
    except Exception:
        return False


PAGE_TEXT_JS = """
() => {
  // body.innerText skips shadow roots, and LinkedIn renders its Easy Apply
  // modal AND its "Your application was sent" confirmation inside one - the
  // submitted-page check never saw it. Append each open shadow root's text.
  const parts = [document.body ? document.body.innerText || '' : ''];
  const walk = (node) => {
    for (const el of node.querySelectorAll('*')) {
      if (!el.shadowRoot) continue;
      for (const child of el.shadowRoot.children) parts.push(child.innerText || '');
      walk(el.shadowRoot);
    }
  };
  walk(document);
  return parts.join('\\n');
}
"""


# Just enough of the page to tell that something happened, without paying for
# a full snapshot: the same control selector, the same shadow-DOM walk, but
# only a count and a rough size. settle() runs this many times per click, so
# building field dicts here cost more than the sleep it replaced.
SHAPE_JS = """
() => {
  const deepAll = (start, sel) => {
    const found = [];
    const walk = (node) => {
      for (const el of node.querySelectorAll('*')) {
        if (el.matches(sel)) found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(start);
    return found;
  };
  const selector = 'input, textarea, select, button, [role=button], [role=checkbox], a[href],' +
    ' [role=combobox], [aria-haspopup=listbox]';
  const controls = deepAll(document, selector);
  let filled = 0;
  for (const el of controls) {
    if (el.value) filled += 1;
    if (el.checked) filled += 1;
  }
  return controls.length + ':' + filled + ':' + (document.body ? document.body.innerText.length : 0);
}
"""


def page_shape(page) -> str:
    """A cheap fingerprint of what the page is showing: its url, how many
    controls it has, how many of them hold something, and how much text is on
    it. Enough to tell whether a click did anything."""
    try:
        return f"{page.url}|{target(page).evaluate(SHAPE_JS)}"
    except Exception:
        return ""


def settle(page, before: str, timeout: int, step: int = 100, quiet: int = 2) -> bool:
    """Wait until the page has changed from `before` AND then held still for
    `quiet` polls, or until `timeout` ms have passed. True when it changed.

    Replaces a fixed sleep, which spent its whole budget whether the page was
    ready in a tenth of the time or never became ready at all. The quiet
    polls matter: a framework often renders a new entry and then re-renders
    it, and returning on the first sign of change would read a half-built
    page.
    """
    waited = 0
    still = 0
    shape = before
    while waited < timeout:
        pause = min(step, timeout - waited)
        page.wait_for_timeout(pause)
        waited += pause
        now = page_shape(page)
        still = still + 1 if now == shape else 0
        shape = now
        if now != before and still >= quiet:
            return True
    return shape != before


def page_text(page, limit: int = 2500) -> str:
    """The visible text: the form's frame first (its "application submitted"
    must fit in the limit), then the page around it."""
    parts: list[str] = []
    scope = target(page)
    for doc in ([scope, page] if scope is not page else [page]):
        try:
            parts.append(doc.evaluate(PAGE_TEXT_JS) or "")
        except Exception:
            continue
    text = "\n".join(p for p in parts if p)
    if text:
        return text[:limit]
    try:
        return (page.inner_text("body") or "")[:limit]
    except Exception:
        return ""


ALERTS_JS = """
() => {
  // Workday's "Errors Found" banner and its inline "Error: The field ... is
  // required" carry no alert role - only class/automation-id names with
  // "error" in them - so those count too, limited to short visible text.
  const sel = '[role=alert], [aria-live="assertive"], [aria-invalid="true"], ' +
    '[data-automation-id*="error" i], [class*="error" i], [id*="error" i]';
  const seen = new Set(), out = [];
  const walk = (node) => {
    for (const el of node.querySelectorAll('*')) {
      if (el.matches(sel) && el.getClientRects().length) {
        let t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (!t && el.labels && el.labels[0]) t = 'invalid: ' + el.labels[0].innerText.trim();
        if (!t || t.length > 240) continue;
        // keep the innermost message, not every wrapper that repeats it
        if ([...seen].some(s => s.includes(t) || t.includes(s))) continue;
        seen.add(t); out.push(t.slice(0, 160));
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return out.slice(0, 8).join(' | ');
}
"""


def alerts(page) -> str:
    """Visible validation messages on the page ("Error: Country is required"),
    for the model and the user when a Next click goes nowhere."""
    try:
        return target(page).evaluate(ALERTS_JS) or ""
    except Exception:
        return ""


def click(locator, timeout: int = 10000) -> str:
    """Click, and when the real click cannot land (an overlay or a stale
    popup intercepts pointer events - one dentsu Workday page blocked Accept
    Cookies, Prefix, the phone code and the skills box alike), dispatch the
    click on the element itself. Returns 'clicked' or 'clicked (direct)'."""
    try:
        locator.click(timeout=timeout)
        return "clicked"
    except Exception as exc:
        message = str(exc)
        blocked = "intercepts pointer events" in message or "Timeout" in message
        if not blocked:
            raise
        try:
            locator.evaluate("el => el.click()", timeout=3000)
        except Exception:
            raise exc
        return "clicked (direct)"


def locate(page, field_id: int, elid: str = ""):
    """The field's element. Workday re-renders a widget's input when its
    list opens or closes (the search box is a fresh node), which drops the
    snapshot's marker - the element's own id, when it has one, finds the
    replacement."""
    selector = f'[data-oea-id="{field_id}"]'
    if elid:
        selector += f', [id="{elid.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"]'
    return target(page).locator(selector).first
