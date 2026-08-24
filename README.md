# Opportunity Explorer Agent

Scrapes Indeed and LinkedIn through Apify, scores each job against your resume (1–10), enriches only the jobs that score **above** `min_score`, and writes JSON under `outputs/`.

There is no email and no Google Sheet. Google Drive is optional and only used if you load the resume from Drive.

## Pipeline

1. **Load resume** — local file, or Google Drive if enabled
2. **Scrape** — Indeed and/or LinkedIn via Apify
3. **Score** — one cheap LLM call per scraped job
4. **Filter** — keep jobs with `relevance > min_score` (default 7, so 8–10)
5. **Enrich** — resume edit suggestions + interview prep, only for those matches
6. **Dump** — write JSON under `outputs/` (also runs after a failure)

If a step fails, later steps do not run. A run summary is still written so you can see which step failed.

## Requirements

- Python 3.11+
- [OpenAI API key](https://platform.openai.com/api-keys)
- [Apify token](https://console.apify.com/settings/integrations)
- A resume as PDF, DOCX, MD, or TXT

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
| `GOOGLE_APPLICATION_CREDENTIALS` | only for Drive resume | Path to a service-account JSON |

Edit [`config/settings.yaml`](config/settings.yaml) for search terms, lookback, models, and how many jobs to fetch.

## Config

### Resume

```yaml
resume:
  primary: local            # local | google_drive
  use_google_drive: false
  drive_file_id: ""         # file id or share URL; ignored unless Drive is on
  local_path: "localData/RishabResume.pdf"
```

- `primary` is tried first.
- Fallback to the other source only if that source is actually configured (`use_google_drive` + `drive_file_id`, or a non-empty `local_path`).
- Relative `local_path` values are resolved from the project root.

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

Use `gpt-5.6-luna` for lower cost, or `gpt-5.6-sol` for a stronger model.

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

### Standalone scraper tests

These hit Apify only (no scoring). From the project root:

```powershell
python test/test_indeed_apify.py
python test/test_linkedin_apify.py
```

They write JSON under `test/output/`.

## Outputs

Every run writes files under `outputs/`:

| File | When | Contents |
| --- | --- | --- |
| `{timestamp}_run.json` and `run.json` | always | Status, counts, error if any |
| `{timestamp}_shortlisted.json` and `shortlisted.json` | success only | Enriched matches |

`run.json` / `shortlisted.json` are copies of the latest run.

Each shortlisted row includes title, company, location, apply/listing URLs, score, why it matched, recruiter fields when the actor provided them (never invented), salary when present, resume-edit suggestions, and interview prep.

A sample from a real run is in [`examples/sample_shortlisted.json`](examples/sample_shortlisted.json). Live dumps stay local under `outputs/` and are gitignored.

## Optional: resume from Google Drive

1. Create a Google Cloud service account and download its JSON.
2. Enable the **Google Drive API**.
3. Share the resume file with the service account email.
4. Set `GOOGLE_APPLICATION_CREDENTIALS` in `.env`.
5. In `settings.yaml`: `primary: google_drive`, `use_google_drive: true`, and `drive_file_id`.

## Daily schedule (Windows)

Task Scheduler → Create Task → Daily trigger → Start in the project folder → action:

```text
<project>\.venv\Scripts\python.exe -m src.cli run
```

## Out of scope

Auto-apply, LinkedIn login, email, Google Sheets, PDF resume export, Notion.
