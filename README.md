# Job matching agent

Pipeline: **resume → scrape Indeed/LinkedIn → score 1–10 → enrich only if score > 7 → JSON files under `outputs/`**.

Fail-fast: if a step errors (after its configured fallbacks), later steps do not run. The run still writes a JSON summary to `outputs/`.

## Setup

```powershell
cd c:\Users\risha\work\jobMatchingAgent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Fill `.env`:

- `OPENAI_API_KEY`
- `APIFY_TOKEN` — required ([Apify integrations](https://console.apify.com/settings/integrations))
- `GOOGLE_APPLICATION_CREDENTIALS` — service account JSON (only if you load the resume from Drive)

Edit [`config/settings.yaml`](config/settings.yaml).

### Resume

```yaml
resume:
  primary: local          # or google_drive
  use_google_drive: false
  drive_file_id: ""       # file id or share URL
  local_path: "localData/RishabResume.pdf"
```

- If Drive is enabled and fails, local is tried **only when `local_path` is set**.
- Share the Drive file with the service account email.

Supported files: PDF, DOCX, MD, TXT.

### Output

Every run writes JSON under `outputs/`:

- `{timestamp}_run.json` — status, counts, errors (also copied to `run.json`)
- `{timestamp}_shortlisted.json` — enriched matches after a successful run (also copied to `shortlisted.json`)

### Scrape

Jobs are fetched through **Apify** (no local browser):

- Indeed: [kaix/indeed-scraper](https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ)
- LinkedIn: [dataji/apify-linkdin-jobs](https://console.apify.com/actors/d1gs0RHIwEnsan7XX)

Indeed uses `keywords`, `location`, `posted_within`, and `max_detail_jobs`. LinkedIn uses `scrape.apify.linkedin_input` plus `max_detail_jobs` as `maxResults`.

- Indeed lookback: `posted_within` `24h` | `3d` | `7d`
- LinkedIn lookback: `apify.linkedin_input.datePosted` (`past24Hours`)
- If LinkedIn fails but Indeed returns jobs, the run continues unless `strict_sources: true`.

### Models

- `openai.score_model` — scoring for every scraped job (`gpt-5.6-luna` or `gpt-5.6-sol`)
- `openai.enrich_model` — resume edits + interview prep only for relevance **> min_score** (default 7)

## Run

```powershell
python -m src.cli run
python -m src.cli run --config config\settings.yaml
```

Exit code `1` if a pipeline step failed.

## Daily schedule (Windows)

Task Scheduler → Create Task → trigger Daily → action:

`c:\Users\risha\work\jobMatchingAgent\.venv\Scripts\python.exe -m src.cli run`

Start in: `c:\Users\risha\work\jobMatchingAgent`

## Google Cloud (Drive resume only)

1. Create a service account, download JSON.
2. Enable **Google Drive API**.
3. Share the resume file with the SA client email.

## Out of scope

Auto-apply, LinkedIn login, PDF resume export, Notion.
