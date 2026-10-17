"""The People page (#51, Oct 2026): who can help with a job.

For each job: who posted it, likely hiring managers, recruiters, and
engineers at the company who could refer the candidate. The candidate sends
every message themselves; this finds the people and keeps the notes.

  - Posted this job: read from "Meet the hiring team" on the LinkedIn
    posting, in the apply window's signed-in Chrome profile (headless, one
    page, when Find people is pressed). Free; not every posting shows one.
  - Managers and recruiters: searched on Apify by role, company and city
    (people.role_actor, 1¢ a person found). Only people whose profile names
    the company are kept; one whose headline does not, or says "ex-", is
    marked.
  - Engineers: a second Apify search (people.engineer_actor) that checks
    each profile's current company; it ignores the role asked for, so they
    are "at the company", not a title.

A search is per company and city, reused by every job there for
people.reuse_days. What each Apify search cost is logged, for two limits
set on the Settings page: above the soft limit (people search alone, this
month) searches find fewer people; once the whole Apify account reaches
pause_at, people search stops until the month resets, so what is left still
pays for job fetches. A free-plan account that runs out stops every run.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src import db, history, properties
from src.config import PeopleConfig

KINDS = ("managers", "recruiters", "engineers")
# A sent note's reminder: after this many days without a reply.
FOLLOW_UP_DAYS = 5
SOFT_KEY = "people.soft_limit_usd"
PAUSE_KEY = "people.pause_at_usd"
# The account figure is asked of Apify at most this often.
ACCOUNT_TTL_S = 60
# Apify's usage figure trails a finished search by a minute or so: searches
# this recent are added on top of it, so two quick ones cannot both slip
# under the pause line.
USAGE_LAG_S = 300


class PeopleError(Exception):
    """A search that cannot run: no token, paused, or Apify failed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- names

_COMPANY_TAIL = re.compile(
    r"[\s,]*\b(inc|incorporated|llc|l\.l\.c|ltd|limited|pvt|private|corp|corporation|co|plc|gmbh|"
    r"llp|ag|sa|bv|pte)\b\.?$", re.I)
_COMPANY_BRACKETS = re.compile(r"\s*\([^)]*\)")
# Words a company's name may carry that its people often leave out.
_GENERIC = {"technologies", "technology", "tech", "software", "solutions", "services", "systems",
            "labs", "ai", "india", "global", "group", "the", "digital", "consulting", "infotech",
            "it", "and", "&", "of", "company", "enterprises", "international", "ventures"}


def company_name(company: str) -> str:
    """The name to search by: "Deltek, Inc." is Deltek, "Acme Software
    India Pvt. Ltd." is Acme Software India."""
    name = _COMPANY_BRACKETS.sub("", str(company or "")).strip()
    while True:
        trimmed = _COMPANY_TAIL.sub("", name).strip(" ,.")
        if trimmed == name or not trimmed:
            break
        name = trimmed
    return " ".join(name.split())


def _company_core(company: str) -> str:
    """What a profile must say to be at the company: the name without the
    words people drop ("JuiceLabs AI" is "juicelabs")."""
    words = re.findall(r"[a-z0-9]+(?:[.'][a-z0-9]+)*", company_name(company).lower())
    core = [w for w in words if w not in _GENERIC]
    return " ".join(core or words)


def _names(text: str, core: str) -> bool:
    if not core or not text:
        return False
    low = text.lower()
    if re.search(r"(?<![a-z0-9])" + re.escape(core) + r"(?![a-z0-9])", low):
        return True
    # "Juice Labs" and "JuiceLabs" are one name; "Sprinto" is not "Sprint".
    squeezed = core.replace(" ", "")
    words = re.findall(r"[a-z0-9]+", low)
    return len(squeezed) >= 4 and any("".join(words[i:j]) == squeezed
                                      for i in range(len(words)) for j in range(i + 1, min(i + 5, len(words) + 1)))


def _left(text: str, core: str) -> bool:
    """"ex-Deltek", "formerly at Deltek", "previously Deltek"."""
    return bool(core and re.search(
        r"\b(ex|former|formerly|previously|past)\b[\s\-:@,]*(?:at\s+|with\s+|@\s*)?" + re.escape(core),
        (text or "").lower()))


_CITY_ALIASES = {"bangalore": "Bengaluru", "gurgaon": "Gurugram", "bombay": "Mumbai",
                 "madras": "Chennai", "calcutta": "Kolkata", "new delhi": "Delhi", "trivandrum": "Thiruvananthapuram"}
