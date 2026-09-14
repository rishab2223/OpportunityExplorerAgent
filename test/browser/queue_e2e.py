"""The queue, end to end, with real browsers.

The unit tests prove the queue's bookkeeping with a fake starter. This proves
the thing they cannot: that job two's Chrome actually launches after job one's
has closed. Chrome allows one instance per user-data-dir, and a submitted
application keeps the window up for CLOSE_GRACE_SECONDS, so advancing at the
wrong moment fails with "already in use". That is the bug this catches.

Everything isolated: scratch Chrome profile, scratch profile JSON, scratch
history DB. Nothing real is opened, filled, stored or submitted.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = E2E = Path(__file__).resolve().parent
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_queue_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402
from src.web import applyqueue  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.invalid",
    "phone": "+91 00000 00000",
    "location": "Bangalore, India",
}), encoding="utf-8")

# make_invoker is called at the top of every session, so the stub has to
# exist; it just must never be USED - the modal fixture needs no model call.
def _no_model(system, user, schema):
    raise AssertionError("the queue check must not need the model")


worker.make_invoker = lambda cfg, env, purpose: _no_model

# start_apply does not take headless; force it for the test's browsers.
_real_run_session = worker.run_session
worker.run_session = lambda *a, **kw: _real_run_session(*a, **{**kw, "headless": True})

MODAL = (E2E / "fixture_modal.html").as_uri()
released: list[str] = []
finished: list[tuple[str, str]] = []
opened: list[str] = []


def starter(stamp: str, job_id: str) -> str:
    opened.append(job_id)
    job = {"job_id": job_id, "company": "DummyCo", "title": f"Engineer {job_id}",
           "description": "Build things.", "apply_url": MODAL}

    def on_finish(status: str) -> None:
        finished.append((job_id, status))

    def on_released(status: str) -> None:
        released.append(job_id)
        applyqueue.release(status)

    sess = worker.start_apply(
        stamp, job, "dummy resume text", AppConfig(), load_env(),
        on_finish=on_finish, out_dir=SCRATCH, on_released=on_released,
    )
    return sess.label


def answer_machine(stop: threading.Event, answers_by_job: dict[str, list[str]]) -> None:
    """Answer whatever the live session asks, from that job's script."""
    while not stop.is_set():
        sess = apply_session.current()
        if sess is not None and sess.status == "waiting_for_user":
            script = answers_by_job.get(sess.job_id)
            if script:
                reply = script.pop(0)
                print(f"    [{sess.job_id}] ASKED: {sess.pending_question[:60]}")
                print(f"    [{sess.job_id}] REPLY: {reply}")
                sess.answer(reply)
        time.sleep(0.2)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(label + " " + detail)


# --------------------------------------------------------------- run the queue
print("== a queue of two jobs, real browsers ==")
scripts = {"job-a": ["done", "done"], "job-b": ["done", "done"]}
stop = threading.Event()
threading.Thread(target=answer_machine, args=(stop, scripts), daemon=True).start()

applyqueue.reset()
state = applyqueue.start(
    [{"stamp": "20260913T120000", "job_id": "job-a", "label": "DummyCo A"},
     {"stamp": "20260913T120000", "job_id": "job-b", "label": "DummyCo B"}],
    starter,
)
check("only one job opens at once", opened == ["job-a"], str(opened))
check("the second is queued, not started", len(state["pending"]) == 1, str(state))

deadline = time.time() + 180
while time.time() < deadline and applyqueue.state()["active"]:
    time.sleep(0.5)
time.sleep(2)
stop.set()

final = applyqueue.state()
print("\n  opened  :", opened)
print("  released:", released)
print("  finished:", finished)
print("  done    :", [(d["job_id"], d["status"]) for d in final["done"]])

check("both jobs opened, in order", opened == ["job-a", "job-b"], str(opened))
check("the second opened only after the first released",
      released[:1] == ["job-a"], str(released))
check("both reached a terminal status", len(finished) == 2, str(finished))
check("both applied", all(s == "applied" for _, s in finished), str(finished))
check("the queue emptied", not final["active"] and not final["pending"], str(final))
check("nothing was parked", final["parked"] == [], str(final["parked"]))
check("no scripted answers left over",
      all(not left for left in scripts.values()), str(scripts))

# ------------------------------------------------------- park, then abort
print("\n== park leaves one job and moves on ==")
opened.clear()
released.clear()
finished.clear()
scripts2 = {"job-c": ["done", "park"], "job-d": ["done", "done"]}
stop2 = threading.Event()
threading.Thread(target=answer_machine, args=(stop2, scripts2), daemon=True).start()

applyqueue.reset()
applyqueue.start(
    [{"stamp": "20260913T120000", "job_id": "job-c", "label": "DummyCo C"},
     {"stamp": "20260913T120000", "job_id": "job-d", "label": "DummyCo D"}],
    starter,
)
deadline = time.time() + 180
while time.time() < deadline and applyqueue.state()["active"]:
    time.sleep(0.5)
time.sleep(2)
stop2.set()

parked_state = applyqueue.state()
print("\n  opened  :", opened)
print("  finished:", finished)
print("  parked  :", [j["job_id"] for j in parked_state["parked"]])
print("  done    :", [(d["job_id"], d["status"]) for d in parked_state["done"]])

check("park did not stop the queue", opened == ["job-c", "job-d"], str(opened))
check("the parked job is recorded as parked",
      [j["job_id"] for j in parked_state["parked"]] == ["job-c"], str(parked_state["parked"]))
check("parking is not an outcome",
      [d["job_id"] for d in parked_state["done"]] == ["job-d"], str(parked_state["done"]))
check("the parked job reported parked, never applied",
      ("job-c", "parked") in finished, str(finished))

# --- abort takes the rest of the queue with it ---
print("\n== abort stops the whole queue ==")
opened.clear()
finished.clear()
scripts3 = {"job-f": ["done", "abort"], "job-g": ["done", "done"]}
stop3 = threading.Event()
threading.Thread(target=answer_machine, args=(stop3, scripts3), daemon=True).start()

applyqueue.reset()
applyqueue.start(
    [{"stamp": "20260913T120000", "job_id": "job-f", "label": "DummyCo F"},
     {"stamp": "20260913T120000", "job_id": "job-g", "label": "DummyCo G"}],
    starter,
)
deadline = time.time() + 180
while time.time() < deadline and applyqueue.state()["active"]:
    time.sleep(0.5)
time.sleep(2)
stop3.set()

aborted_state = applyqueue.state()
print("\n  opened  :", opened)
print("  finished:", finished)
print("  note    :", aborted_state["note"])
check("abort stopped the queue", opened == ["job-f"], str(opened))
check("the queued job never opened", not aborted_state["pending"], str(aborted_state))
check("the abort is explained", "still queued" in aborted_state["note"], aborted_state["note"])

print("\nQUEUE E2E PASSED")
print("scratch dir:", SCRATCH)
