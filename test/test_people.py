"""The People page (#51): names and cities, which search results are kept,
the search with its limits, notes sent, and the run's three tabs.

A temp database and outputs folder; Apify is a fake client, so nothing here
spends anything or reads the real account."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src import history, people, tracker
from src.config import PeopleConfig
from src.web import outreach, runs

CFG = PeopleConfig()


def job(job_id: str, company: str = "Deltek, Inc.", location: str = "Bengaluru, Karnataka, India", **extra) -> dict:
    # A title of its own: two rows with one company and title are the same
    # posting to the history.
    return {"job_id": job_id, "company": company, "title": f"Senior Software Engineer {job_id}", "source": "linkedin",
            "location": location, "listing_url": f"https://in.linkedin.com/jobs/view/senior-engineer-{job_id}",
            "relevance": 80, "score_scale": 100, "description": "Join our Accounts Payable team.", **extra}


def role_item(name: str, headline: str, slug: str, snippet: str = "", company: str = "") -> dict:
    return {"fullName": name, "headline": headline, "jobTitle": headline, "currentCompany": company,
            "profileUrl": f"https://www.linkedin.com/in/{slug}", "snippet": snippet}


def engineer_item(name: str, slug: str, company: str = "Deltek", title: str = "Deltek") -> dict:
    return {"fullName": name, "profileUrl": f"https://www.linkedin.com/in/{slug}", "currentCompany": company,
            "currentTitle": title, "matched": True}


class FakeClient:
    """Apify, as far as people.py uses it. `results` maps what was searched
    (the role actor's jobTitle, or "engineers") to its items - by what, not
    in turn: the searches run side by side. `fail` names the same keys."""

    def __init__(self, results=None, used=0.5, limit=5.0, status="SUCCEEDED", fail=(), raise_limits=False):
        self.results = dict(results or {})
        self.used, self.limit, self.status, self.fail = used, limit, status, set(fail)
        self.raise_limits = raise_limits
        self.calls: list[dict] = []
        self.aborted: list[str] = []
        self._datasets: dict[str, list] = {}

    def user(self, _):
        client = self

        class User:
            def limits(self):
                if client.raise_limits:
                    raise RuntimeError("network down")
                return {"current": {"monthlyUsageUsd": client.used},
                        "limits": {"maxMonthlyUsageUsd": client.limit},
                        "monthlyUsageCycle": {"startAt": "2026-09-23T00:00:00Z", "endAt": "2026-10-22T23:59:59.999Z"}}
        return User()

    def actor(self, actor_id):
        client = self

        class Actor:
            def call(self, **kwargs):
                n = len(client.calls)
                client.calls.append({"actor": actor_id, **kwargs})
                what = kwargs["run_input"].get("jobTitle") or "engineers"
                if what in client.fail:
                    raise RuntimeError("actor exploded")
                client._datasets[f"ds{n}"] = list(client.results.get(what) or [])
                return {"id": f"run{n}", "status": client.status, "defaultDatasetId": f"ds{n}",
                        "usageTotalUsd": 0.0001}
        return Actor()

    def dataset(self, ds):
        client = self

        class Dataset:
            def iterate_items(self):
                return iter(client._datasets.get(ds, []))
        return Dataset()

    def run(self, run_id):
        client = self

        class Run:
            def abort(self):
                client.aborted.append(run_id)
        return Run()


MANAGERS = [role_item("Asha Rao", "Engineering Manager at Deltek", "asha"),
            role_item("Ben Iyer", "Engineering Manager", "ben", snippet="At Deltek I lead the AP team"),
            role_item("Chen Li", "Engineering Manager at Oracle", "chen")]
RECRUITERS = [role_item("Dee Shah", "We Are HIRING @Deltek", "dee"),
              role_item("Ed Paul", "Talent Acquisition, ex-Deltek", "ed"),
              role_item("Asha Rao", "Engineering Manager at Deltek", "asha")]
ENGINEERS = [engineer_item("Fay K", "fay"), engineer_item("Gus M", "gus", company="Infosys"),
             engineer_item("Asha Rao", "asha"), engineer_item("Me Myself", "my-own-profile")]


class TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db, self._out = history.DB_PATH, runs.OUTPUT_DIR
        history.DB_PATH = Path(self._tmp.name) / "history.db"
        runs.OUTPUT_DIR = Path(self._tmp.name) / "outputs"
        people._account_cache.clear()
        patcher = mock.patch.object(people, "_own_profile", return_value="my-own-profile")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        history.DB_PATH, runs.OUTPUT_DIR = self._db, self._out
        people._account_cache.clear()
        self._tmp.cleanup()

    def client(self, **kwargs) -> FakeClient:
        kwargs.setdefault("results", {"Engineering Manager": MANAGERS, "Talent Acquisition": RECRUITERS,
                                      "engineers": ENGINEERS})
        fake = FakeClient(**kwargs)
        patcher = mock.patch.object(people, "_client", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class NameTests(unittest.TestCase):
    def test_company_name_drops_the_legal_tail(self) -> None:
        self.assertEqual(people.company_name("Deltek, Inc."), "Deltek")
        self.assertEqual(people.company_name("Acme Software India Pvt. Ltd."), "Acme Software India")
        self.assertEqual(people.company_name("Zeta (Directi)"), "Zeta")
        self.assertEqual(people.company_name("Co"), "Co")   # a name that is all tail stays

    def test_city_of_a_location(self) -> None:
        self.assertEqual(people.city_of("Bengaluru, Karnataka, India"), "Bengaluru")
        self.assertEqual(people.city_of("Bangalore Urban, Karnataka"), "Bengaluru")
        self.assertEqual(people.city_of("Greater Hyderabad Area"), "Hyderabad")
        self.assertEqual(people.city_of("Pune Division, Maharashtra, India"), "Pune")
        self.assertEqual(people.city_of("Gurgaon"), "Gurugram")
        self.assertEqual(people.city_of("Hybrid - Pune"), "Pune")
        for nowhere in ("Karnataka", "India", "Remote", "", None):
            self.assertEqual(people.city_of(nowhere), "")

    def test_one_profile_however_its_link_is_written(self) -> None:
        key = people.profile_key("https://www.linkedin.com/in/Asha-Rao/")
        self.assertEqual(people.profile_key("https://in.linkedin.com/in/asha-rao?trk=x"), key)
        self.assertEqual(people._profile_url("https://in.linkedin.com/in/asha-rao/"), "https://www.linkedin.com/in/asha-rao")

    def test_the_team_a_posting_names(self) -> None:
        self.assertEqual(people.team_hint("You will join our Accounts Payable team in Bengaluru."), "Accounts Payable")
        self.assertEqual(people.team_hint("Be part of the Platform Engineering team."), "Platform Engineering")
        self.assertEqual(people.team_hint("We are a great team of builders."), "")


class KeptResultTests(unittest.TestCase):
    def test_a_headline_naming_the_company_is_kept_clean(self) -> None:
        p = people._role_person(MANAGERS[0], "Deltek, Inc.")
        self.assertEqual((p["name"], p["flag"]), ("Asha Rao", ""))

    def test_only_the_snippet_naming_it_is_kept_and_marked(self) -> None:
        self.assertIn("does not name Deltek", people._role_person(MANAGERS[1], "Deltek")["flag"])

    def test_another_company_is_dropped(self) -> None:
        self.assertIsNone(people._role_person(MANAGERS[2], "Deltek"))

    def test_ex_company_is_marked_as_maybe_left(self) -> None:
        self.assertIn("May have left", people._role_person(RECRUITERS[1], "Deltek")["flag"])

    def test_a_brand_written_without_its_generic_word(self) -> None:
        item = role_item("Ira", "Backend lead @JuiceLabs", "ira")
        self.assertIsNotNone(people._role_person(item, "JuiceLabs AI"))
        item = role_item("Jo", "Engineer at Juice Labs", "jo")
        self.assertIsNotNone(people._role_person(item, "JuiceLabs AI"))

    def test_a_short_name_is_matched_as_a_word(self) -> None:
        self.assertIsNone(people._role_person(role_item("Kay", "Manager at Sprinto", "kay"), "Sprint"))

    def test_engineers_need_the_company_as_their_current_one(self) -> None:
        kept = people._engineer_person(ENGINEERS[0], "Deltek, Inc.")
        self.assertEqual(kept["headline"], "At Deltek")
        self.assertIsNone(people._engineer_person(ENGINEERS[1], "Deltek"))
        self.assertIsNone(people._engineer_person({**ENGINEERS[0], "matched": False}, "Deltek"))
        titled = people._engineer_person(engineer_item("Lee", "lee", title="Software Engineer II"), "Deltek")
        self.assertEqual(titled["headline"], "Software Engineer II")


class SearchTests(TempDb):
    def test_a_search_keeps_the_right_people_once_each(self) -> None:
        fake = self.client()
        found = people.find(job("j1"), CFG, "token")
        self.assertEqual([p["name"] for p in found["managers"]], ["Asha Rao", "Ben Iyer"])
        # Asha is a manager already; Ed may have left but is kept, marked.
        self.assertEqual([p["name"] for p in found["recruiters"]], ["Dee Shah", "Ed Paul"])
        # Gus works elsewhere, Asha is listed, and the candidate is not their own referrer.
        self.assertEqual([p["name"] for p in found["engineers"]], ["Fay K"])
        self.assertEqual((found["company"], found["city"], found["size"]), ("Deltek", "Bengaluru", "full"))
        role_calls = [c for c in fake.calls if c["actor"] == CFG.role_actor]
        self.assertEqual(sorted(c["run_input"]["jobTitle"] for c in role_calls), ["Engineering Manager", "Talent Acquisition"])
        for call in role_calls:
            self.assertEqual(call["run_input"]["maxResults"], 3)
            self.assertEqual(call["run_input"]["location"], "Bengaluru")
            self.assertEqual(call["run_input"]["country"], "in")
            self.assertLessEqual(float(call["max_total_charge_usd"]), 0.0331)
        eng = next(c for c in fake.calls if c["actor"] == CFG.engineer_actor)
        self.assertEqual(eng["run_input"]["currentCompanies"], ["Deltek"])
        self.assertEqual(eng["run_input"]["locations"], ["Bengaluru"])
        # Six people from the role actor at a cent each, logged even though
        # each run reported less, and the engineer run's own figure.
        self.assertAlmostEqual(people._spent_since(datetime(2026, 1, 1, tzinfo=timezone.utc)), 0.0601, places=4)

    def test_a_fresh_search_is_reused_by_the_next_job_there(self) -> None:
        fake = self.client()
        people.find(job("j1"), CFG, "token")
        calls = len(fake.calls)
        people.find(job("j2", company="Deltek"), CFG, "token")
        self.assertEqual(len(fake.calls), calls)
        people.find(job("j2", company="Deltek"), CFG, "token", again=True)
        self.assertEqual(len(fake.calls), calls + 3)

    def test_without_the_city(self) -> None:
        fake = self.client()
        found = people.find(job("j1"), CFG, "token", without_city=True)
        self.assertEqual(found["city"], "")
        self.assertTrue(all("location" not in c["run_input"] and "locations" not in c["run_input"] for c in fake.calls))

    def test_over_the_soft_limit_searches_are_small(self) -> None:
        self.client()
        people._log_spend("x", "r", "k", 3.10)
        found = people.find(job("j1"), CFG, "token")
        self.assertEqual(found["size"], "small")
        self.assertEqual((len(found["managers"]), len(found["recruiters"]), len(found["engineers"])), (2, 2, 1))

    def test_paused_near_the_account_line_and_nothing_is_called(self) -> None:
        fake = self.client(used=4.47)
        with self.assertRaisesRegex(people.PeopleError, "paused"):
            people.find(job("j1"), CFG, "token")
        self.assertEqual(fake.calls, [])
        state = people.budget(CFG, "token")
        self.assertEqual((state["mode"], state["resets"]), ("paused", "2026-10-23"))

    def test_spending_that_cannot_be_read_stops_the_search(self) -> None:
        fake = self.client(raise_limits=True)
        with self.assertRaisesRegex(people.PeopleError, "could not read your Apify account"):
            people.find(job("j1"), CFG, "token")
        self.assertEqual(fake.calls, [])
        with self.assertRaisesRegex(people.PeopleError, "APIFY_TOKEN"):
            people.find(job("j1"), CFG, "  ")

    def test_a_search_never_spends_past_the_pause_line(self) -> None:
        fake = self.client(used=4.40)
        with mock.patch.object(people, "limits", return_value={"soft": 9.0, "pause_at": 4.48}):
            people.find(job("j1"), CFG, "token")
        for call in fake.calls:
            if "max_total_charge_usd" in call:
                self.assertLessEqual(float(call["max_total_charge_usd"]), 0.04)

    def test_one_part_failing_keeps_the_rest_and_says_so(self) -> None:
        self.client(fail={"engineers"})
        found = people.find(job("j1"), CFG, "token")
        self.assertEqual(found["engineers"], [])
        self.assertIn("engineer search failed", found["note"])
        self.assertTrue(found["managers"])

    def test_every_part_failing_keeps_the_old_list(self) -> None:
        self.client()
        people.find(job("j1"), CFG, "token")
        self.client(fail={"Engineering Manager", "Talent Acquisition", "engineers"})
        with self.assertRaisesRegex(people.PeopleError, "failed"):
            people.find(job("j1"), CFG, "token", again=True)
        self.assertEqual(len(people.stored_search(people.search_key("Deltek", "Bengaluru"))["managers"]), 2)

    def test_a_run_still_going_is_stopped(self) -> None:
        fake = self.client(status="RUNNING")
        found = people.find(job("j1"), CFG, "token")
        self.assertEqual(len(fake.aborted), 3)
        self.assertIn("stopped after", found["note"])

    def test_a_job_with_no_company_is_not_searched(self) -> None:
        self.client()
        with self.assertRaisesRegex(people.PeopleError, "no company"):
            people.find(job("j1", company=""), CFG, "token")


class LimitTests(TempDb):
    def test_limits_are_checked_and_kept(self) -> None:
        self.assertEqual(people.limits(CFG), {"soft": 3.0, "pause_at": 4.5})
        people.set_limits("2", "4", account_limit=5)
        self.assertEqual(people.limits(CFG), {"soft": 2.0, "pause_at": 4.0})
        for soft, pause in (("4", "3"), ("x", "3"), ("1", "6"), ("1", "0")):
            with self.assertRaises(ValueError):
                people.set_limits(soft, pause, account_limit=5)


def write_run(stamp: str, shortlisted: list, held: list = (), decisions: dict | None = None) -> None:
    directory = runs.OUTPUT_DIR / stamp
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (directory / "shortlisted.json").write_text(json.dumps(shortlisted), encoding="utf-8")
    if held:
        (directory / "held_back.json").write_text(json.dumps(list(held)), encoding="utf-8")
    if decisions:
        (directory / "applications.json").write_text(json.dumps(decisions), encoding="utf-8")


STAMP = "20261017T090000"


class RunTabTests(TempDb):
    def setUp(self) -> None:
        super().setUp()
        rows = [job("wait", relevance=70), job("skip"), job("applied"), job("ref"), job("closed"),
                job("best", relevance=95, company="Planview", location="Hyderabad")]
        held = [{**job("held"), "held_back": "low score"}, {**job("moved"), "held_back": "low score"}]
        write_run(STAMP, rows, held, {"skip": {"decision": "no", "status": "skipped"},
                                      "moved": {"decision": "yes", "status": "pending"}})
        history.record(job("applied"), "applied", stamp=STAMP)
        history.record(job("ref"), "referral_pending", stamp=STAMP, contact="Asha")
        history.record(job("closed"), "closed", stamp=STAMP)

    def test_each_run_counts_its_three_tabs(self) -> None:
        write_run("20261016T090000", [job("old")])
        counts = people.run_counts()
        self.assertEqual([c["stamp"] for c in counts], [STAMP, "20261016T090000"])
        self.assertEqual({k: counts[0][k] for k in people.BUCKETS}, {"not_applied": 3, "applied": 1, "referral": 1})

    def test_the_run_jobs_with_their_people(self) -> None:
        self.client()
        people.find(job("wait"), CFG, "token")
        jobs = people.run_jobs(STAMP, CFG)
        # Best score first; "moved" (80) was held back, then moved to the shortlist.
        self.assertEqual([j["job_id"] for j in jobs if j["bucket"] == "not_applied"], ["best", "moved", "wait"])
        by_id = {j["job_id"]: j for j in jobs}
        # One Deltek search serves every Deltek job in Bengaluru.
        self.assertTrue(by_id["moved"]["searched"])
        self.assertEqual(by_id["applied"]["groups"]["managers"][0]["name"], "Asha Rao")
        self.assertFalse(by_id["best"]["searched"])
        self.assertEqual(by_id["wait"]["team"], "Accounts Payable")
        self.assertTrue(by_id["wait"]["linkedin_posting"])

    def test_a_city_search_falls_back_to_the_company_kind_by_kind(self) -> None:
        self.client(results={"Engineering Manager": MANAGERS})
        people.find(job("wait"), CFG, "token")
        jobs = {j["job_id"]: j for j in people.run_jobs(STAMP, CFG)}
        self.assertTrue(jobs["wait"]["searched_city"] and not jobs["wait"]["searched_anywhere"])
        self.client(results={"Talent Acquisition": RECRUITERS})
        people.find(job("wait"), CFG, "token", without_city=True)
        jobs = {j["job_id"]: j for j in people.run_jobs(STAMP, CFG)}
        self.assertEqual(jobs["wait"]["groups"]["managers"][0]["name"], "Asha Rao")
        self.assertFalse(jobs["wait"]["wide"]["managers"])
        self.assertEqual(jobs["wait"]["groups"]["recruiters"][0]["name"], "Dee Shah")
        self.assertTrue(jobs["wait"]["wide"]["recruiters"])

    def test_an_old_search_is_marked_old(self) -> None:
        self.client()
        people.find(job("wait"), CFG, "token")
        later = datetime.now(timezone.utc) + timedelta(days=15)
        with mock.patch.object(people, "_now", return_value=later):
            wait = next(j for j in people.run_jobs(STAMP, CFG) if j["job_id"] == "wait")
        self.assertTrue(wait["search"]["old"])


class SentTests(TempDb):
    def setUp(self) -> None:
        super().setUp()
        write_run(STAMP, [job("a"), job("b")])

    def test_a_note_sent_for_a_waiting_job_makes_it_a_referral_and_undo_puts_it_back(self) -> None:
        asha = {"name": "Asha Rao", "profile_url": "https://in.linkedin.com/in/asha/", "kind": "engineers"}
        out = people.mark_sent(job("a"), STAMP, asha)
        self.assertEqual(out["moved"], "referral")
        entry = history.lookup("a")
        self.assertEqual((entry["status"], entry["contact"]), ("referral_pending", "Asha Rao"))
        self.assertEqual(people.run_counts()[0]["referral"], 1)
        self.assertTrue(people.unmark_sent("a", asha)["moved_back"])
        self.assertIsNone(history.lookup("a"))

    def test_undo_leaves_a_referral_marked_by_hand(self) -> None:
        history.record(job("a"), "referral_pending", stamp=STAMP, note="manual", contact="Ben")
        asha = {"name": "Asha Rao", "profile_url": "https://www.linkedin.com/in/asha", "kind": "managers"}
        self.assertEqual(people.mark_sent(job("a"), STAMP, asha)["moved"], "")
        self.assertFalse(people.unmark_sent("a", asha)["moved_back"])
        self.assertEqual(history.lookup("a")["contact"], "Ben")

    def test_an_applied_job_stays_applied_and_its_timeline_says_so(self) -> None:
        history.record(job("b"), "applied", stamp=STAMP)
        people.mark_sent(job("b"), STAMP, {"name": "Dee", "profile_url": "https://www.linkedin.com/in/dee",
                                            "kind": "recruiters"})
        self.assertEqual(history.lookup("b")["status"], "applied")
        self.assertTrue(tracker.detail("b")["messages_sent"].get("outreach"))

    def test_a_reminder_is_due_after_five_days(self) -> None:
        people.mark_sent(job("a"), STAMP, {"name": "Asha", "profile_url": "https://www.linkedin.com/in/asha"})
        self.assertEqual(people.follow_ups_due(), 0)
        later = datetime.now(timezone.utc) + timedelta(days=6)
        with mock.patch.object(people, "_now", return_value=later):
            self.assertEqual(people.follow_ups_due(), 1)

    def test_drafts_are_kept_per_person(self) -> None:
        asha = {"name": "Asha", "profile_url": "https://www.linkedin.com/in/asha", "kind": "engineers"}
        people.save_text("a", asha, "note", "Hi Asha")
        people.save_text("a", asha, "message", "Longer")
        held = people.messages_for(["a"])["a"]["asha"]
        self.assertEqual((held["note"], held["message"], held["sent_at"]), ("Hi Asha", "Longer", None))
        with self.assertRaises(ValueError):
            people.save_text("a", asha, "subject", "x")


class PosterTests(TempDb):
    class Page:
        def __init__(self, url, result):
            self.url, self.result = url, result

        def evaluate(self, _js):
            return self.result

        def wait_for_timeout(self, _ms):
            pass

    def launcher(self, url="https://www.linkedin.com/jobs/view/1", result=None):
        page = self.Page(url, result or {"found": True, "people": [
            {"name": "Rita", "headline": "Hiring for backend", "profile_url": "https://www.linkedin.com/in/rita"}]})
        closed = []

        class Context:
            def close(self):
                closed.append("context")

        class Pw:
            def stop(self):
                closed.append("pw")

        def launch(_url, headless=False):
            self.assertTrue(headless)
            return Pw(), Context(), page
        launch.closed = closed
        return launch

    def test_the_poster_is_read_and_kept(self) -> None:
        launch = self.launcher()
        out = people.read_poster(job("a"), launch=launch)
        self.assertEqual((out["result"], out["people"][0]["name"]), ("found", "Rita"))
        self.assertEqual(people.stored_poster("a")["people"][0]["name"], "Rita")
        self.assertEqual(sorted(launch.closed), ["context", "pw"])

    def test_a_posting_from_elsewhere_has_none(self) -> None:
        out = people.read_poster(job("a", listing_url="https://in.indeed.com/viewjob?jk=1", apply_url=""))
        self.assertEqual(out["result"], "not_linkedin")

    def test_a_sign_in_wall_is_not_kept(self) -> None:
        out = people.read_poster(job("a"), launch=self.launcher(url="https://www.linkedin.com/authwall?x"))
        self.assertEqual(out["result"], "signin")
        self.assertIsNone(people.stored_poster("a"))

    def test_none_shown(self) -> None:
        with mock.patch.object(people.time, "monotonic", side_effect=[0, 0, 99]):
            out = people.read_poster(job("a"), launch=self.launcher(result={"found": False, "people": []}))
        self.assertEqual(out["result"], "none")

    def test_busy_while_an_apply_session_runs(self) -> None:
        from src.apply import session as apply_session

        running = mock.Mock(status="running")
        with mock.patch.object(apply_session, "current", return_value=running):
            out = people.read_poster(job("a"), launch=self.launcher())
        self.assertEqual(out["result"], "busy")

    def test_the_profile_in_use_is_busy_not_an_error(self) -> None:
        def launch(_url, headless=False):
            raise RuntimeError("a browser is already using C:/profile; close that window")
        self.assertEqual(people.read_poster(job("a"), launch=launch)["result"], "busy")

    def test_the_linkedin_posting_link(self) -> None:
        self.assertEqual(people.linkedin_posting(job("a")), "https://www.linkedin.com/jobs/view/senior-engineer-a")
        self.assertEqual(people.linkedin_posting({"apply_url": "https://careers.example.com/1"}), "")


class DraftTests(TempDb):
    def test_a_note_over_300_characters_is_asked_again_once(self) -> None:
        answers = iter([outreach.MessageTurn(draft="x" * 320, reply="long"),
                        outreach.MessageTurn(draft="Hi Asha, short.", reply="Shortened.")])
        asked: list[str] = []

        def invoke(_system, user, _schema):
            asked.append(user)
            return next(answers)
        person = {"name": "Asha", "headline": "Engineer", "profile_url": "https://www.linkedin.com/in/asha",
                  "kind": "engineers"}
        with mock.patch.object(outreach, "_candidate", return_value="CANDIDATE"):
            out = outreach.people_draft(job("a"), person, "note", invoke=invoke)
        self.assertEqual(out["text"], "Hi Asha, short.")
        self.assertEqual(len(asked), 2)
        self.assertIn("has not applied yet", asked[0])
        self.assertIn("referral request", asked[0])
        self.assertEqual(people.messages_for(["a"])["a"]["asha"]["note"], "Hi Asha, short.")

    def test_an_applied_job_and_a_follow_up_say_so(self) -> None:
        asked: list[str] = []

        def invoke(_system, user, _schema):
            asked.append(user)
            return outreach.MessageTurn(draft="Following up.", reply="ok")
        person = {"name": "Dee", "profile_url": "https://www.linkedin.com/in/dee", "kind": "recruiters"}
        with mock.patch.object(outreach, "_candidate", return_value="CANDIDATE"):
            outreach.people_draft(job("a"), person, "message", applied_at="2026-10-15T10:00:00", follow_up=True,
                                  invoke=invoke)
        self.assertIn("applied on 2026-10-15", asked[0])
        self.assertIn("have not replied", asked[0])


if __name__ == "__main__":
    unittest.main()