# A location that names no city: a state, a country, remote.
_NOT_A_CITY = re.compile(
    r"^(india|remote|anywhere|hybrid|on-?site|work from home|wfh|karnataka|maharashtra|telangana|"
    r"tamil nadu|kerala|haryana|uttar pradesh|west bengal|gujarat|rajasthan|andhra pradesh|"
    r"madhya pradesh|punjab|odisha|delhi ncr|ncr|united states|usa|united kingdom|uk)$", re.I)


def city_of(location: str) -> str:
    """"Bengaluru, Karnataka, India" is Bengaluru; "Greater Hyderabad Area"
    is Hyderabad; "Karnataka" or "Remote" is no city."""
    first = re.split(r"[,;/|]", str(location or ""))[0]
    first = re.sub(r"\((remote|hybrid|on-?site)\)", "", first, flags=re.I)
    first = re.sub(r"^\s*(remote|hybrid|on-?site)\s*[-–:]\s*", "", first, flags=re.I)
    first = re.sub(r"^\s*greater\s+", "", first, flags=re.I)
    first = re.sub(r"\s+(area|division|metropolitan region|region|district|urban)\s*$", "", first, flags=re.I)
    first = " ".join(first.split())
    if not first or _NOT_A_CITY.match(first):
        return ""
    return _CITY_ALIASES.get(first.lower(), first)


def search_key(company: str, city: str) -> str:
    return f"{_company_core(company)}|{city.lower()}"


def profile_key(url: str) -> str:
    """One person, however their link is written: in.linkedin.com, a
    trailing slash or a ?tracking query are the same profile."""
    found = re.search(r"linkedin\.com/in/([^/?#]+)", str(url or ""), re.I)
    return found.group(1).lower() if found else str(url or "").strip().lower()


def _profile_url(url: str) -> str:
    key = profile_key(url)
    return f"https://www.linkedin.com/in/{key}" if re.search(r"linkedin\.com/in/", str(url or ""), re.I) else str(url or "")


# ------------------------------------------------------------ the results

def _role_person(item: dict[str, Any], company: str) -> dict[str, str] | None:
    """A manager or recruiter from the role actor, or None when nothing on
    the profile names the company."""
    url = str(item.get("profileUrl") or "")
    name = " ".join(str(item.get("fullName") or "").split())
    if not url or not name:
        return None
    core = _company_core(company)
    now_text = " · ".join(str(item.get(k) or "") for k in ("headline", "jobTitle", "currentCompany"))
    snippet = str(item.get("snippet") or "")
    shown = company_name(company)
    if _left(now_text, core) or (_left(snippet, core) and not _names(now_text, core)):
        flag = f"May have left {shown}: the profile says ex- or former"
    elif _names(now_text, core):
        flag = ""
    elif _names(snippet, core):
        flag = f"The headline does not name {shown}; check they are still there"
    else:
        return None
    return {"name": name, "headline": " ".join(str(item.get("headline") or item.get("jobTitle") or "").split()),
            "profile_url": _profile_url(url), "flag": flag}


def _engineer_person(item: dict[str, Any], company: str) -> dict[str, str] | None:
    """An engineer from the engineer actor, kept only when it matched the
    profile and its current company names this one."""
    url = str(item.get("profileUrl") or "")
    name = " ".join(str(item.get("fullName") or "").split())
    if not url or not name or item.get("matched") is False:
        return None
    current = str(item.get("currentCompany") or "")
    if not _names(current, _company_core(company)):
        return None
    title = " ".join(str(item.get("currentTitle") or item.get("headline") or "").split())
    # The actor often gives the company as the title; say where they work.
    headline = title if title and title.lower() != current.lower() else f"At {current}"
    return {"name": name, "headline": headline, "profile_url": _profile_url(url), "flag": ""}


# ------------------------------------------------------------ spending

def limits(cfg: PeopleConfig) -> dict[str, float]:
    """The two limits: the Settings page's, else settings.yaml's."""
    def read(key: str, default: float) -> float:
        try:
            return float(properties.get(key) or default)
        except ValueError:
            return default
    return {"soft": read(SOFT_KEY, cfg.soft_limit_usd), "pause_at": read(PAUSE_KEY, cfg.pause_at_usd)}


def set_limits(soft: Any, pause_at: Any, account_limit: float | None = None) -> dict[str, float]:
    try:
        soft_f, pause_f = round(float(soft), 2), round(float(pause_at), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("both limits must be dollar amounts") from exc
    if soft_f < 0 or pause_f <= 0:
        raise ValueError("the limits must be above $0")
    if soft_f > pause_f:
        raise ValueError("the smaller-searches limit must not be above the pause limit")
    if account_limit and pause_f > account_limit:
        raise ValueError(f"the pause limit must not be above your Apify plan's ${account_limit:.2f} a month")
    properties.put(SOFT_KEY, f"{soft_f:.2f}")
    properties.put(PAUSE_KEY, f"{pause_f:.2f}")
    return {"soft": soft_f, "pause_at": pause_f}


_account_lock = threading.Lock()
_account_cache: dict[str, Any] = {}


def _field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)
    return {}


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _client(token: str):
    """The Apify client; tests put a fake in its place."""
    from apify_client import ApifyClient

    return ApifyClient(token.strip())


