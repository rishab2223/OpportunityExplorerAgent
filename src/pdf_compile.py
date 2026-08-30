from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 180
AUX_SUFFIXES = (
    ".aux",
    ".log",
    ".out",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".toc",
    ".xdv",
)
MISSING_TOOLCHAIN = (
    "no LaTeX toolchain found; install MiKTeX or TeX Live to get PDFs "
    "(the .tex file is still written)"
)


def toolchain() -> str:
    for name in ("latexmk", "pdflatex"):
        if shutil.which(name):
            return name
    return ""


def is_stale(tex_path: Path, pdf_path: Path) -> bool:
    if not pdf_path.exists():
        return True
    try:
        return pdf_path.stat().st_mtime < tex_path.stat().st_mtime
    except OSError:
        return True


def compile_tex(tex_path: Path, timeout: int = TIMEOUT_SECONDS) -> tuple[Path | None, str]:
    """Compile a .tex to a sibling .pdf. Returns (pdf_path, error_message)."""
    tex_path = Path(tex_path)
    if not tex_path.exists():
        return None, f"missing .tex file: {tex_path}"
    tool = toolchain()
    if not tool:
        return None, MISSING_TOOLCHAIN

    out_dir = tex_path.parent
    if tool == "latexmk":
        commands = [
            [
                "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-outdir={out_dir}",
                tex_path.name,
            ]
        ]
    else:
        # pdflatex needs two passes for references to settle.
        commands = [
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={out_dir}",
                tex_path.name,
            ]
        ] * 2

    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=out_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"{tool} timed out after {timeout}s"
        except OSError as exc:
            return None, f"{tool} failed to start: {exc}"
        if proc.returncode != 0:
            return None, _error_tail(tex_path, proc.stdout, proc.stderr)

    pdf_path = out_dir / f"{tex_path.stem}.pdf"
    if not pdf_path.exists():
        return None, f"{tool} reported success but produced no PDF"
    _clean_aux(tex_path)
    return pdf_path, ""


def ensure_pdf(tex_path: Path) -> tuple[Path | None, str]:
    """Return the cached PDF, compiling only when it is missing or out of date."""
    tex_path = Path(tex_path)
    pdf_path = tex_path.with_suffix(".pdf")
    if not is_stale(tex_path, pdf_path):
        return pdf_path, ""
    return compile_tex(tex_path)


def _error_tail(tex_path: Path, stdout: str, stderr: str) -> str:
    log_file = tex_path.with_suffix(".log")
    if log_file.exists():
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            errors = [ln for ln in text.splitlines() if ln.startswith("!")]
            if errors:
                return " | ".join(errors[:5])[:1500]
        except OSError:
            pass
    combined = f"{stderr}\n{stdout}".strip()
    return combined[-1500:] or "LaTeX compilation failed with no output"


def _clean_aux(tex_path: Path) -> None:
    for suffix in AUX_SUFFIXES:
        candidate = tex_path.with_suffix(suffix)
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
