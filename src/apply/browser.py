from __future__ import annotations

from typing import Any

from src.config import ROOT

CHROME_PROFILE_DIR = ROOT / "localData" / "chrome-profile"
MAX_FIELDS = 60

# Tags each interactive element with data-oea-id so the LLM can address it by number.
SNAPSHOT_JS = """
() => {
  const out = [];
  const selector = 'input, textarea, select, button, [role=button], [role=checkbox]';
  let i = 0;
  for (const el of document.querySelectorAll(selector)) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (rect.width === 0 || rect.height === 0) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    i += 1;
    el.setAttribute('data-oea-id', String(i));
    let label = '';
    if (el.labels && el.labels.length) label = el.labels[0].innerText;
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label && el.id) {
      const forLabel = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (forLabel) label = forLabel.innerText;
    }
    if (!label) {
      const wrapper = el.closest('label');
      if (wrapper) label = wrapper.innerText;
    }
    if (!label) {
      label = el.getAttribute('placeholder') || el.getAttribute('name') || el.innerText || '';
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
    const item = {
      id: i,
      tag: el.tagName.toLowerCase(),
      type: type,
      role: el.getAttribute('role') || '',
      path: path,
      group: (group || '').trim().slice(0, 160),
      label: (label || '').trim().slice(0, 200),
      name: el.getAttribute('name') || '',
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      value: (el.value || '').toString().slice(0, 200),
      text: (el.innerText || '').trim().slice(0, 80),
    };
    if (el.tagName.toLowerCase() === 'select') {
      item.options = Array.from(el.options).map(o => o.label || o.value).slice(0, 40);
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
                args=["--start-maximized"],
                no_viewport=True,
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


def snapshot(page) -> list[dict[str, Any]]:
    global _last_snapshot_error
    try:
        fields = page.evaluate(SNAPSHOT_JS)
        _last_snapshot_error = ""
    except Exception as exc:
        _last_snapshot_error = str(exc).splitlines()[0][:300]
        return []
    return fields[:MAX_FIELDS]


def last_snapshot_error() -> str:
    return _last_snapshot_error


def page_text(page, limit: int = 2500) -> str:
    try:
        return (page.inner_text("body") or "")[:limit]
    except Exception:
        return ""


def locate(page, field_id: int):
    return page.locator(f'[data-oea-id="{field_id}"]').first