def account(token: str, fresh: bool = False) -> dict[str, Any]:
    """{used, limit, cycle_start, cycle_end} for the Apify account, asked at
    most once a minute (fresh: now). Raises PeopleError when it cannot be
    read: spending is never guessed."""
    with _account_lock:
        held = _account_cache.get(token)
        if held and not fresh and time.monotonic() - held["fetched"] < ACCOUNT_TTL_S:
            return dict(held["value"])
    if not token.strip():
        raise PeopleError("APIFY_TOKEN is not set in .env")
    try:
        data = _as_dict(_client(token).user("me").limits())
    except Exception as exc:
        raise PeopleError(f"could not read your Apify account: {exc}") from exc
    cycle = _as_dict(data.get("monthlyUsageCycle") or {})
    value = {"used": float(_as_dict(data.get("current") or {}).get("monthlyUsageUsd") or 0),
             "limit": float(_as_dict(data.get("limits") or {}).get("maxMonthlyUsageUsd") or 0),
             "cycle_start": _when(cycle.get("startAt")), "cycle_end": _when(cycle.get("endAt")),
             "read_at": _now()}
    with _account_lock:
        _account_cache[token] = {"fetched": time.monotonic(), "value": value}
    return dict(value)


def _spent_since(since: datetime) -> float:
    conn = db.connect()
    try:
        row = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM people_spend WHERE at >= ?",
                           (since.isoformat(),)).fetchone()
    finally:
        conn.close()
    return float(row[0] or 0)


def _cycle_start(acct: dict[str, Any] | None) -> datetime:
    start = (acct or {}).get("cycle_start")
    if start:
        return start
    now = _now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _sizes(cfg: PeopleConfig, mode: str) -> tuple[int, int, int]:
    return tuple(cfg.small if mode == "small" else cfg.full)  # type: ignore[return-value]


def search_cost(cfg: PeopleConfig, mode: str) -> float:
    """What a search costs, in dollars: the role actor's people, plus the
    engineer search (about 0.3¢, measured 17 Oct 2026). Each role run is
    capped on Apify's side, so this is also close to the most it can cost."""
    managers, recruiters, _ = _sizes(cfg, mode)
    return round((managers + recruiters) * cfg.usd_per_person + 0.003, 4)


def budget(cfg: PeopleConfig, token: str, fresh: bool = False) -> dict[str, Any]:
    """Where spending stands, and what a search may do now:
    mode full | small | paused | unknown (the account could not be read)."""
    lim = limits(cfg)
    out: dict[str, Any] = {**lim, "account_used": None, "account_limit": None, "resets": "",
                           "error": "", "full": list(cfg.full), "small": list(cfg.small)}
    try:
        acct = account(token, fresh=fresh)
    except PeopleError as exc:
        acct = None
        out["error"] = str(exc)
    out["people_spent"] = round(_spent_since(_cycle_start(acct)), 4)
    if acct is None:
        out["mode"] = "unknown"
        out["next_cost"] = search_cost(cfg, "full")
        return out
    used = acct["used"] + _spent_since(acct["read_at"] - timedelta(seconds=USAGE_LAG_S))
    out["account_used"] = round(used, 4)
    out["account_limit"] = acct["limit"] or None
    if acct.get("cycle_end"):
        out["resets"] = (acct["cycle_end"] + timedelta(seconds=1)).date().isoformat()
    small = out["people_spent"] >= lim["soft"]
    mode = "small" if small else "full"
    # Paused when this search could carry the account past the line.
    if used + search_cost(cfg, mode) > lim["pause_at"]:
        mode = "paused"
    out["mode"] = mode
    out["next_cost"] = search_cost(cfg, "small" if small else "full")
    return out


def _log_spend(actor: str, run_id: str, key: str, usd: float) -> None:
    conn = db.connect()
    try:
        with conn:
            conn.execute("INSERT INTO people_spend (at, actor, run_id, search_key, usd) VALUES (?, ?, ?, ?, ?)",
                         (_now().isoformat(), actor, run_id, key, float(usd)))
    finally:
        conn.close()


# ----------------------------------------------------------------- search

