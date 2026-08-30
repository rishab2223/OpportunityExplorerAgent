# Opportunity Explorer Agent

Two separate features that share nothing but the files on disk:

1. **Discover** — scrapes Indeed and LinkedIn through Apify, scores each job against your resume (1–10), enriches only the jobs that score **above** `min_score`, and writes a self-sufficient run folder under `outputs/`: the shortlist, a tailored `.tex` per job, and a compiled `.pdf` per job. Runs headless from the CLI; nothing else is required to use the results.
2. **Assisted apply** — optional, one job at a time, started by hand from the web UI. Opens a visible Chrome, fills what it can, and stops to ask you whenever it is unsure.

Applying yourself is a first-class path: the table gives you the apply URL and the local path of the tailored PDF, so you never have to use the apply feature at all.

There is no email, Google Sheet, or Google Drive. The resume is always a local file.

## Pipeline

1. **Load resume** — local `.tex` (preferred) or PDF/DOCX/MD/TXT
2. **Scrape** — Indeed and/or LinkedIn via Apify
3. **Score** — one cheap LLM call per scraped job (plain text from the resume)
4. **Filter** — keep jobs with `relevance > min_score` (default 7, so 8–10)
5. **Enrich** — for `.tex` input, a tailored one-page LaTeX resume plus interview prep; for PDF, markdown suggestions plus interview prep
6. **Dump** — `outputs/{timestamp}/`, compiling each tailored `.tex` to a `.pdf` (also writes a run summary after a failure)

If a step fails, later steps do not run. A run summary is still written so you can see which step failed.

## Requirements

