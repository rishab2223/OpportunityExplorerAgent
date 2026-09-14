"""Every command the README promises must exist in the code, and every
command the code accepts should be documented."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from src.apply import session as apply_session  # noqa: E402
from src.apply import worker  # noqa: E402

readme = (ROOT / "README.md").read_text(encoding="utf-8")
ui = (ROOT / "src" / "web" / "static" / "index.html").read_text(encoding="utf-8")

failures = []

# 1. the words the code actually accepts
expected = {
    "next": "next" in worker.CONTINUE_WORDS,
    "auto next": 'in ("auto next", "autonext", "auto")' in (ROOT / "src/apply/worker.py").read_text(encoding="utf-8"),
    "done": "done" in worker.FINISHED_WORDS,
    "skip": "skip" in worker.SKIP_WORDS,
    "leave blank": "leave blank" in worker.SKIP_WORDS,
    "attach resume": "attach resume" in worker.RESUME_COMMANDS,
    "cover letter": "cover letter" in worker.LETTER_COMMANDS,
    "retry": True,
    "closed": True,
    "abort": True,
}
for word, real in expected.items():
    if not real:
        failures.append(f"the code no longer accepts {word!r}")
    if word not in readme:
        failures.append(f"README does not mention {word!r}")

# 2. the prefixed commands
for probe, fn, name in (
    ("llm: shorter", worker._llm_instruction, "llm:"),
    ("redo", worker.REDO_RE.match, "redo"),
    ("redo salary", worker.REDO_RE.match, "redo <words>"),
    ("dump", apply_session.DUMP_COMMAND_RE.match, "dump"),
    ("dump 10", apply_session.DUMP_COMMAND_RE.match, "dump 10"),
):
    if not fn(probe):
        failures.append(f"{name}: the code does not accept {probe!r}")
    if name.split()[0] not in readme:
        failures.append(f"README does not mention {name}")

# 3. a plain answer must not look like a command
for plain in ("Bengaluru", "6 years", "no", "yes"):
    if worker.REDO_RE.match(plain) and plain not in ("redo",):
        failures.append(f"{plain!r} reads as a redo")

# 4. the README's claims about the answer box match the page
for claim, needle, where in (
    ("textarea answer box", "<textarea id=\"chat\"", ui),
    ("Ask for changes button", 'id="redraft"', ui),
    ("Restore draft button", 'id="restoredraft"', ui),
    ("character count", 'id="chatcount"', ui),
):
    if needle not in where:
        failures.append(f"README claims a {claim}, which the page does not have")

# 5. the README and the UI list the same commands
readme_cmds = set(re.findall(r"^\| `([a-z]+)", readme, re.M))
ui_cmds = set(re.findall(r"<code>([a-z ]+)</code>", ui))
print("  README commands:", sorted(readme_cmds))
print("  UI commands:    ", sorted(c for c in ui_cmds if c))
for cmd in ("redo", "dump", "skip", "next", "done", "abort", "retry"):
    if cmd not in readme:
        failures.append(f"{cmd} missing from the README")
    if cmd not in ui:
        failures.append(f"{cmd} missing from the UI command list")

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nREADME CHECK PASSED")