def _run(client, actor_id: str, run_input: dict[str, Any], cap_usd: float | None,
         timeout_minutes: int) -> tuple[list[dict[str, Any]], float, str, str]:
    """One actor run: (items, cost, run id, problem). A run still going when
    this side stops waiting is aborted, so it stops billing."""
    from src.scrape.apify_jobs import _run_field, _status

    limit = timedelta(minutes=timeout_minutes)
    kwargs: dict[str, Any] = {"run_input": run_input, "run_timeout": limit,
                              "wait_duration": limit + timedelta(seconds=30), "logger": None}
    if cap_usd is not None:
        kwargs["max_total_charge_usd"] = Decimal(f"{max(cap_usd, 0.001):.4f}")
    try:
        run = client.actor(actor_id).call(**kwargs)
    except Exception as exc:
        return [], 0.0, "", f"failed: {exc}"
    if run is None:
        return [], 0.0, "", "did not start"
    run_id = str(_run_field(run, "id", "id") or "")
    status = _status(run)
    if status in ("READY", "RUNNING"):
        try:
            client.run(run_id).abort()
        except Exception:
            pass
        status = "TIMED-OUT"
    items: list[dict[str, Any]] = []
    dataset = _run_field(run, "defaultDatasetId", "default_dataset_id")
    if dataset:
        try:
            items = [i for i in client.dataset(str(dataset)).iterate_items() if isinstance(i, dict)]
        except Exception as exc:
            return [], float(_run_field(run, "usageTotalUsd", "usage_total_usd") or 0), run_id, f"results unreadable: {exc}"
    cost = float(_run_field(run, "usageTotalUsd", "usage_total_usd") or 0)
    problem = "" if status == "SUCCEEDED" else (
        f"stopped after {timeout_minutes} min" if status.startswith("TIMED") else f"ended {status.lower() or 'oddly'}")
    return items, cost, run_id, problem


_search_lock = threading.Lock()


def _own_profile() -> str:
    try:
        from src.apply import profile
        return profile_key(profile.load_profile().get("linkedin") or "")
    except Exception:
        return ""


def stored_search(key: str) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT key, company, city, size, found_at, cost_usd, note FROM people_searches"
                           " WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        people = conn.execute("SELECT kind, rank, name, headline, profile_url, flag FROM people"
                              " WHERE search_key = ? ORDER BY kind, rank", (key,)).fetchall()
    finally:
        conn.close()
    found = dict(row)
    for kind in KINDS:
        found[kind] = [dict(p) for p in people if p["kind"] == kind]
    return found


def _fresh(search: dict[str, Any] | None, cfg: PeopleConfig) -> bool:
    when = _when(search["found_at"]) if search else None
    return bool(when and _now() - when < timedelta(days=cfg.reuse_days))