- Python 3.11+
- [OpenAI API key](https://platform.openai.com/api-keys)
- [Apify token](https://console.apify.com/settings/integrations)
- A resume as `.tex` (for tailored LaTeX dumps) or PDF, DOCX, MD, or TXT
- A LaTeX toolchain (MiKTeX or TeX Live) if you want PDFs. Without `latexmk`/`pdflatex` on `PATH` the run still succeeds and writes `.tex` only, recording the reason in `resume_pdf_error`.
- Only for assisted apply: `playwright install chromium` (skip it if you apply manually)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Fill `.env`:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | Scoring and enrichment |
| `APIFY_TOKEN` | yes | Indeed and LinkedIn scrapes |

Edit [`config/settings.yaml`](config/settings.yaml) for search terms, lookback, models, and how many jobs to fetch.

## Config

### Resume

```yaml
resume:
  local_path: "localData/RishabResume.tex"   # or .pdf
```

- Local disk only. Relative paths are resolved from the project root.
- `localData/` is gitignored. Copy your Overleaf source to e.g. `localData/RishabResume.tex`.
- **`.tex`:** scoring uses stripped text; enrich returns a full one-page `.tex` per shortlisted job. `shortlisted.json` still includes `resume_edit_suggestions` as a changelog of those edits.
- **`.pdf` (and other non-tex):** scoring and markdown resume suggestions; dump is JSON only (no `.tex` files).
- V1 is a single main `.tex` file (no `\input` graph).

### Scrape

Jobs come from Apify (no local browser):

- Indeed: [kaix/indeed-scraper](https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ)
- LinkedIn: [dataji/apify-linkdin-jobs](https://console.apify.com/actors/d1gs0RHIwEnsan7XX)

| Setting | Indeed | LinkedIn |
| --- | --- | --- |
| Search text | `scrape.keywords` → actor `keyword` (Indeed operators like `title:(...)` are allowed) | `scrape.apify.linkedin_input.keywords` (plain keywords, not the Indeed `title:(...)` string) |
| Location | `scrape.location` → actor `country` (`India` → `IN`) | `scrape.apify.linkedin_input.location` |
| Recency | `scrape.posted_within`: `24h` / `3d` / `7d` → `fromDays` | `scrape.apify.linkedin_input.datePosted` (currently `past24Hours`) |
| Cap per source | `scrape.max_detail_jobs` → `maxItems` | `scrape.max_detail_jobs` → `maxResults` |

Current defaults in `settings.yaml`: Indeed last **3 days**, LinkedIn last **24 hours**, **100** jobs per source.

`scrape.sources` can be `indeed`, `linkedin`, or both.

If LinkedIn fails but Indeed returned jobs, the run continues unless `strict_sources: true`.

### Models

```yaml
openai:
  score_model: gpt-5.6-luna    # every scraped job
  enrich_model: gpt-5.6-luna   # shortlisted jobs only
```

Use `gpt-5.6-luna` for lower cost, or `gpt-5.6-sol` for stronger structured LaTeX.

## Run

```powershell
python -m src.cli run
python -m src.cli run --config config\settings.yaml
```

After `pip install -e .` you can also run:

```powershell
opportunity-explorer run
```

Exit code `0` on success, `1` if a pipeline step failed.

### Standalone tests

Scraper tests hit Apify only (no scoring). From the project root:

```powershell
python test/test_indeed_apify.py
python test/test_linkedin_apify.py
```

They write JSON under `test/output/`.

Filename and LaTeX-strip unit tests (no API):

```powershell
python -m unittest test.test_filenames test.test_latex_plain
```

## Outputs

Each run uses a timestamp folder, e.g. `outputs/20260823T140406/`:

| Path | When | Contents |
| --- | --- | --- |
| `{stamp}/run.json` and `outputs/run.json` | always | Status, counts, error if any |
| `{stamp}/run.log` | always | Every progress line from that run |
| `{stamp}/shortlisted.json` and `outputs/shortlisted.json` | success only | Enriched matches (URLs, scores, interview prep, resume-edit changelog, `resume_tex_file`, `resume_pdf_path`) |
| `{stamp}/{company}_{job_title}.tex` | success, and resume input was `.tex` | Tailored one-page resume per shortlisted job |
| `{stamp}/{company}_{job_title}.pdf` | when a LaTeX toolchain is installed | Compiled resume, the path shown in the UI table |
| `{stamp}/applications.json` | after you skip or apply | Your per-job decision and apply status |

`outputs/run.json` / `outputs/shortlisted.json` are copies of the latest run.

PDF runs stop at JSON in that folder. TeX runs add a `.tex` (and, with LaTeX installed, a `.pdf`) per shortlisted job. Live dumps stay under `outputs/` and are gitignored.

A sample from an older JSON run is in [`examples/sample_shortlisted.json`](examples/sample_shortlisted.json).

## Web UI

```powershell
python -m src.web              # then open http://127.0.0.1:8000
opportunity-explorer-web       # same thing after pip install -e .
```

One plain page, four sections:

- **Run** — optional resume path and job cap, a Start run button, and the live log. Logs stream over server-sent events, so you watch `[score] 12/40 …` as it happens instead of guessing. One run at a time.
- **Shortlist** — pick any past run from the dropdown; the table shows company, title, relevance, location, apply and listing links, the local PDF path with a copy button, status, and per-row actions.
- **Selected job** — why_score, the resume-edit changelog, interview prep, and the tailored resume rendered in a PDF preview.
- **Apply** — the transcript and chat box for an assisted apply session.

The UI never downloads files. Copy the path from the table and open the PDF wherever you like.

## Assisted apply

Click **Start apply** on a row. This is deliberately supervised, one job at a time.

Before the first run, fill in [`localData/apply_profile.json`](localData/) (created automatically, gitignored) with your name, email, phone, notice period, CTC expectations, and work authorization. Answers you give in chat can be added to its `learned` map and reused next time.

What happens:

1. A visible Chrome opens on the apply URL, using the persistent profile at `localData/chrome-profile`. Log into the job site once yourself; the session is remembered.
2. The agent reads the visible form fields and fills the ones it can justify from your profile, learned answers, or resume, logging every field it touches.
3. It stops and asks in the chat pane for anything else: unknown questions, OTPs, captchas, consent and legal checkboxes. Answer, or handle it in the browser yourself and type `done`. Type `skip` to leave a field alone, `abort` to stop.
4. It never submits on its own. When the form is ready it asks, and only clicks Submit after you reply `apply`.

OTPs and other secrets are held in memory for the session and never written to disk. The agent will not invent visa status, salary, or legal answers. There is no captcha solving and no unattended mass-apply.

## Daily schedule

Not wired up yet, deliberately. `python -m src.cli run` is fully non-interactive and reads keys from `.env`, so any scheduler can drive it. On Windows: Task Scheduler → Create Task → Daily trigger → Start in the project folder → action:

```text
<project>\.venv\Scripts\python.exe -m src.cli run
```

Each run writes its own `outputs/{stamp}/`, so daily runs pile up as a history the UI dropdown can browse.

## Out of scope

Unattended mass-apply, captcha solving, LinkedIn login automation, Overleaf API, email, Google Sheets, Notion.
