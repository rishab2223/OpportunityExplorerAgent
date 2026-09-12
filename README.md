# Opportunity Explorer Agent

Two separate features that share nothing but the files on disk:

1. **Discover** — scrapes Indeed and LinkedIn through Apify, scores each job against your resume (1–10), enriches only the jobs that score **at or above** `min_score`, and writes a self-sufficient run folder under `outputs/`: the shortlist, a tailored `.tex` per job, and a compiled `.pdf` per job. Runs headless from the CLI; nothing else is required to use the results.
2. **Assisted apply** — optional, one job at a time, started by hand from the web UI. Opens a visible Chrome, fills what it can, and stops to ask you whenever it is unsure.

Applying yourself is a first-class path: the table gives you the apply URL and the local path of the tailored PDF, so you never have to use the apply feature at all.

There is no email, Google Sheet, or Google Drive. The resume is always a local file.

## Pipeline

1. **Load resume** — local `.tex` (preferred) or PDF/DOCX/MD/TXT
2. **Scrape** — Indeed and/or LinkedIn via Apify
3. **Score** — one cheap LLM call per scraped job (plain text from the resume)
4. **Filter** — keep jobs with `relevance >= min_score` (default 7, so 7–10)
5. **Enrich** — for `.tex` input, per-section tailored edits (summary, skills, bullet rewording) spliced into your original LaTeX source, plus interview prep; for PDF, markdown suggestions plus interview prep. The model never rewrites the whole document: the preamble, section headings, and layout come through byte-identical, sections cannot be dropped, and edits that fail validation (unbalanced braces/environments, forbidden commands, big length changes) are rejected and retried once with the reasons named — a rejected section keeps its original text
6. **Dump** — `outputs/{timestamp}/`, compiling each tailored `.tex` to a `.pdf` and keeping it to **one page** (also writes a run summary after a failure). The model writes LaTeX blind, so length is enforced by measurement, not by asking: the model may add genuinely useful content (up to a 1.15× overall cap that blocks runaway rewrites), knowing the declared cost — if the compiled PDF runs past one page, content is dropped in a fixed priority order the model is told about: the CCNA certification bullet first, then the whole Certifications section, recompiling after each cut and stopping as soon as it fits. Every cut is named in the run log, and a resume that is still too long keeps its content and is flagged on the row rather than gutted further. Experience, Skills, Summary and Education are never touched.

If a step fails, later steps do not run. A run summary is still written so you can see which step failed.

## Requirements