def find(job: dict[str, Any], cfg: PeopleConfig, token: str, without_city: bool = False,
         again: bool = False) -> dict[str, Any]:
    """Search the job's company (and city) for managers, recruiters and
    engineers; store and return the search. A fresh one already stored is
    returned as it is, at no cost, unless `again`."""
    company = company_name(job.get("company") or "")
    if not company:
        raise PeopleError("this job has no company name to search")
    city = "" if without_city else city_of(job.get("location") or "")
    key = search_key(company, city)
    # One search at a time: two clicks must not both pass the pause check,
    # or both pay for the same company.
    with _search_lock:
        held = stored_search(key)
        if held and not again and _fresh(held, cfg):
            return held
        state = budget(cfg, token, fresh=True)
        if state["mode"] == "unknown":
            raise PeopleError(state["error"] or "could not read your Apify spending")
        if state["mode"] == "paused":
            raise PeopleError(f"people search is paused: your Apify account is at "
                              f"${state['account_used']:.2f} of the ${state['pause_at']:.2f} you set"
                              + (f"; it resets on {state['resets']}" if state["resets"] else ""))
        client = _client(token)
        mode = state["mode"]
        n_man, n_rec, n_eng = _sizes(cfg, mode)
        # Whatever is left under the pause line is the most this search may spend.
        room = max(0.0, state["pause_at"] - state["account_used"])

        def role(title: str, n: int) -> tuple[list, float, str, str]:
            run_input: dict[str, Any] = {"jobTitle": title, "currentCompany": company,
                                         "maxResults": n, "enrich": False}
            if city:
                run_input["location"] = city
            if _in_india(job.get("location") or ""):
                run_input["country"] = "in"
            cap = min(n * cfg.usd_per_person + 0.003, room / 2)
            return _run(client, cfg.role_actor, run_input, cap, cfg.timeout_minutes)

        def engineers() -> tuple[list, float, str, str]:
            run_input = {"search": cfg.engineer_title, "currentCompanies": [company],
                         "mode": "short", "maxItems": n_eng + 3, "maxRunTimeSecs": 150}
            if city:
                run_input["locations"] = [city]
            return _run(client, cfg.engineer_actor, run_input, None, cfg.timeout_minutes)

        with ThreadPoolExecutor(max_workers=3) as pool:
            parts = {"managers": pool.submit(role, cfg.manager_title, n_man),
                     "recruiters": pool.submit(role, cfg.recruiter_title, n_rec),
                     "engineers": pool.submit(engineers)}
            results = {kind: future.result() for kind, future in parts.items()}

        total = 0.0
        problems: list[str] = []
        people: dict[str, list[dict[str, str]]] = {}
        seen = {_own_profile()} - {""}
        for kind in KINDS:
            items, cost, run_id, problem = results[kind]
            if kind != "engineers":
                # What the role actor bills is a cent a person; its own
                # figure can trail that when the run has just ended.
                cost = max(cost, len(items) * cfg.usd_per_person)
            if cost or run_id:
                _log_spend(cfg.engineer_actor if kind == "engineers" else cfg.role_actor, run_id, key, cost)
            total += cost
            if problem:
                problems.append(f"{kind[:-1]} search {problem}")
            pick = _engineer_person if kind == "engineers" else _role_person
            limit = n_eng if kind == "engineers" else (n_man if kind == "managers" else n_rec)
            kept = []
            for item in items:
                person = pick(item, company)
                if person is None or profile_key(person["profile_url"]) in seen:
                    continue
                seen.add(profile_key(person["profile_url"]))
                kept.append(person)
                if len(kept) >= limit:
                    break
            people[kind] = kept
        # A part that timed out keeps whom it had found; only a search that
        # went wrong everywhere and found nobody leaves the old list alone.
        if all(results[k][3] for k in KINDS) and not any(results[k][0] for k in KINDS):
            raise PeopleError("Apify searches failed: " + "; ".join(problems))
        note = "; ".join(problems)
        conn = db.connect()
        try:
            with conn:
                conn.execute("DELETE FROM people WHERE search_key = ?", (key,))
                conn.execute(
                    "INSERT INTO people_searches (key, company, city, size, found_at, cost_usd, note)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET company = excluded.company,"
                    " city = excluded.city, size = excluded.size, found_at = excluded.found_at,"
                    " cost_usd = excluded.cost_usd, note = excluded.note",
                    (key, company, city, mode, _now().isoformat(), round(total, 4), note))
                for kind in KINDS:
                    for rank, p in enumerate(people[kind]):
                        conn.execute(
                            "INSERT OR IGNORE INTO people (search_key, kind, rank, name, headline, profile_url, flag)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (key, kind, rank, p["name"], p["headline"], p["profile_url"], p["flag"]))
        finally:
            conn.close()
        return stored_search(key) or {}


_ABROAD = re.compile(r"\b(usa|united states|uk|united kingdom|germany|canada|australia|singapore|"
                     r"ireland|netherlands|france|uae|united arab emirates|dubai)\b", re.I)


def _in_india(location: str) -> bool:
    """A location naming no country ("Bengaluru, Karnataka") is India: the
    runs search India."""
    return not _ABROAD.search(str(location or ""))


# ------------------------------------------------- who posted the job

# "Meet the hiring team" on a signed-in LinkedIn posting: the people in the
# section under that heading, each card's first line after the name taken
# as the headline. Class names change; the heading and the /in/ links do not.
HIRING_TEAM_JS = r"""
() => {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const slug = (a) => (/\/in\/([^/?#]+)/.exec(a.getAttribute("href") || "") || [])[1] || "";
  const heads = [...document.querySelectorAll("h1,h2,h3,h4,h5,span,div,p")]
    .filter((e) => /^meet the hiring team$/i.test(clean(e.textContent)));
  if (!heads.length) return { found: false, people: [] };
  const out = [], seen = new Set();
  const noise = /^(message|follow|connect|view profile|job poster|verified|meet the hiring team|[•·]|[•·]?\s*\d(st|nd|rd|th)\+?( degree connection)?)$/i;
  for (const head of heads) {
    let box = head.parentElement;
    for (let i = 0; box && i < 6 && !box.querySelector('a[href*="/in/"]'); i++) box = box.parentElement;
    if (!box) continue;
    for (const a of box.querySelectorAll('a[href*="/in/"]')) {
      const id = slug(a);
      if (!id || seen.has(id.toLowerCase())) continue;
      let card = a;
      while (card.parentElement && card.parentElement !== box) {
        const ids = new Set([...card.parentElement.querySelectorAll('a[href*="/in/"]')].map(slug));
        if (ids.size > 1) break;
        card = card.parentElement;
      }
      const lines = String(card.innerText || "").split("\n").map(clean).filter((l) => l && !noise.test(l));
      // The photo is a link too, with no text: the name is the first link that has some.
      const named = [...card.querySelectorAll('a[href*="/in/"]')]
        .map((x) => clean(String(x.innerText || "").split("\n")[0])).find(Boolean) || lines[0] || "";
      const name = named.split(/\s[•·]\s?/)[0].trim();
      if (!name) continue;
      const headline = lines.find((l) => l !== name && !l.startsWith(name + " ")) || "";
      seen.add(id.toLowerCase());
      out.push({ name, headline, profile_url: "https://www.linkedin.com/in/" + id });
    }
  }
  return { found: true, people: out };
}
"""

_SIGNIN_URL = re.compile(r"linkedin\.com/(login|authwall|checkpoint|uas/|signup)", re.I)
_poster_lock = threading.Lock()


def linkedin_posting(job: dict[str, Any]) -> str:
    """The posting on www.linkedin.com, or '' for a job from elsewhere."""
    for key in ("listing_url", "apply_url"):
        url = str(job.get(key) or "")
        found = re.search(r"https?://[a-z]{0,3}\.?linkedin\.com(/jobs/view/[^?#\s]+)", url, re.I)
        if found:
            return "https://www.linkedin.com" + found.group(1)
    return ""


def stored_poster(job_id: str) -> dict[str, Any] | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT result, people, read_at FROM job_posters WHERE job_id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        people = json.loads(row["people"] or "[]")
    except ValueError:
        people = []
    return {"result": row["result"], "people": people, "read_at": row["read_at"]}


def _save_poster(job_id: str, result: str, people: list[dict[str, str]]) -> dict[str, Any]:
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO job_posters (job_id, result, people, read_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(job_id) DO UPDATE SET result = excluded.result, people = excluded.people,"
                " read_at = excluded.read_at",
                (job_id, result, json.dumps(people), _now().isoformat()))
    finally:
        conn.close()
    return stored_poster(job_id) or {}


def read_page_team(page, wait_s: float = 7.0) -> tuple[str, list[dict[str, str]]]:
    """(result, people) from an open posting: found | none | signin.

    The wait is short on purpose: Start apply waits only 12 s for this
    Chrome profile to be free (browser.PROFILE_BUSY_WAIT_S). The page is
    scrolled while waiting, in case the section is drawn only when seen."""
    deadline = time.monotonic() + wait_s
    while True:
        if _SIGNIN_URL.search(page.url or ""):
            return "signin", []
        try:
            got = page.evaluate(HIRING_TEAM_JS)
        except Exception:
            got = {"found": False, "people": []}
        if got.get("found") and got.get("people"):
            return "found", list(got["people"])
        if time.monotonic() >= deadline:
            return "none", []
        try:
            page.evaluate("window.scrollBy(0, 400)")
        except Exception:
            pass
        page.wait_for_timeout(500)


def read_poster(job: dict[str, Any], launch=None) -> dict[str, Any]:
    """Read who posted the job from the posting, in the apply window's
    signed-in Chrome profile, headless. Stored, and returned as
    {result, people, read_at}: found | none | not_linkedin | signin | busy | error."""
    job_id = str(job.get("job_id") or "")
    url = linkedin_posting(job)
    if not url:
        return _save_poster(job_id, "not_linkedin", [])
    from src.apply import session as apply_session

    sess = apply_session.current()
    if sess is not None and sess.status not in apply_session.TERMINAL_STATUSES:
        # Its Chrome profile is the one an apply session has open.
        return {"result": "busy", "people": [], "read_at": ""}
    if launch is None:
        from src.apply.browser import launch
    with _poster_lock:
        pw = context = None
        try:
            pw, context, page = launch(url, headless=True)
            result, people = read_page_team(page)
        except Exception as exc:
            busy = "already using" in str(exc)
            return {"result": "busy" if busy else "error", "people": [], "read_at": "",
                    "error": "" if busy else str(exc).splitlines()[0][:200]}
        finally:
            for closer in (getattr(context, "close", None), getattr(pw, "stop", None)):
                try:
                    if closer:
                        closer()
                except Exception:
                    pass
    if result == "signin":
        return {"result": "signin", "people": [], "read_at": ""}
    return _save_poster(job_id, result, people)


# ------------------------------------------------------------- messages

def messages_for(job_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """{job_id: {profile key: message row}}."""
    if not job_ids:
        return {}
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM people_messages WHERE job_id IN (%s)" % ",".join("?" for _ in job_ids),
            tuple(job_ids)).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["job_id"], {})[profile_key(r["profile_url"])] = dict(r)
    return out


