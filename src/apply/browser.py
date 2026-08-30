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
    const item = {
      id: i,
      tag: el.tagName.toLowerCase(),
      type: type,
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


def launch(url: str):
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
                headless=False,
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


def snapshot(page) -> list[dict[str, Any]]:
    try:
        fields = page.evaluate(SNAPSHOT_JS)
    except Exception:
        return []
    return fields[:MAX_FIELDS]


def page_text(page, limit: int = 2500) -> str:
    try:
        return (page.inner_text("body") or "")[:limit]
    except Exception:
        return ""


def locate(page, field_id: int):
    return page.locator(f'[data-oea-id="{field_id}"]').first