- Python 3.11+
- [OpenAI API key](https://platform.openai.com/api-keys) for scoring
- For Claude enrichment: a Claude login (Pro/Max) for the `agent-sdk` backend, or an [Anthropic API key](https://platform.claude.com/) for the `api` backend
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
| `OPENAI_API_KEY` | yes | Scoring (and enrichment when `enrich_provider: openai`) |
| `ANTHROPIC_API_KEY` | only for `claude.backend: api` | Enrichment via the Anthropic API; the `agent-sdk` backend uses your Claude login instead |
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
- **`.tex`:** scoring uses stripped text; enrich returns per-section replacement bodies that are validated and spliced into your original source, producing a `.tex` per shortlisted job whose untouched parts are byte-identical to yours. `shortlisted.json` still includes `resume_edit_suggestions` as a changelog of those edits (including a note when an edit was rejected).
- **`.pdf` (and other non-tex):** scoring and markdown resume suggestions; dump is JSON only (no `.tex` files).
- V1 is a single main `.tex` file (no `\input` graph).
- Tailoring keys off `\section{...}` / `\section*{...}` headings; a `.tex` without any falls back to text suggestions.

### Scrape

Jobs come from Apify (no local browser):

- Indeed: [kaix/indeed-scraper](https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ)
- LinkedIn: [dataji/apify-linkdin-jobs](https://console.apify.com/actors/d1gs0RHIwEnsan7XX)

| Setting | Indeed | LinkedIn |
| --- | --- | --- |
| Search text | `scrape.keywords` → actor `keyword` (Indeed operators like `title:(...)` are allowed) | the URL-encoded `keywords=` query in `scrape.apify.linkedin_input.searchUrls` |
| Location | `scrape.location` → actor `country` (`India` → `IN`) | the `geoId` in `searchUrls` (`102713980` = India) |
| Recency | `scrape.posted_within`: `24h` / `3d` / `7d` → `fromDays` | derived from `scrape.posted_within`: `f_TPR` (`24h`→`r86400`, `3d`→`r259200`, `7d`→`r604800`) is injected into every `searchUrls` entry at run time |
| Cap per source | `scrape.max_detail_jobs` → `maxItems` | `scrape.max_detail_jobs` → `maxResults` |

Current defaults in `settings.yaml`: last **3 days** on both sources, **150** jobs per source. A local `posted_at` filter also drops anything older than `posted_within` regardless of source. Both actors run concurrently.

`scrape.sources` can be `indeed`, `linkedin`, or both.

If LinkedIn fails but Indeed returned jobs, the run continues unless `strict_sources: true`.

### Models

Scoring always runs on OpenAI (one short call per scraped job). Enrichment — the tailored resume and interview prep — can run on OpenAI or Claude:

```yaml
enrich_provider: claude          # openai | claude

claude:
  backend: agent-sdk             # agent-sdk | api
  enrich_model: claude-opus-5    # claude-opus-5 | claude-fable-5 | claude-sonnet-5
  effort: medium                 # low | medium | high | xhigh | max

openai:
  score_model: gpt-5.6-luna      # every scraped job
  enrich_model: gpt-5.6-sol      # only when enrich_provider is openai
```

- **`claude` / `agent-sdk`** runs each enrichment as a one-turn, tool-less query through the Claude Code binary bundled with `claude-agent-sdk`, authenticated the way Claude Code is (your Claude login, or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` for headless runs). No API key. Usage counts against your Claude plan's limits, and each call carries the Claude Code harness prompt (~20K tokens, cached after the first call), so it is slower per job (~45–90 s on Opus 5 at `medium`) than a bare API call. Note Anthropic's docs direct Agent SDK apps to API-key auth and do not permit offering claude.ai login to third parties; this is a personal tool, but read that note before relying on it.
- **`claude` / `api`** uses the Anthropic Messages API with `ANTHROPIC_API_KEY` in `.env` (pay-as-you-go, Opus 5 at $5/$25 per 1M tokens).
- **`openai`** uses `openai.enrich_model`: `gpt-5.6-luna` for lowest cost, `gpt-5.6-sol` for the best OpenAI LaTeX edits.

`min_score`, not the model, is the main cost lever: every shortlisted job gets one enrichment call.

`openai.score_concurrency` (default 8) and `openai.enrich_concurrency` (default 4) set how many LLM calls run in parallel per step; lower them if you hit rate limits.

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
| `{stamp}/{company}_{job_title}.pdf` | when a LaTeX toolchain is installed | Compiled resume, trimmed to one page, the path shown in the UI table |
| `{stamp}/applications.json` | after you skip or apply | Your per-job decision and apply status |

`outputs/run.json` / `outputs/shortlisted.json` are copies of the latest run.

### Job history

Apify returns many of the same postings run after run. A small SQLite database at `localData/job_history.db` (created automatically, gitignored, nothing to install — SQLite ships inside Python) remembers every job you applied to — via **Mark applied** on a row, or automatically when an assisted-apply session finishes — and drops those jobs **right after scrape** on later runs, before they cost a scoring or enrichment call. **Skip** is recorded too but does not block by default (`history.skip_skipped`). **Closed** marks a posting that stopped accepting applications (LinkedIn's "No longer accepting applications" banner is also detected automatically when you Start apply on one); closed jobs never come back (`history.skip_closed`), and Unmark reverses any of these.

Matching is by job id first, then by normalized company+title (`history.match_similar`) for the ~5% of postings whose id changes between scrapes; every company+title match is named in the run log, since it could hide a genuinely new opening with the same title. **Unmark** on a row reverses a mistake; deleting `localData/job_history.db` resets everything. Inspect it any time with `python -m sqlite3 localData/job_history.db "SELECT company, title, status, contact, marked_at FROM job_history"`.

To test or experiment without touching your real history, set `JOB_HISTORY_DB` to another path before starting the server or a script — every read and write goes to that file instead: `$env:JOB_HISTORY_DB='localData/scratch_history.db'; python -m src.web`.

#### Referrals

Some jobs are better chased through a referral than a cold application. **Referral** on a shortlist row asks who you are approaching, records the job as `referral_pending`, and moves it to the **Referrals** tab (top of the page, with a live count) showing contact, asked-date and state. From there:

- **Referral sent** — your application went in via the referral (`referral_sent`, shown blue).
- **Referral failed** / **Clear** — deletes the history row: the job hops back into the shortlist immediately and becomes eligible for future scrapes again.

Both referral states keep the job out of later runs (`history.skip_referral`), exactly like applied jobs — no wasted scoring or enrichment calls while you wait on a contact. The Referrals table shows the run you're viewing; a referral marked in an older run lives in that run (pick it in the Run dropdown to update it).

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

Before the first run, fill in [`localData/apply_profile.json`](localData/) (created automatically, gitignored) with your name, email, phone, notice period, CTC expectations, and work authorization — every field filled there is a question the agent never has to ask. Several more keys each remove a whole class of question:

| Key | What it fills |
| --- | --- |
| `skills` | a comma-separated list, written into a skills box or picked one by one in a skills typeahead. **Order matters**: a form that says "add up to 10 skills" gets the first ten, so put the strongest first |
| `languages` | `English - Intermediate; Hindi - Fluent` — the agent clicks *Add Language* once per entry and fills the level selects |
| `education` | `NorthCap University - Bachelors, Computer and Information Science, 2015-2019` — school, degree, field of study and years, so a 345-entry "Field of study" dropdown is answered without the model guessing |
| `jobs` | your employment history, **most recent first**, filled by the script so the model is never asked to re-read it off your resume. Each entry takes `title`, `company`, `location`, `start` and `end` as `MM/YYYY` (`"current": true` instead of an end date for the job you are in), and a `description`. The description is static: when you want it tailored for one application, type `redo` at the review prompt and edit it there. An entry the site has already filled with an employer this list does not name is left completely alone rather than overwritten |
| `university`, `highest_education_level`, `field_of_study`, `graduation_year` | the same facts as `education`, split out so a standalone degree dropdown or a "Field of study" list is answered without the model guessing |
| `city`, `postal_code`, `date_of_birth` | address and identity boxes a form asks for separately from `location` |
| `preferred_location`, `willing_to_travel`, `earliest_start_date`, `how_did_you_hear`, `relevant_experience_years` | asked by most forms, and constant across them |
| `linkedin`, `github`, `portfolio` | a box that names a site gets that link; a generic Websites/Portfolio section gets the remaining ones |
| `phone_country_code`, `state`, `address_line1`, `current_company_location` | phone-code pickers, address blocks, and the location of your current employer's entries in a work-history section |
| `salary_currency`, `salary_period` | the currency and period dropdowns that sit beside a salary amount (`INR`, `Annual`) |
| `not_employment` | resume entries that are your own projects, not jobs (`Applied AI & LLM Agents`). They are never entered as a work experience, and an entry a site creates from your resume is removed again |

Eligibility and demographic questions are deliberately **not** profile keys. Work authorisation, visa sponsorship, citizenship, gender, disability, veteran status and background checks are legal declarations: the agent asks you once, stores the answer only after you confirm it, and logs every reuse. No profile rule can answer one of them, whatever the profile happens to hold.

**Your contact details are re-checked after every step.** A site that parses your uploaded resume can overwrite them with its own reading — one wrote `acandidate@example.invalid` for an address whose underscore the PDF text layer had swallowed. If a box the agent filled changes underneath it, the right value goes back in; if any contact box disagrees with your profile, whoever wrote it, the review prompt leads with `CHECK YOUR CONTACT DETAILS` and names both values. (Resumes compiled by this project carry `\usepackage[T1]{fontenc}` so the underscore survives extraction; add it to any hand-written `.tex`.)

The form-filling model is `apply_provider` in `settings.yaml` (`claude` → `claude.apply_model`, default `claude-opus-5` at `apply_effort: low`; `openai` → `openai.apply_model`) — but most fields never reach it. A deterministic resolver fills everything the profile or the answer bank already covers (contact fields via their `autocomplete` attributes, links, notice period, CTC…), the script clicks Next/Continue/Review wizard buttons itself, and the model gets **one batched call per page** for only the fields that remain. A typical application costs 0-3 model calls; the session log ends with the exact tally (`Model calls this session: N`).

**The answer bank.** Any question you answer in chat is remembered in the `known_answers` table of `localData/job_history.db`, keyed by topic so every phrasing of "notice period" is one entry. Next application, it's filled automatically: neutral answers silently (logged as `[saved]`), legal/eligibility answers only after you confirmed "remember this?" once — and every reuse prints a visible `[saved] question -> answer` line. OTPs, passwords and captchas are never stored, and neither is anything mentioning the specific company. Fix a wrong entry any time: `python -m sqlite3 localData/job_history.db "SELECT * FROM known_answers"`.

**Attachments are built when the form asks for them**, never ahead of time — a form that never wants a resume or cover letter costs nothing.

- **Resume.** The moment an upload field is detected, the tailored `.tex` is compiled (needs a LaTeX toolchain such as MiKTeX on PATH) and a dialog offers the **tailored PDF** or your **default resume** (`resume.local_path`, served via `/api/resume/default.pdf`), with the model's edit changelog and links to view both. *Edit source* reveals the tailored LaTeX for small fixes: it is validated, compiled and re-measured for one page before use, and a broken edit is rolled back with the error shown. The choice is remembered for the rest of the session.
- **Cover letter.** When a cover-letter field is detected, the model drafts a short one (~3 paragraphs, plain voice) and the dialog opens with it in an editable box. Edit it freely — that costs nothing — or *Ask for changes* to have the model revise it (one call each). On accept, a textarea gets the text and a file input gets a small compiled PDF written to the run folder. Letters are never reused across jobs and never stored in the answer bank.
- **If detection misses the field**, the **Attach resume** and **Cover letter** buttons beside the chat box (or typing `attach resume` / `cover letter`) start either flow by hand. *Skip* in either dialog leaves the field untouched.

What happens:

1. A visible Chrome opens on the apply URL, using the persistent profile at `localData/chrome-profile`. Log into the job site once yourself; the session is remembered.
2. On a LinkedIn job the agent opens the apply flow itself: **Easy Apply** jobs get the in-page modal; **Apply on company website** jobs open the employer's form in a new tab, which the agent follows (the log shows `Switched to <url>`).
3. The resolver fills what it can, the model plans the rest, and the agent stops and asks in the chat pane for anything unknown: OTPs, captchas, consent and legal questions. Answer, or handle it in the browser yourself and type `done`. Type `skip` to leave a field alone, paste a URL to send the agent there, `abort` to stop.
4. **The agent never clicks submit — you do.** When everything is filled it says so and waits; you review the form, click Submit in the browser yourself, and type `done`. This is enforced in code (a submit click raises), not just prompted.

### The chat pane

The transcript is colour-coded so the eye lands on what needs you: what the agent asks (blue), what you replied (green), failures (red), warnings such as a contact detail that disagrees with your profile or an optional question left empty (amber), routine fills from the profile or answer bank (grey), and model chatter (faint). Long field labels and values are shortened to one line each — hover a shortened line to see it in full.

**The answer box** is a text area, not a single line, so a drafted paragraph is readable and editable in place. It grows with the text up to a point and can be dragged taller. **Enter** sends; **Shift+Enter** starts a new line. Whenever the agent pre-fills a draft, three things appear beneath it: **Ask for changes** (puts `llm: ` in front, so you type only what to change), **Restore draft** (the model's original text back after you have edited it), and a character count for forms with limits.

**Editing an answer the model drafted** — three cases:

1. **The draft is still on screen.** Edit it and press Enter, or press *Ask for changes* and say what to change (`llm: shorter, and mention the payments work`). One model call; the question comes back with the new draft loaded, as often as you like. `skip` leaves the field empty.
2. **You already sent it and the agent moved on.** Type `redo` at whatever it asks next, or at the "This step is filled in" prompt: your answer comes back in the box exactly as it went into the form. Edit and Enter, or `llm: <what to change>` to redraft, or `skip` to leave it. The agent then returns to the question it was asking. `redo` alone reopens the most recent text answer; name an older one by its words (`redo skill`, `redo salary`).
3. **The form asked something the agent never raised.** Optional questions it left empty are listed at the review prompt. Reply `llm:` plus the question, or enough of it to identify it, and you get a draft to edit.

Two limits: `redo` only reaches boxes still on the step in front of you (once you have clicked Continue, fix that one in the browser), and a redo changes the form but does not rewrite a previously saved entry in the answer bank.

**Chat commands** (also listed under *Chat commands* in the UI):

| Type | What happens |
| --- | --- |
| `next` / `auto next` | click the wizard's Next for you; `auto next` stops it asking for the rest of the session |
| `done` | the page is ready, or you submitted it yourself (`applied`, `submitted`, `finished`, `ok`, `continue`, `ready`) |
| `skip` | leave the current field empty (`leave it`, `leave blank`, `ignore`, `no answer`) |
| `yes` / `no` | confirm or refuse a click, checkbox or radio the agent proposes — short replies only, so a sentence containing "yes" is guidance, never consent |
| `llm: <instruction>` | draft or redraft the answer to the question being asked; at the review prompt, `llm: <question>` answers an optional question left empty |
| `redo` / `redo <words>` | reopen an answer you already gave, pre-filled, to edit or redraft |
| `attach resume` / `cover letter` | start either attachment flow by hand (same as the buttons) |
| `dump` / `dump 10` | save the page as it is (DOM, fields, screenshot) under `outputs/dom/` and keep waiting; `dump 10` waits ten seconds first so you can open a widget |
| a URL | open that page when the agent is stuck |
| `retry` | try the model again after an outage (`try again`) |
| `closed` | record that the posting no longer accepts applications and stop |
| `abort` | end the session (`stop`, `cancel`) |

OTPs and other secrets are held in memory for the session and never written to disk. The agent will not invent visa status, salary, or legal answers: anything that reads like a legal or eligibility declaration (consent, terms, work authorisation, citizenship, background checks, demographics) is gated — answered from your confirmed saved answer with a loud log line, or asked. For radio groups the gate acts only on the option that matches your answer, so a saved "No" can never tick the "Yes" box. A yes/no you type is parsed on whole words with negatives winning — "I don't agree" leaves the box unticked. There is no captcha solving and no unattended mass-apply.

## Daily schedule

Not wired up yet, deliberately. `python -m src.cli run` is fully non-interactive and reads keys from `.env`, so any scheduler can drive it. On Windows: Task Scheduler → Create Task → Daily trigger → Start in the project folder → action:

```text
<project>\.venv\Scripts\python.exe -m src.cli run
```

Each run writes its own `outputs/{stamp}/`, so daily runs pile up as a history the UI dropdown can browse.

## Out of scope

Unattended mass-apply, captcha solving, LinkedIn login automation, Overleaf API, email, Google Sheets, Notion.