def save_text(job_id: str, person: dict[str, str], which: str, text: str) -> None:
    if which not in ("note", "message"):
        raise ValueError("which must be note or message")
    url = _profile_url(person.get("profile_url") or "")
    if not url:
        raise ValueError("no profile link")
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                f"INSERT INTO people_messages (job_id, profile_url, name, kind, {which}, updated_at)"
                f" VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, profile_url) DO UPDATE SET"
                f" {which} = excluded.{which}, updated_at = excluded.updated_at",
                (job_id, url, person.get("name") or "", person.get("kind") or "", text, _now().isoformat()))
    finally:
        conn.close()


def mark_sent(job: dict[str, Any], stamp: str, person: dict[str, str]) -> dict[str, Any]:
    """The candidate sent a note to this person. Sets a reminder in
    FOLLOW_UP_DAYS days. A job nothing was done with yet becomes a referral
    being chased (the Referrals tab, and the People page's Referral tab); an
    applied job stays applied and its Applied page timeline says so."""
    job_id = str(job.get("job_id") or "")
    url = _profile_url(person.get("profile_url") or "")
    if not job_id or not url:
        raise ValueError("a job and a profile link are needed")
    now = _now()
    follow = (now + timedelta(days=FOLLOW_UP_DAYS)).isoformat()
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO people_messages (job_id, profile_url, name, kind, sent_at, follow_up_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, profile_url) DO UPDATE SET"
                " sent_at = excluded.sent_at, follow_up_at = excluded.follow_up_at,"
                " name = excluded.name, kind = excluded.kind, updated_at = excluded.updated_at",
                (job_id, url, person.get("name") or "", person.get("kind") or "", now.isoformat(), follow,
                 now.isoformat()))
    finally:
        conn.close()
    current = (history.lookup(job_id) or {}).get("status", "")
    moved = ""
    if current in ("", "skipped"):
        history.record(job, "referral_pending", stamp=stamp, note="people", contact=person.get("name") or "")
        moved = "referral"
    elif current == "applied":
        from src import tracker

        kind = "referral" if person.get("kind") == "engineers" else "outreach"
        try:
            tracker.mark_sent(job_id, kind)
        except tracker.NotApplied:
            pass   # unmarked between the page loading and this click
    return {"sent_at": now.isoformat(), "follow_up_at": follow, "moved": moved}


