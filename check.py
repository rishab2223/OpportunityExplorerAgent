"""Run the checks that your change actually touches.

    python check.py              what the working tree changed needs
    python check.py unit         one group by name
    python check.py unit ui      several
    python check.py all          everything (slow: the browser groups)
    python check.py all -j6      the same, six scripts at a time
    python check.py --list       the groups, their scripts and what they cost

-j runs the browser scripts concurrently. They are safe to overlap - each one
keeps its state in its own temp directory, none of them reads localData, and
the two that serve a page use their own fixed port. What they are NOT immune
to is the clock: a handful assert on how long something took, and a loaded
machine slows everything. settle_check failed that way twice before its
timing assumptions were taken out. So -j is opt-in, and if a check starts
failing only under -j, suspect its assertions before its subject.

The unit suite is ten seconds for all of it, so it is never worth splitting:
every run includes it. The browser groups are the expensive ones, and those
are what this picks between.

Nothing here calls a model. The apply harness swaps in a scripted stand-in,
and the unit suite has no route to one at all - so a full run costs time and
nothing else.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BROWSER = ROOT / "test" / "browser"

# A group is a name for one kind of breakage, not one folder.
GROUPS: dict[str, list[str]] = {
    "unit": [],                       # special-cased: unittest discover
    "apply": ["run_e2e.py"],          # the whole apply loop, 20 scenarios
    "sites": [                        # one replica per ATS widget that bit us
        "phenom_check.py", "material_check.py", "radix_check.py",
        "rippling_check.py", "signin_check.py", "longlist_check.py",
        "mykaarma_check.py", "keystroke_check.py", "async_check.py",
        "combo_check.py", "radio_check.py", "rdp_probe.py", "work_check.py",
        "contact_check.py", "rewipe_check.py", "optional_check.py",
        "redo_check.py", "amount_check.py", "snapshot_check.py",
        "dump_check.py", "fallback_check.py", "cf7_check.py", "foreground_check.py", "settle_check.py", "reactselect_check.py",
    ],
    "attach": ["chooser_check.py", "jobvite_upload_check.py", "taleo_check.py"],
    "linkedin": ["li_ready_e2e.py", "li_noisy_repro.py", "li_rerender_repro.py"],
    "submit": [                       # "was it sent?", every shape of it
        "sent_no_model_repro.py", "sent_dialog_repro.py",
        "submitted_at_any_prompt.py", "submitted_tail_check.py",
        "boilerplate_repro.py", "llm_at_submit_repro.py",
    ],
    "queue": ["queue_e2e.py"],
    "ui": ["ui_check.py", "queue_ui_check.py", "chat_ui_check.py",
           "pager_check.py", "readme_check.py"],
}

# The slow ones, worst first, so -j starts them before it starts anything it
# can finish quickly. Measured on a serial `check.py all`; each of these is
# slow because it waits out a real timeout or runs a whole apply session, not
# because it sleeps.
LONGEST = [
    "run_e2e.py",             # ~128s: 22 apply sessions
    "longlist_check.py",      # ~37s: server-side dropdown search
    "keystroke_check.py",     # ~35s: the typing fallback after a failed fill
    "submitted_tail_check.py",  # ~25s
    "async_check.py",         # ~21s
    "ui_check.py",            # ~20s
    "queue_e2e.py",           # ~17s
    "rdp_probe.py",           # ~13s
    "material_check.py",      # ~11s
    "phenom_check.py",        # ~11s
    "queue_ui_check.py",      # ~10s
]

# Which groups a changed file puts at risk. First match wins, so the specific
# paths come before the general ones.
TOUCHES: list[tuple[str, tuple[str, ...]]] = [
    ("src/apply/sites/linkedin.py", ("linkedin", "apply")),
    ("src/apply/cover_letter.py", ("attach", "apply")),
    ("src/apply/session.py", ("queue", "apply", "submit")),
    ("src/apply/worker.py", ("apply", "sites", "attach", "submit", "queue")),
    ("src/apply/browser.py", ("apply", "sites", "attach")),
    ("src/apply/resolver.py", ("apply", "sites")),
    ("src/apply/profile.py", ("apply", "sites")),
    ("src/answers.py", ("apply", "sites")),
    ("src/apply/", ("apply", "sites")),
    ("src/web/static/", ("ui", "queue")),
    ("src/web/", ("ui", "queue")),
    ("README.md", ("ui",)),
    ("test/browser/", ()),            # the checks themselves: run what you edited
]


def changed_files() -> list[str]:
    """Everything different from the last commit, staged or not."""
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    return [line[3:].strip().replace("\\", "/") for line in out.splitlines() if line[3:].strip()]


def groups_for(paths: list[str]) -> list[str]:
    wanted = {"unit"}                 # always: it is ten seconds
    for path in paths:
        for prefix, groups in TOUCHES:
            if path.startswith(prefix):
                wanted.update(groups)
                break
    return [g for g in GROUPS if g in wanted]


def run_unit() -> tuple[bool, float, str]:
    started = time.time()
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "test", "-q"],
                          cwd=ROOT, capture_output=True, text=True)
    # The count comes from the run, not from a number typed in here: a
    # hardcoded one goes stale the first time a test is added.
    found = re.search(r"Ran (\d+) test", done.stderr or "")
    return done.returncode == 0, time.time() - started, f"{found.group(1) if found else '?'} unit tests"


def run_script(name: str) -> tuple[bool, float, str]:
    started = time.time()
    done = subprocess.run([sys.executable, str(BROWSER / name)], cwd=str(BROWSER),
                          capture_output=True)
    text = (done.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    errs = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    last = (text[-1] if text else (errs[-1] if errs else ""))[:64]
    return done.returncode == 0, time.time() - started, last


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        for name, scripts in GROUPS.items():
            print(f"{name:9} {len(scripts) or 'the whole unittest suite'} "
                  f"{'script(s)' if scripts else ''}")
            for script in scripts:
                print(f"            {script}")
        return 0

    if args == ["all"]:
        wanted = list(GROUPS)
    elif args:
        unknown = [a for a in args if a not in GROUPS]
        if unknown:
            print(f"no such group: {unknown} - try {list(GROUPS)}")
            return 2
        wanted = [g for g in GROUPS if g in args]
    else:
        paths = changed_files()
        wanted = groups_for(paths)
        print(f"{len(paths)} changed file(s) -> {', '.join(wanted)}\n")

    jobs = 1
    for arg in sys.argv[1:]:
        if arg.startswith("-j"):
            tail = arg[2:].lstrip("=") or "0"
            jobs = int(tail) if tail.isdigit() and int(tail) > 0 else (os.cpu_count() or 4)

    failed: list[str] = []
    started = time.time()

    if "unit" in wanted:
        # Its own command, and ten seconds: no reason to fold it into the pool.
        print("== unit ==")
        ok, took, what = run_unit()
        print(f"  {'PASS' if ok else 'FAIL'}  {what}  {took:.1f}s")
        if not ok:
            failed.append("unit")

    tasks = [(g, s) for g in wanted if g != "unit" for s in GROUPS[g]]
    # Longest first, or the run ends waiting on a 92-second script that a pool
    # with nothing left to do picked up last. A hint only: getting this list
    # wrong or letting it go stale costs seconds, never correctness.
    tasks.sort(key=lambda t: LONGEST.index(t[1]) if t[1] in LONGEST else len(LONGEST))
    if jobs > 1 and len(tasks) > 1:
        # Across groups, not within them: `apply` is one 114s script, and a
        # pool that finished each group before starting the next would sit
        # behind it for most of the run.
        print(f"== {len(tasks)} scripts, {jobs} at a time ==")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            running = {pool.submit(run_script, s): (g, s) for g, s in tasks}
            for done in as_completed(running):
                group, script = running[done]
                ok, took, last = done.result()
                print(f"  {'PASS' if ok else 'FAIL'}  {script:28} {took:6.1f}s  "
                      f"[{group}] {last}")
                if not ok:
                    failed.append(script)
    else:
        for group in wanted:
            if group == "unit":
                continue
            print(f"== {group} ==")
            for script in GROUPS[group]:
                ok, took, last = run_script(script)
                print(f"  {'PASS' if ok else 'FAIL'}  {script:28} {took:6.1f}s  {last}")
                if not ok:
                    failed.append(script)

    print(f"\n{time.time() - started:.0f}s total")
    if failed:
        print("FAILED:", ", ".join(failed))
    else:
        print("ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
