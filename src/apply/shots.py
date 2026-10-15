"""Pictures of parts of a form, for the model when the text falls short.

Taken only where something went wrong or cannot be read: a box that turned
its value down with a message under it, a dropdown that refused the answer,
a question with no words of its own, a box the page marks invalid after
Next, or one the model asked to look at. Never the whole screen: a crop of
four questions cost ~900 input tokens and was read correctly, where a
viewport shot missed the questions entirely (tested Oct 2026).

Every picture sent is kept under screenshots/<company>/ (gitignored) so a
session can be checked afterwards.
"""

from __future__ import annotations

import re
import struct
import time
from pathlib import Path
from typing import Any

from src.config import ROOT

SHOTS_DIR = ROOT / "screenshots"

# Room around what is pictured - the box, its label, wrapper and message
# (SURROUNDINGS_JS) - so nothing at an edge is cut. Generous on purpose:
# wider than the probe's 8-16 px.
PAD_SIDE = 64
PAD_ABOVE = 96
PAD_BELOW = 80
# Large enough for an open list beside its box; at ~w*h/750 tokens a crop
# this size is ~1,300.
MAX_WIDTH = 1100
MAX_HEIGHT = 900
# Pictures in one model call.
PER_CALL = 4


def _shot_page(page):
    """The Page to photograph: a frame's own page when handed a frame."""
    return page if hasattr(page, "screenshot") else getattr(page, "page", page)


def _viewport(page) -> tuple[float, float]:
    size = getattr(page, "viewport_size", None)
    if size:
        return float(size["width"]), float(size["height"])
    try:
        width, height = page.evaluate("() => [innerWidth, innerHeight]")
        return float(width), float(height)
    except Exception:
        return 1280.0, 900.0


def region(boxes: list[dict[str, float]], viewport: tuple[float, float]) -> dict[str, float] | None:
    """The clip around these boxes (viewport coordinates), padded and kept
    on screen. The first box is what matters: when everything together is
    too big, the clip is cut back to keep it, from the top."""
    boxes = [b for b in boxes if b and b.get("width", 0) > 0 and b.get("height", 0) > 0]
    if not boxes:
        return None
    left = min(b["x"] for b in boxes) - PAD_SIDE
    top = min(b["y"] for b in boxes) - PAD_ABOVE
    right = max(b["x"] + b["width"] for b in boxes) + PAD_SIDE
    bottom = max(b["y"] + b["height"] for b in boxes) + PAD_BELOW
    first = boxes[0]
    if right - left > MAX_WIDTH:
        # Centred on the box: its label may be on either side of it.
        left = max(left, first["x"] + first["width"] / 2 - MAX_WIDTH / 2)
        right = left + MAX_WIDTH
    if bottom - top > MAX_HEIGHT:
        top = max(top, first["y"] - PAD_ABOVE)
        bottom = top + MAX_HEIGHT
    width, height = viewport
    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right - left < 8 or bottom - top < 8:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


# What belongs in a box's picture besides the box: its labels (an inline
# label sits LEFT of the box, past any fixed margin - the first crops cut
# "Highest degree you hold?" in half), its own wrapper, the message just
# below that wrapper, and the question of the group an option sits in.
# Offsets from the box itself, so the page's frame does not matter.
SURROUNDINGS_JS = """
(el) => {
  const me = el.getBoundingClientRect();
  const rel = (n, maxW, maxH) => {
    if (!n) return null;
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height || r.width > maxW || r.height > maxH) return null;
    return {x: r.left - me.left, y: r.top - me.top, width: r.width, height: r.height};
  };
  const out = [];
  for (const l of (el.labels || [])) out.push(rel(l, 1000, 200));
  const root = el.getRootNode();
  for (const id of (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)) {
    out.push(rel((root.getElementById ? root.getElementById(id) : null) || document.getElementById(id), 1000, 200));
  }
  const CONTROLS = 'input:not([type=hidden]), select, textarea, button, [role=combobox], [role=radio], [role=checkbox]';
  let scope = null;
  for (let box = el.parentElement, i = 0; box && i < 5; box = box.parentElement, i++) {
    if (box.querySelectorAll(CONTROLS).length > 1) break;
    scope = box;
  }
  if (scope) {
    out.push(rel(scope, 1000, 400));
    const next = scope.nextElementSibling;
    if (next && !next.querySelector(CONTROLS)) out.push(rel(next, 1000, 120));
  }
  const group = el.closest('fieldset, [role=radiogroup], [role=group]');
  if (group) {
    const legend = group.querySelector('legend');
    out.push(rel(legend, 1000, 200) || rel(group, 1000, 400));
  }
  return out.filter(Boolean);
}
"""


def crop(page, locators: list[Any], below: int = 0) -> bytes | None:
    """A PNG of these elements with room around them, or None when they
    cannot be seen. The first is scrolled into view, and its label, wrapper
    and message come with it; the rest only add to the picture where they
    are on screen with it (an open list's rows). `below`: that much more
    under the first, for an open list whose rows could not be found."""
    if not locators:
        return None
    shot_page = _shot_page(page)
    try:
        locators[0].scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
    boxes = []
    for locator in locators:
        try:
            box = locator.bounding_box(timeout=1000)
        except Exception:
            box = None
        if box is None and not boxes:
            return None                  # the field itself is not on screen
        if box:
            boxes.append(box)
    first = boxes[0]
    try:
        around = locators[0].evaluate(SURROUNDINGS_JS, timeout=1000) or []
    except Exception:
        around = []
    boxes.extend({"x": first["x"] + r["x"], "y": first["y"] + r["y"],
                  "width": r["width"], "height": r["height"]} for r in around)
    if below > 0:
        # As wide as a list usually is: a search box can be a sliver of it.
        boxes.append({"x": first["x"], "y": first["y"] + first["height"],
                      "width": max(first["width"], 320.0), "height": float(below)})
    clip = region(boxes, _viewport(shot_page))
    if clip is None:
        return None
    try:
        return shot_page.screenshot(clip=clip, timeout=5000)
    except Exception:
        return None


def size(png: bytes) -> tuple[int, int]:
    """Width and height of a PNG, from its header."""
    if len(png) < 24:
        return 0, 0
    return struct.unpack(">II", png[16:24])


def folder_name(company: str) -> str:
    """screenshots/<this>: the company, safe as a folder name."""
    name = re.sub(r"[^A-Za-z0-9]+", "-", company or "").strip("-")
    return name[:60] or "unknown-company"


def save(png: bytes, company: str, label: str) -> Path | None:
    """Keep a picture under screenshots/<company>/; its path, or None."""
    try:
        folder = SHOTS_DIR / folder_name(company)
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9]+", "-", label or "").strip("-")[:50] or "field"
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{stem}.png"
        n = 2
        while path.exists():
            path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{stem}_{n}.png"
            n += 1
        path.write_bytes(png)
        return path
    except OSError:
        return None