def unmark_sent(job_id: str, person: dict[str, str]) -> dict[str, Any]:
    """Undo Mark sent. When that was the only note sent for a job it had
    moved to Referral, the job goes back too."""
    url = _profile_url(person.get("profile_url") or "")
    conn = db.connect()
    try:
        with conn:
            conn.execute("UPDATE people_messages SET sent_at = NULL, follow_up_at = NULL, updated_at = ?"
                         " WHERE job_id = ? AND profile_url = ?", (_now().isoformat(), job_id, url))
            others = conn.execute("SELECT COUNT(*) FROM people_messages WHERE job_id = ? AND sent_at IS NOT NULL",
                                  (job_id,)).fetchone()[0]
            row = conn.execute("SELECT status, note FROM job_history WHERE job_id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    back = bool(row and row["status"] == "referral_pending" and row["note"] == "people" and not others)
    if back:
        history.forget(job_id)
    return {"moved_back": back}


# ------------------------------------------------------------- the page

BUCKETS = ("not_applied", "applied", "referral")

_TEAM_RE = re.compile(
    r"\b(?:[Jj]oin|[Jj]oining|part of|member of|work(?:ing)? (?:in|on|with))\s+(?:our|the)\s+"
    r"((?:[A-Z][\w&+./-]*\s+){0,4}?[A-Z][\w&+./-]*)\s+[Tt]eam\b")


def team_hint(description: str) -> str:
    """The team the posting names ("join our Accounts Payable team"), shown
    as a hint for picking whom to ask; '' when it names none."""
    found = _TEAM_RE.search(str(description or ""))
    return found.group(1).strip() if found else ""


def bucket(row: dict[str, Any]) -> str:
    """Which People tab a run's job belongs on, or '' for none: held back
    and untouched, skipped, closed."""
    hist = row.get("history_status") or ""
    if hist in history.REFERRAL_STATUSES:
        return "referral"
    if hist == "applied":
        return "applied"
    if hist in ("closed", "skipped"):
        return ""
    if row.get("held_back") and not row.get("decision"):
        return ""
    if row.get("decision") == "no" or row.get("status") == "skipped":
        return ""
    return "not_applied"


def run_counts() -> list[dict[str, Any]]:
    """Every run, newest first, with how many of its jobs are on each tab.
    Read from the run files and one history snapshot, not load_jobs per run."""
    from src.web import runs

    by_id, by_fp = history.snapshot()
    out = []
    for stamp in runs.list_stamps():
        try:
            rows = runs.raw_rows(stamp)
            decisions = runs.load_decisions(stamp)
        except (runs.RunNotFound, OSError, ValueError):
            continue
        counts = {b: 0 for b in BUCKETS}
        for row in rows:
            entry, _ = runs.history_entry(row, by_id, by_fp)
            decided = decisions.get(row.get("job_id") or "", {})
            view = {"history_status": entry["status"] if entry else "",
                    "held_back": row.get("held_back"), "decision": decided.get("decision", ""),
                    "status": decided.get("status", "pending")}
            b = bucket(view)
            if b:
                counts[b] += 1
        out.append({"stamp": stamp, **counts})
    return out


def _person_view(p: dict[str, Any], kind: str, sent: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any]:
    m = sent.get(profile_key(p.get("profile_url") or "")) or {}
    follow = _when(m.get("follow_up_at")) if m.get("follow_up_at") else None
    return {"name": p.get("name") or "", "headline": p.get("headline") or "", "profile_url": p.get("profile_url") or "",
            "flag": p.get("flag") or "", "kind": kind, "sent_at": m.get("sent_at") or "",
            "follow_up_at": m.get("follow_up_at") or "", "follow_up_due": bool(follow and follow <= now),
            "has_note": bool(m.get("note")), "has_message": bool(m.get("message"))}


def run_jobs(stamp: str, cfg: PeopleConfig) -> list[dict[str, Any]]:
    """One run's jobs on the three tabs, each with its people: its stored
    search (the company and city's, else the company's without a city, kind
    by kind), who posted it, and what was sent to whom."""
    from src.web import runs

    rows = [r for r in runs.load_jobs(stamp) if bucket(r)]
    ids = [str(r.get("job_id") or "") for r in rows]
    sent_by_job = messages_for(ids)
    now = _now()
    searches: dict[str, dict[str, Any] | None] = {}

    def search(key: str) -> dict[str, Any] | None:
        if key not in searches:
            searches[key] = stored_search(key)
        return searches[key]

    out = []
    for row in rows:
        job_id = str(row.get("job_id") or "")
        company = company_name(row.get("company") or "")
        city = city_of(row.get("location") or "")
        here = search(search_key(company, city)) if city else None
        anywhere = search(search_key(company, ""))
        sent = sent_by_job.get(job_id, {})
        groups: dict[str, list[dict[str, Any]]] = {}
        wide: dict[str, bool] = {}
        for kind in KINDS:
            people = (here or {}).get(kind) or []
            wide[kind] = False
            if not people and anywhere and anywhere is not here:
                people = anywhere.get(kind) or []
                wide[kind] = bool(people)
            groups[kind] = [_person_view(p, kind, sent, now) for p in people]
        main = here or anywhere
        poster = stored_poster(job_id)
        if poster:
            poster = {**poster, "people": [_person_view(p, "poster", sent, now) for p in poster.get("people") or []]}
        found_at = _when(main["found_at"]) if main else None
        out.append({
            "job_id": job_id, "stamp": stamp, "company": row.get("company") or "", "title": row.get("title") or "",
            "location": row.get("location") or "", "city": city, "source": row.get("source") or "",
            "listing_url": row.get("listing_url") or row.get("apply_url") or "",
            "linkedin_posting": bool(linkedin_posting(row)),
            "relevance": row.get("relevance"), "score_scale": row.get("score_scale") or 10,
            "posted_at": row.get("posted_at") or "", "bucket": bucket(row),
            "team": team_hint(row.get("description") or ""),
            "applied_at": row.get("history_marked_at") if row.get("history_status") == "applied" else "",
            "referral_contact": row.get("history_contact") or "",
            "searched": main is not None,
            "search": None if main is None else {
                "found_at": main["found_at"], "cost_usd": main["cost_usd"], "size": main["size"],
                "city": main["city"], "note": main.get("note") or "",
                "old": bool(found_at and now - found_at >= timedelta(days=cfg.reuse_days)),
                "reused_until": (found_at + timedelta(days=cfg.reuse_days)).date().isoformat() if found_at else "",
            },
            "searched_city": here is not None, "searched_anywhere": anywhere is not None,
            "groups": groups, "wide": wide, "poster": poster,
            "follow_ups_due": sum(1 for m in sent.values()
                                  if m.get("follow_up_at") and (_when(m["follow_up_at"]) or now) <= now),
        })
    out.sort(key=lambda j: -(runs.score_out_of_100(j)))
    return out


def follow_ups_due() -> int:
    """Notes sent with no reply recorded, past their reminder: the nav count."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT COUNT(*) FROM people_messages WHERE sent_at IS NOT NULL"
                           " AND follow_up_at IS NOT NULL AND follow_up_at <= ?", (_now().isoformat(),)).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)
