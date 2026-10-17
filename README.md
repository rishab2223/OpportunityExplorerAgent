# Opportunity Explorer Agent

Two separate features that share nothing but the files on disk:

1. **Discover** — scrapes Indeed and LinkedIn through Apify, scores each job against your resume (out of 100), enriches only the jobs that score **at or above** `min_score`, and writes a self-sufficient run folder under `outputs/`: the shortlist, a tailored `.tex` per job, and a compiled `.pdf` per job. Runs headless from the CLI; nothing else is required to use the results.
2. **Assisted apply** — optional, one job at a time, started by hand from the web UI. Opens a visible Chrome, fills what it can, and stops to ask you whenever it is unsure.

Applying yourself is a first-class path: the table gives you the apply URL and the local path of the tailored PDF, so you never have to use the apply feature at all.

There is no email, Google Sheet, or Google Drive. The resume is always a local file.

## Pipeline

1. **Load resume** — local `.tex` (preferred) or PDF/DOCX/MD/TXT
2. **Scrape** — Indeed and/or LinkedIn via Apify, then drop jobs already in your history and, when `experience.enabled`, jobs asking for more years than you allow
3. **Score** — one cheap LLM call per scraped job (plain text from the resume), out of 100. The model marks six parts one at a time against fixed anchors, and the code adds them up: **skills 35, experience 20, role 15, stack 10, company 10, location 10**. A single 1–10 number put 96 of 104 shortlisted jobs at 7 or 8 and none at 10, so it did not rank. A job scoring just under the bar (`second_look_below`, 10 points by default) is scored again by a stronger model (`openai.second_look_model`) and kept if either look reaches it; only that band pays for the second call. A call that fails twice keeps the job at the bar, marked "check it yourself", rather than losing it. Re-scored this way, 70 of the 89 jobs you had applied to reached 70 (14 of them through the second look) in 80 s
4. **Filter** — keep jobs with `relevance >= min_score` (default 70). A bar written on the old scale (1–10) is read as tens: 7 means 70. Runs saved before Oct 2026 keep their 1–10 scores and show as `8/10`
5. **Enrich** — for `.tex` input, per-section tailored edits (summary, skills, bullet rewording) spliced into your original LaTeX source; for PDF, markdown suggestions. Interview prep is written on demand by default (**Job info → Write interview prep**), or with every resume when `interview_prep: run`. Postings with the same company and title share one tailoring call, and a posting an earlier run already tailored against the same resume and model reuses that result (`reuse_enrichment`). The model never rewrites the whole document: the preamble, section headings, and layout come through byte-identical, sections cannot be dropped, and edits that fail validation (unbalanced braces/environments, forbidden commands, big length changes) are rejected and retried once with the reasons named — a rejected section keeps its original text
6. **Dump** — `outputs/{timestamp}/`, compiling each tailored `.tex` to a `.pdf` and keeping it to **one page** (also writes a run summary after a failure). The model writes LaTeX blind, so length is enforced by measurement, not by asking: the model may add genuinely useful content (up to a 1.15× overall cap that blocks runaway rewrites), knowing the declared cost — if the compiled PDF runs past one page, your **fit policy** is applied step by step, recompiling after each and stopping as soon as it fits. You agree that policy with the AI in the **Resume fit** tab (see [Web UI](#web-ui)); until you save one, the default drops the whole Certifications section. Every step is named in the run log, the resume as written is kept in `{stamp}/full/`, and a resume that is still too long keeps its content and is flagged on the row rather than gutted further. PDFs compile side by side (`pdf_concurrency`).

If a step fails, later steps do not run. A run summary is still written so you can see which step failed.

## Requirements

| What | Needed for | Notes |
| --- | --- | --- |
| Python 3.11+ | everything | |
| [OpenAI API key](https://platform.openai.com/api-keys) | scoring, always | Scoring runs on OpenAI whichever provider tailors. Also tailoring and form filling when you choose `openai` for them |
| [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) + a Claude login (Pro/Max) | `claude` provider with `backend: agent-sdk` | Installed with the project. It drives a bundled Claude Code binary, which signs in the way Claude Code does: log in once with the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) (`claude`, then `/login`), or make a long-lived token with `claude setup-token` and put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`. No API key |
| [Anthropic API key](https://platform.claude.com/) | `claude` provider with `backend: api` | Pay as you go, instead of the login above |
| [Apify token](https://console.apify.com/settings/integrations) | scraping | The two scrapers are third-party Apify actors ([Scrape](#scrape)); check their pricing on Apify before a large run |
| SQLite | job history, saved answers, caches | Built into Python; `localData/job_history.db` is created on first use |
| A resume | everything | `.tex` for tailored LaTeX and PDFs, or PDF, DOCX, MD or TXT for scoring and text suggestions. [`examples/resume_example.tex`](examples/resume_example.tex) shows the shape tailoring expects |
| LaTeX (MiKTeX or TeX Live) | PDFs | `latexmk` or `pdflatex` on `PATH`, with the packages your resume uses (the example needs `geometry`, `lmodern`, `enumitem`, `hyperref`). Without it a run still succeeds and writes `.tex` only, recording the reason in `resume_pdf_error` |
| `playwright install chromium` | assisted apply and the browser checks | Skip it if you only scrape and apply by hand. Apply uses your installed Google Chrome when there is one, else this Chromium |

## Setup

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
playwright install chromium
copy .env.example .env
New-Item -ItemType Directory -Force localData
copy examples\resume_example.tex localData\resume.tex
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
mkdir -p localData && cp examples/resume_example.tex localData/resume.tex
```

Fill `.env`:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | Scoring, and tailoring or form filling when their provider is `openai` |
| `APIFY_TOKEN` | yes | Indeed and LinkedIn scrapes |
| `CLAUDE_CODE_OAUTH_TOKEN` | only for `agent-sdk` without a Claude Code login | The token from `claude setup-token` |
| `ANTHROPIC_API_KEY` | only for `claude.backend: api` | Claude through the Anthropic API |
| `JOB_HISTORY_DB` | no | Another path for the SQLite database (default `localData/job_history.db`) |

Then make it yours:

1. **Resume.** Replace `localData/resume.tex` with your own (keep the `\section*{...}` headings and the `lmodern` / `fontenc` lines, see [Fonts](#fonts)), or point `resume.local_path` in `config/settings.yaml` at it.
2. **Search.** [`config/settings.yaml`](config/settings.yaml) as shipped is the author's search: software engineering roles in India, LinkedIn's India `geoId`, 150 postings per source, the last 3 days. Change `scrape.keywords`, `scrape.location` and the LinkedIn `searchUrls` to yours.
3. **Models.** The shipped settings tailor and fill forms with Claude through `agent-sdk` (`enrich_provider: claude`, `apply_provider: claude`). With an OpenAI key and nothing else, set both to `openai`. See [Models](#models).
4. **Profile for assisted apply.** Fill it from the dashboard (user icon → **Profile**), or copy [`examples/apply_profile.example.json`](examples/apply_profile.example.json) to `localData/apply_profile.json` and edit it. See [Assisted apply](#assisted-apply).

Everything personal lives in `localData/`, `outputs/`, `dumps/` and `.env`, all gitignored: nothing you add there reaches the repository.

## Config

### Resume

```yaml
resume:
  local_path: "localData/resume.tex"   # or .pdf
```

- Local disk only. Relative paths are resolved from the project root.
- `localData/` is gitignored. Copy your Overleaf source to e.g. `localData/resume.tex`.
- **`.tex`:** scoring uses stripped text; enrich returns per-section replacement bodies that are validated and spliced into your original source, producing a `.tex` per shortlisted job whose untouched parts are byte-identical to yours. `shortlisted.json` still includes `resume_edit_suggestions` as a changelog of those edits (including a note when an edit was rejected).
- **`.pdf` (and other non-tex):** scoring and markdown resume suggestions; dump is JSON only (no `.tex` files).
- V1 is a single main `.tex` file (no `\input` graph).
- Tailoring keys off `\section{...}` / `\section*{...}` headings; a `.tex` without any falls back to text suggestions.

#### Fonts

Every tailored `.tex` is given two packages before it compiles, if your source does not already have them:

```latex
\usepackage{lmodern}
\usepackage[T1]{fontenc}
```

`fontenc` is what makes an underscore in an email address survive PDF text extraction — without it, a site that parses your uploaded resume reads `a_candidate@…` as `acandidate@…` and fills the wrong address into its own form. But `[T1]{fontenc}` **alone** switches the document to EC fonts, which ship only as bitmaps: the text stays correct and becomes visibly soft and grey, and the PDF carries Type 3 fonts. `lmodern` supplies the same shapes as real Type 1 vectors, so you get the encoding without the blur. Both lines, or neither — one on its own is the worst of the three.

If you hand-write a `.tex`, put both in your preamble. The pair is added idempotently, so a source that already has them is left byte-identical.

### Scrape

Jobs come from Apify (no local browser):

- Indeed: [kaix/indeed-scraper](https://console.apify.com/actors/BIeK7ZcYUrdxDgOEQ)
- LinkedIn: [dataji/apify-linkdin-jobs](https://console.apify.com/actors/d1gs0RHIwEnsan7XX)

| Setting | Indeed | LinkedIn |
| --- | --- | --- |
| Search text | `scrape.keywords` → actor `keyword` (Indeed operators like `title:(...)` are allowed) | the URL-encoded `keywords=` query in `scrape.apify.linkedin_input.searchUrls` |
| Location | `scrape.location` → actor `country` (`India` → `IN`) | the `geoId` in `searchUrls` (`102713980` = India) |
| Recency | `scrape.posted_within`: `24h` / `3d` / `7d` → `fromDays` | derived from `scrape.posted_within`: `f_TPR` (`24h`→`r86400`, `3d`→`r259200`, `7d`→`r604800`) is injected into every `searchUrls` entry at run time |
| Cap per source | `scrape.max_jobs.indeed`, else `scrape.max_detail_jobs` → `maxItems` | `scrape.max_jobs.linkedin`, else `scrape.max_detail_jobs` → `maxResults` |

Current defaults in `settings.yaml`: last **3 days** on both sources, **150** jobs per source (1–500; the run settings popup sets each source on its own). A local `posted_at` filter also drops anything older than `posted_within` regardless of source. Both actors run concurrently.

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

`openai.score_concurrency` (16 in `settings.yaml`, 1–32) and `openai.enrich_concurrency` (8, 1–16; used for Claude too) set how many LLM calls run in parallel per step; lower them if you hit rate limits or your Claude plan starts refusing calls.

#### Speed

A 266-job run took an hour; nearly all of it was tailoring (46 min) and compiling PDFs one at a time (10 min). These keep it down:

| Setting | Default | Effect |
| --- | --- | --- |
| `interview_prep` | `on_demand` | `run` writes interview prep with every tailored resume — about 60% of what that call writes. `on_demand` writes it only for a job you open (**Job info → Write interview prep**). |
| `reuse_enrichment` | `true` | A posting scored or tailored by an earlier run, against the same resume, model and prompt, reuses that result (`llm_cache` in `localData/job_history.db`; entries older than 30 days are ignored). |
| `pdf_concurrency` | `6` | Resumes compiled at once (1–16). |
| `openai.enrich_concurrency` | `8` | Tailoring calls at once. |

The same posting listed in several cities (same company and title, descriptions at least 97% alike) is scored and tailored once and shares the result; two different roles under one title are not merged. Each Claude call through `agent-sdk` starts a Claude Code process (about 20 s before the model sees the prompt), with no tool definitions in it and a working folder of its own, so it never reads this repo's `.claude/` settings. Assisted apply does not pay that per call: clicking Apply starts two Claude Code processes in the background while the job page loads (one for form decisions, one for dropdown picks) and keeps them until the session ends, so a decision takes about 2 s. Each process is cleared (`/clear`) after every answer: a fresh session id alone did not stop one call's conversation reaching the next. If a kept process fails, that call starts its own as before. Each run's `run.json` records seconds per step under `timings`, and the run settings popup shows the last run's figures.

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

### Tests

The whole suite is offline — no API key, no network, no browser — so it runs in about ten seconds:

```powershell
python -m unittest discover -s test
```

They cover the resolver's rules, the apply loop's guards, the answer bank, LaTeX validation, PDF trimming, salary parsing and the history database. Anything touching the apply engine should keep them green. Most exist because a real application went wrong in a specific way, and the comment above each says which — so a failure usually names the bug it was written for.

Two files in `test/` are **not** unit tests: they are manual probes that call the real Apify actors and spend credits. Run them by hand when an actor's output format changes, never as part of the suite:

```powershell
python test/probe_indeed_apify.py
python test/probe_linkedin_apify.py
```

They are named `probe_*` so `unittest discover` does not import them on every run.

They write JSON under `test/output/`.

### Browser checks

The unit suite never runs the apply loop, so it cannot catch a mistake inside it. `test/browser/` does: each script drives a real Chromium against a local fixture and asserts what the agent did. `run_e2e.py` and `run_e2e_b.py` are the main ones — 23 scenarios through the real `run_session`, about 65 seconds each. They share their rig (`e2e_common.py`) and run as two scripts so `check.py -j` can overlap them.

No model is called by any of it. The harness swaps in a scripted stand-in, which is why every scenario asserts an exact model-call count; the unit suite has no route to a model at all. A full run costs time and nothing else.

```powershell
python check.py            # only the groups your working tree touches
python check.py sites      # one group by name
python check.py all        # everything
python check.py all -j6    # the same, six scripts at a time (~147s, not ~480s)
python check.py --list     # the groups and what is in them
```

`check.py` always includes the unit suite — at ten seconds there is nothing to gain by skipping it — and picks the browser groups from which files you changed. `python check.py --list` shows what each group covers.

## Outputs

Each run uses a timestamp folder, e.g. `outputs/20260823T140406/`:

| Path | When | Contents |
| --- | --- | --- |
| `{stamp}/run.json` and `outputs/run.json` | always | Status, counts, error if any |
| `{stamp}/run.log` | always | Every progress line from that run |
| `{stamp}/shortlisted.json` and `outputs/shortlisted.json` | success only | Enriched matches (URLs, scores, interview prep, resume-edit changelog, `resume_tex_file`, `resume_pdf_path`) |
| `{stamp}/{company}_{job_title}.tex` | success, and resume input was `.tex` | Tailored one-page resume per shortlisted job |
| `{stamp}/{company}_{job_title}.pdf` | when a LaTeX toolchain is installed | Compiled resume, fitted to one page, the path shown in the UI table |
| `{stamp}/full/{company}_{job_title}.tex` | when the fit policy had to shorten it | The tailored resume as written, before any fit step — what the Resume fit tab previews on |
| `{stamp}/run_settings.json` | runs started from the web page | What the run settings popup chose for that run |
| `{stamp}/applications.json` | after you skip or apply | Your per-job decision and apply status |

`outputs/run.json` / `outputs/shortlisted.json` are copies of the latest run.

### Experience filter

Off unless `config/settings.yaml` turns it on; nothing is inferred from your resume. The rule lives in `settings.yaml`; your years are `total_experience_years` in your profile (user menu → **Profile**), the same value application forms are filled from, and the filter does nothing while it is empty.

```yaml
experience:
  enabled: true
  tolerance_years: 1     # 0 = strict; 1 keeps a 7+ job at 6 years and drops 8+, and drops a 1-4 range
  mode: review           # drop | review - required once enabled
  review_min_score: 90   # review only: the score (out of 100) a held-back job needs to be shown
  review_max: 5          # review only: at most this many per run
```

It runs right after scrape. Only the general requirement counts ("8+ years of software engineering experience"); a skill line ("3+ years of Kafka") does not, and a posting that states no minimum is kept. It works both ways: a posting whose range tops out below your years minus the tolerance ("1-4 years" at six years) is a junior role and is held or dropped the same way (`asks for up to 4 years; you have 6`); "7+" names no top and is never read as junior. The reader is `src/experience.py`. What happens to a job outside the range is `mode`, which has no default — an enabled filter without one stops the run with a message saying so:

- **`drop`** — removed before scoring, so it costs nothing, and logged (`Skipping 'Esko Software Engineer Specialist': asks for 8+ years; your limit is 7`). You never see it.
- **`review`** — scored like any other job (one cheap call), never enriched. Those scoring `review_min_score` or more, strongest first and at most `review_max`, go to the **Held back** tab instead of the shortlist, each with its reason. Held back is a waiting list across **every** run, not just the one selected: a job held on Monday is still there on Wednesday, with a **Found** column naming the run that found it (a job held in several runs shows once, from the newest). **Start apply** there uses your base resume, since no tailored one was made, and switches the Jobs tab to that job's run. **Move to shortlist** puts the job in the shortlist of the run that found it and says so ("Moved … to the shortlist of the 1 Oct, 3:30 pm run") with a **Show it** link that opens that run on the Jobs tab with the row picked out — no automatic jump, so you can work down the list. Skip, Mark applied, Referral and Closed work as on any row. Acting on a row takes it off the list, and a moved job keeps a small "held back" tag in the Jobs table. The run writes them to `{stamp}/held_back.json`.

Held back also reads your own clicks across every run and suggests, never applies, a change to the rule: keep 3 or more held-back jobs and at least three in four of those you decided on, and it names the `tolerance_years` that would have let them all through; skip 5 or more and keep none, and it suggests `mode: drop` to stop scoring them. Closed postings count for neither.

### Job history

Apify returns many of the same postings run after run. A small SQLite database at `localData/job_history.db` (created automatically, gitignored, nothing to install — SQLite ships inside Python) remembers every job you applied to — via **Mark applied** on a row, or automatically when an assisted-apply session finishes — and drops those jobs **right after scrape** on later runs, before they cost a scoring or enrichment call. **Skip** is recorded too but does not block by default (`history.skip_skipped`). **Closed** marks a posting that stopped accepting applications (LinkedIn's "No longer accepting applications" banner is also detected automatically when you Start apply on one); closed jobs never come back (`history.skip_closed`), and Unmark reverses any of these.

Matching is by job id first, then by normalized company+title (`history.match_similar`) for the ~5% of postings whose id changes between scrapes. A company+title match also has to share most of its description with the job you marked: recruiters such as Recrew AI post every client's role under their own name, so two "Backend Engineer" jobs there can be two different companies. Each history row keeps the opening of its job's description for this; rows recorded before that are filled in from their run the first time the app reads one. Every company+title match is still named in the run log. **Unmark** on a row reverses a mistake; deleting `localData/job_history.db` resets everything. Inspect it any time with `python -m sqlite3 localData/job_history.db "SELECT company, title, status, contact, marked_at FROM job_history"`.

To test or experiment without touching your real history, set `JOB_HISTORY_DB` to another path before starting the server or a script — every read and write goes to that file instead: `$env:JOB_HISTORY_DB='localData/scratch_history.db'; python -m src.web`.

#### Referrals

Some jobs are better chased through a referral than a cold application. **Referral** on a shortlist row asks who you are approaching, records the job as `referral_pending`, and moves it to the **Referrals** tab (top of the page, with a live count) showing contact, asked-date and state. From there:

- **Referral sent** — your application went in via the referral (`referral_sent`, shown blue).
- **Referral failed** / **Clear** — deletes the history row: the job hops back into the shortlist immediately and becomes eligible for future scrapes again.

Both referral states keep the job out of later runs (`history.skip_referral`), exactly like applied jobs — no wasted scoring or enrichment calls while you wait on a contact. Like Held back, the Referrals tab spans every run: a referral asked in an older run is still listed when a newer run is on screen, with the **Found** column naming the run it came from, and its buttons act on that run. Its own **Run** dropdown starts on *All runs* and lists each run that has a referral, with the count, to look at one run at a time.

PDF runs stop at JSON in that folder. TeX runs add a `.tex` (and, with LaTeX installed, a `.pdf`) per shortlisted job. Live dumps stay under `outputs/` and are gitignored.

A sample from an older JSON run is in [`examples/sample_shortlisted.json`](examples/sample_shortlisted.json).

## Web UI

```powershell
python -m src.web              # then open http://127.0.0.1:8000
opportunity-explorer-web       # same thing after pip install -e .
```

One page, three cards, plus the **Applied**, **Referrals**, **Held back** and **Resume fit** tabs:

- **Run** — **Start run…** opens the run settings popup, then the live log. Logs stream over server-sent events, so you watch `[score] 12/40 …` as it happens instead of guessing. One run at a time.
- **Shortlist** — pick any past run from the dropdown; the table shows company, title, relevance, location, apply and listing links, the local PDF path with a copy button, status, and per-row actions. Long runs page at 15 rows: `‹ Prev  1 2 3 … [box] … 29 30  Next ›`, where the box takes a page number directly, so run 200 of a 400-job scrape is one keystroke away instead of thirty clicks. An identical toolbar sits **above and below** the table — tick boxes and **Start queue**, plus **Job info** — so a long page never has to be scrolled back up to act on it.
- **Apply** — the transcript and chat box for an assisted apply session, directly under the table.

Clicking a row selects it; it does not open anything. **Job info** expands the selected job's why_score, resume-edit changelog, interview prep and a PDF preview of the tailored resume — kept behind a button because that preview is 780px tall and otherwise sits between the shortlist and the chat you are answering.

The UI never downloads files. Copy the path from the table and open the PDF wherever you like.

### Run settings

**Start run…** opens a popup instead of starting straight away. It starts from `config/settings.yaml` (the "standard settings") and covers one run. It is laid out as the three things a run does, and every choice has a line under it saying, in plain words, what it will do - rewritten as you change it (the experience line names your own years from the Profile):

1. **Find jobs** — a switch per job site (LinkedIn, Indeed) and how many postings to fetch from each, 1–500, with a running total; **what each site is searched for**, as a comma-separated list of terms (up to 15, prefilled with what `settings.yaml` searches today); how recent a posting must be; words that rule a posting out before it is scored. Indeed's terms go to Apify as a title query — `title:("Software Engineer" or "NodeJS")`, each matched in the job title — and LinkedIn's into the search URL's `keywords=` (`software engineer OR nodejs`), the URL's location and sort left as they are. Terms nobody changed send exactly the query they were read from. A site that is switched on needs at least one term. The run log names them: `[run] searching LinkedIn for … ; Indeed for …`.
2. **Score and keep the good ones** — the score a job needs for a tailored resume, with an **(i)** beside it: hover or focus it to see how the 100 marks are given, part by part (read from the scorer itself, so it cannot describe another rubric); whether to watch the years of experience a posting asks for, how far either side of yours is fine, and whether jobs outside that are left out or kept aside in **Held back**; and which jobs from earlier runs stay out (applied, skipped, referral, closed).
3. **Tailor your resume** — Claude or OpenAI, the model, how hard Claude thinks, interview prep for every job or only when you open one, and reusing work from earlier runs.

**Advanced**, folded away, holds the speed and file settings most runs never touch: the scoring model, how many jobs are scored / tailored / turned into PDFs at once, whether to make PDFs, and your resume file. A problem in one of them opens it.

The footer shows how many jobs the run will fetch and how long the last run took, step by step. A value out of range says so and keeps **Start run** disabled; a control in a switched-off section is not read at all, and the server checks everything again and shows its answer in the popup, which stays open. **Use these next time too** saves only what differs from `settings.yaml` to `localData/run_preferences.json`, for the next run started here (the CLI reads only `settings.yaml`); a notice at the top says when saved choices are in effect, with a link back to the standard settings. **Standard settings** puts the file's values back in the form. Each run's choices are written to `{stamp}/run_settings.json`, summed up on the first lines of its log, and used again for work on that run's jobs afterwards (interview prep is written against the resume the run tailored from).

### Applied

Every job marked applied (by hand, or when an apply session ends), and what happened next. History used to stop at "applied", so nothing recorded replies, interviews or rejections, and nothing could say which channel, score or kind of company gets answers.

- **The stage bar**: Applied → Screening → Interview → Assessment → Offer → Accepted, with a count under each, plus Rejected and Withdrawn and how many moved past Applied at all.
- **One run at a time**: the **Run** menu lists *All runs* and each run that has applications, newest first, named by date with its count (an application recorded without a run is under *No run*). It opens on the newest run and remembers your choice; everything on the page — the stage bar, the board, the table, the counts — follows it. **Follow-up due only** narrows that to the applications waiting on you.
- **Columns or Table** (remembered): a board with a card per application in its stage's column — score, source, days since applying, follow-up — or a table with search, stage and source filters, sortable columns and a stage menu on each row.
- **Click any application** for its panel: the stage, a timeline of stage changes and messages sent, the contact (name, role, profile link, and **how you know them**), notes, and the messages:
  - **Referral, Outreach, Follow-up, Thank you** — **Draft it** writes one from the posting, your profile and resume; **Copy**, **Mark sent** (records the date and sets a follow-up a week later), **Redraft**. You send every message yourself: they are drafts only.
  - **Edit with AI** — a small chat that knows the job and your profile: "make it shorter", "drop the college line". It rewrites the draft above. The connection line (a shared employer or college) is used only when *how you know them* says so, and no fact is invented; your own AI project work is called projects, never a job.
  - The message model starts when the page opens and is kept for the server's life, so a draft takes a few seconds, not twenty.
- **Follow up?** — an open application with no change for 10 days, or a sent message's follow-up date passed, is flagged amber.

Stages are yours to set; nothing guesses a stage from silence. Referrals stay on their own tab. The data is in `localData/job_history.db` (`applications`, `application_events`, `outreach_messages`).

### Resume fit

When a tailored resume runs past one page something has to give, and the **Resume fit** tab is where you decide what. It is a conversation with the AI on the left and a resume on the right:

- Say what you want ("keep my certifications; tighten the layout first, but nothing cramped"). The assistant replies and revises the **draft policy** below the chat: an ordered list of steps, each one of — a layout change (margins 0.4–1.0in, font size 10/11/12pt, space around headings or between bullets; only ever tighter than the resume already is), dropping one bullet (if it is the only one in its list, the list goes too), dropping a section, or a **fixed shorter version** of a section it writes from your own text for you to read first. A fixed text replaces whatever the tailoring wrote in that section, so it suits sections tailoring leaves alone (Education, Certifications, Projects), not Summary or Skills. It can also note rules for the tailoring model ("never remove the AWS certification").
- Every reply is tried on a real resume: **Try it on** lists the tailored resumes from recent runs, the ones that ran long first, rebuilt as they were before anything was cut. The trail above the PDF shows the page count as written and after each step that ran.
- Reorder or remove a step by hand with the arrows and ×; the preview follows.
- When the assistant thinks you have agreed it says so, but nothing changes for your runs until you press **Save as my fit policy**. From then on every run applies it, and the tailoring prompt is told what it costs to add length. **Back to the default rule** forgets it; **Start over** clears the conversation and keeps the saved policy.

The policy lives in `localData/resume_fit_policy.json` and the conversation in `localData/resume_fit_draft.json`. Every step is applied by code and checked (a margin outside its range, an unbalanced section or a heading the resume does not have is refused with the reason), never as free-form edits by the model. A step that breaks the compile on some resume is skipped for that resume, named in the log, and the steps after it still get their turn. The same policy applies when you edit a resume during an apply session.

## Assisted apply

Click **Start apply** on a row, or tick a few rows and use **Start queue** ([the queue](#the-queue)). Either way it is deliberately supervised and strictly one job at a time.

Before the first run, fill in your profile: the user icon at the top right of the dashboard → **Profile**. It edits `localData/apply_profile.json` (gitignored; [`examples/apply_profile.example.json`](examples/apply_profile.example.json) shows a filled one; editing the file by hand works too), groups the keys into sections, marks the empty ones, checks numbers, emails, URLs and job dates before saving, and keeps any key it does not know. A save is used from the next box the agent fills, with no restart. Start with your name, email, phone, location, notice period and CTC expectations — every field filled there is a question the agent never has to ask, and a fact the model never has to be paid to re-derive from your resume. Several more keys each remove a whole class of question:

| Key | What it fills |
| --- | --- |
| `full_name`, `email`, `phone`, `location` | the contact block on every form, matched by the boxes' `autocomplete` attributes before their labels, so a form in any wording gets them |
| `current_company`, `current_title`, `total_experience_years` | "Current employer", "Current designation", "Total years of experience" |
| `notice_period`, `current_ctc`, `expected_ctc`, `willing_to_relocate` | the four questions almost every Indian application asks. `notice_period` takes a human phrase (`2 months`, `Immediate`) and is converted to whatever unit the box wants |
| `skills` | a comma-separated list, written into a skills box or picked one by one in a skills typeahead. **Order matters**: a form that says "add up to 10 skills" gets the first ten, so put the strongest first |
| `languages` | `English - Intermediate; Hindi - Fluent` — the agent clicks *Add Language* once per entry and fills the level selects |
| `education` | `Example University - Bachelors, Computer and Information Science, 2015-2019` — school, degree, field of study and years, so a 345-entry "Field of study" dropdown is answered without the model guessing |
| `jobs` | your employment history, **most recent first**, filled by the script so the model is never asked to re-read it off your resume. Each entry takes `title`, `company`, `location`, `start` and `end` as `MM/YYYY` (`"current": true` instead of an end date for the job you are in), and a `description`. The description is static: when you want it tailored for one application, type `redo` at the review prompt and edit it there. An entry the site has already filled with an employer this list does not name is left completely alone rather than overwritten |
| `university`, `highest_education_level`, `field_of_study`, `graduation_year` | the same facts as `education`, split out so a standalone degree dropdown or a "Field of study" list is answered without the model guessing |
| `degree_recognized_by`, `college_tier` | asked by most Indian forms and constant for a candidate: the accrediting body (`UGC`, `AICTE`) and where an employer's tier list puts the college (`Tier 1`, `Tier 2`, `Other/Not Listed`) |
| `gpa_10_point`, `gpa_5_point`, `gpa_scale` | the same grade on both scales, because a form asks for one or the other and converting on the spot is how a 7.4 becomes a 7.4 out of 5. Filled **only where the form makes it mandatory**. `gpa_scale` (`10` or `5`) is the scale actually studied on, so a form asking about the other one gets "I attended a university using a 10-point scale" rather than a converted figure |
| `city`, `postal_code`, `date_of_birth` | address and identity boxes a form asks for separately from `location` |
| `education_start_date`, `education_end_date` | DD/MM/YYYY. For a form whose education boxes are full date pickers; without them the years are taken from `education` / `graduation_year` and a box that insists on a month is left to you |
| `father_name` | for a "Father's name" box. A form's Family Details rows are never filled from the profile: their Name and Phone are someone else's |
| `pronouns` | optional; goes only into a "Pronouns" box, and only what you write here. Left empty, the box is left alone |
| `preferred_location` | cities, `Bengaluru, Pune`, or `Any` when location is no filter for you. A "which cities do you prefer?" list of tick boxes gets every city it offers under `Any` (never "None of the above" or "Other"), or the ones you name, with their other spellings (Bangalore is Bengaluru). A one-choice question gets "Any location" / "No preference" when it offers one, or your first city |
| `willing_to_travel`, `earliest_start_date`, `how_did_you_hear`, `relevant_experience_years` | asked by most forms, and constant across them. Leave `earliest_start_date` empty and it is **worked out from `notice_period`**: today plus the notice, moved off a weekend, as `YYYY-MM-DD` (logged as `[notice period]`). A start-date dropdown ("Within 30 days") is not answered this way, since a date matches none of its options |
| `linkedin`, `github`, `portfolio` | a box that names a site gets that link; a generic Websites/Portfolio section gets the remaining ones |
| `phone_country_code`, `state`, `address_line1`, `current_company_location` | phone-code pickers, address blocks, and the location of your current employer's entries in a work-history section |
| `salary_currency`, `salary_period` | the currency and period dropdowns that sit beside a salary amount (`INR`, `Annual`) |
| `not_employment` | resume entries that are your own projects, not jobs (`Weekend AI Projects`). They are never entered as a work experience, and an entry a site creates from your resume is removed again |

Eligibility and demographic questions are deliberately **not** profile keys. Work authorisation, visa sponsorship, citizenship, gender, disability, veteran status and background checks are legal declarations: the agent asks you once, stores the answer only after you confirm it, and logs every reuse. No profile rule can answer one of them, whatever the profile happens to hold.

**Your contact details are re-checked after every step.** A site that parses your uploaded resume can overwrite them with its own reading — one wrote `acandidate@example.invalid` for an address whose underscore the PDF text layer had swallowed. If a box the agent filled changes underneath it, the right value goes back in; if any contact box disagrees with your profile, whoever wrote it, the review prompt leads with `CHECK YOUR CONTACT DETAILS` and names both values. (Resumes compiled by this project carry `\usepackage{lmodern}` and `\usepackage[T1]{fontenc}` so the underscore survives extraction; add both to any hand-written `.tex`. `fontenc` alone is not enough — see [Fonts](#fonts).)

The form-filling model is `apply_provider` in `settings.yaml` (`claude` → `claude.apply_model`, default `claude-opus-5` at `apply_effort: low`; `openai` → `openai.apply_model`) — but most fields never reach it. A deterministic resolver fills everything the profile or the answer bank already covers (contact fields via their `autocomplete` attributes, links, notice period, CTC…), the script clicks Next/Continue/Review wizard buttons itself, and the model gets **one batched call per page** for only the fields that remain. A typical application costs 0-3 model calls; the session log ends with the exact tally (`Model calls this session: N`).

#### What the script handles without asking

Each of these started as a real application that went wrong. They are listed so you know what the agent is already expected to survive — if one of them misbehaves again, it is a regression, not a gap.

| Situation | What happens |
| --- | --- |
| **A form wipes what was typed** | React and Next.js forms hydrate *after* the first paint. A value filled before that moment is accepted, reads back correctly, and is then thrown away when the component mounts. Every value the agent wrote is re-checked against the live page at the end of the step and typed again — with real keystrokes, which a controlled component cannot discard — if it went missing |
| **A skills box that is not a chip box** | The control is classified before anything is typed: a plain input or textarea gets one comma-separated write, a chip typeahead gets one skill at a time with its suggestion list, a `<select multiple>` gets its options picked by name. Guessing wrong the other way — dumping a comma list into a chip box — makes one nonsense chip that cannot be undone, so an ambiguous control tries the typeahead first |
| **"Notice period" in the wrong unit** | A box asking for days gets `60`, one asking for months gets `2`, one asking for free text gets `2 months`. The unit comes from the label, not from a guess |
| **A salary box that reformats as you type** | Some amount fields insert their own separators on each keystroke, so a profile value of `30,00,000` typed one character at a time came out as `3,00,00,000` — three crore instead of thirty lakh, on a real application. Salary amounts go in as bare digits and the box applies its own formatting. A value that is prose rather than a number (`25-30 LPA`) is left exactly as written |
| **Styled controls with no real input** | Radix and shadcn render a checkbox or radio as a `<button>` with `aria-checked`, paired with a hidden native input that is `aria-hidden` and untabbable. The snapshot skips the mirror and treats the button as the control, so the answer lands where the page is actually looking |
| **A wizard that renders slowly** | Waiting is adaptive, not a fixed sleep: after a click the page is polled every 100 ms until it changes and then holds still for two polls. A step that renders at once costs ~300 ms instead of the whole budget; one that takes 1.5 s is still waited for. The quiet polls matter — frameworks routinely render an entry and immediately re-render it, and returning on the first sign of change reads a half-built page |
| **A job that no longer exists** | LinkedIn does not say "closed" for a posting that was taken down; it serves *"Job id provided may not be valid or the job posting has been removed"* on a page with no form at all. That is recorded as closed and the job drops out of future scrapes, instead of the agent asking you to paste a URL for a job nobody can apply to |
| **Work history the model would otherwise re-derive** | Employment entries come from the `jobs` key, matched to the form's entry blocks by title and company. An entry the site pre-filled with an employer your profile does not name is left completely alone |
| **A step that is still drawing** | Nothing left to fill and no Next or Submit on screen is what a page looks like mid-render, so before asking the model about it the agent gives the page 1.5 s. 15 of 58 real model calls were about zero fields, 7 s each; on Indeed's contact step the Continue button arrived while the model was being asked to find it. A page that really needs the model still gets it, 1.5 s later |
| **A control that cannot take the action** | Before any click or fill, the agent gives the control 1.5 s to be there, enabled and visible, instead of the 10–15 s each action would otherwise wait. A box the page re-drew used to take **22 s** to fail — the click's 10 s and then four fallbacks at 3 s each, all looking for a node that no longer existed — and now fails in 2 s with *"the form re-drew it"*; the next pass reads the page again and finds it under its new id. A button under something the page left behind is clicked through in under 2 s instead of 10, and a click under an overlay the page raised on purpose is still refused, just as quickly |
| **A form bigger than one model call** | Every control on the page is read — up to 250, a guard against pathological pages, where it used to be 60. The largest real forms met so far are SuccessFactors at 151 and UltiPro at 142, and past 60 the profile, the answer bank and the sweep could not see them either. The model is still shown at most 60 per call, but the ones it still has to decide come first: empty fields, then buttons, then — only if there is room — what is already filled. The rest follow in the next call |
| **A dropdown that does not list your answer word for word** | The nearest honest option is taken, and every such pick is logged as `[best match]` with its reason. Rules first: a level is never overstated (Fluent becomes Advanced when that is the best on offer, never Native), the option that is your answer's first words is its broad heading (Computer Science becomes Computer), and short degree forms are read (B.Tech is a Bachelors; on a list that spells degrees out it is Bachelor of Technology, and with several subjects and none of them yours, nothing is guessed). What the rules cannot settle goes to one call per page (`claude.pick_model`, Sonnet at medium effort by default), which must copy an option from the list word for word and rate it exact, close, loose or none. Only exact and close picks are entered, and each is remembered for the next form with the same answer and options. "Other", "No Selection" and "Prefer not to say" are never chosen this way, and legal or personal questions (gender, work authorisation, sponsorship) never reach it. The call is never made with an empty list. A list whose rows carry no option markup is read by where it opened: short, alike items just under the box, one of which names what was typed. A list with no readable text at all goes with a picture instead (see *Pictures for the model*) |
| **You answer on the page instead of in the chat** | When the agent asked about a choice and you picked it in the browser, the option you picked is saved for that question ("Saved your choice 'Yes' for ..."), so the next form asking it is answered for you. Never for sensitive topics (gender, citizenship, work authorisation), which are saved only when you say so |
| **A job page whose "Apply Now" is a link to the form** | On the first page of a session, with nothing on it to fill, an "Apply" / "Apply Now" / "Apply for this job" link outside any form is followed to its address, with no model call. The agent goes to the address rather than clicking, so it is a plain page load and nothing can be sent. A link to `#` or `javascript:` is not followed, and neither is any Apply *button*: on some job boards that button is a one-click apply. On Infor's job page this replaced a 21-second model call whose click on the link was refused as submit-worded, which left only you able to open the form |
| **An application inside the employer's page (iCIMS in an iframe)** | The agent stays with the form's frame while it holds a form at all. Before, a control appearing or disappearing (a dropdown's search box, the page's own header search) swapped it back to the employer's page, and you were told to click its "Submit a Ticket" help link. A site search box no longer counts as the page having a form. iCIMS's "Apply for this job online" link in the frame is followed in the frame, with no model call. A dropdown drawn as a link (`<a role="combobox">`) is read as a dropdown, and the search box inside its list is not a field of its own. A box marked `autocomplete="tel-national"` gets the number without its country code |
| **Several ways to apply (Workday's Autofill with Resume / Apply Manually / Use My Last Application)** | Asked at once, without a model call. Type the option's name and it is remembered per form system (Answers tab: "Way to apply on Workday") and chosen for you from then on |
| **A page that comes up with nothing on it** | The agent says which of five things it is, each with its own fix: the page could not be read, the tab is blank, the site is still on its loading spinner, the page's content never arrived, or it is a job description whose form sits behind an Apply button — named by what the button says, and only when there is one. For "never arrived" it names the data requests that were refused (`401 api.example.com/getJobDesc`): a site whose sign-in is stale in the agent's Chrome profile draws its header and none of the job. Signing out of that site and back in, in the agent's window, or clearing its data there, fixes it |

#### Pictures for the model

The model reads the form as text: each box's label, options and value. When that text falls short, or the page visibly says something went wrong, the next model call also gets a **cropped picture** of the boxes concerned. Each crop shows the box, its label, and the message under it, with a margin on every side. It is never the whole screen.

| When | What is pictured |
| --- | --- |
| A box turns its value down with a message of its own ("25 LPA" into a digits-only CTC box became "25" with a red *Invalid input*) | the box and the message. The box is emptied, the refusal goes to the model with the message in words, and the model fills it again in the form the box wants. The same value is never typed again |
| A dropdown refused the answer and is still empty | the dropdown, opened on the refused answer, so its options are in the picture. Not when the refusal already listed the options as text: the model has them word for word, and opening the list would only cost a second |
| A dropdown's options could not be read as text at all | the list, opened on the refused answer, goes to the per-page dropdown call (below). It reads the options off the picture and names the row; the row is taken with the keyboard, and the box must then show exactly that option, or the choice is taken back out. If the rows turn out to be readable once the list is open, they go to that call as text and no picture is taken. If the call declines, or the row does not go in, the planning call gets the same picture |
| A box with no question words of its own (an empty label, "Select One", a question drawn by the page's styling) | the box and what surrounds it |
| Next did not move the form on | each box the page marks invalid, with its message. A box with an error message of its own is emptied and its value is not typed again. One without (the start date in "end date before start date") keeps its value. Either way it goes to the next model call before you are asked, even when it still holds a value; a second failed Next sends no second picture, and the model is told it is the same field |
| The model is unsure what a field asks | the model plans a `look` at it and gets the picture in its next call |

**One picture per question.** A Yes / No question is two fields, one per option, and each used to be pictured separately: the same question twice ("Are you located in Vadodara?" on Indeed). Now the question is one crop with all its options in it, named by the question, and its line in the prompt names every option's field id. The same goes for the model's `look`: a look at Yes and then at No is one picture.

Nothing is pictured for boxes your profile or saved answers filled, a page where everything was resolved, or a native `<select>`. A native select's options are all in the text already, and its open list is drawn by the operating system, not the page. A call carries at most four pictures; any more wait for the next call, and pictures a failed call was to carry go with the retry. A picture of a field answered since it was taken is not sent. Each question is pictured once per session unless it refuses something new. A crop costs about 1,000 input tokens; in testing that added no measurable time to a call. The log says `Sending N picture(s) of the form with it`.

**Enter in a dropdown never sends the form.** The agent sometimes presses Enter to commit a dropdown row. With no row highlighted, a browser passes that Enter to the form, by clicking the form's default button and sending it. For the length of each such keypress the agent holds both off, and if Enter tried to reach the form, the pick is refused instead.

Every picture sent is kept under `dumps/screenshots/<company>/` (gitignored), so you can see what the model saw. Nothing deletes them yet: clear old folders by hand.

#### What a session leaves behind

Each apply session gets its own folder, `dumps/apply_dumps/<company>/<date-time>_<job title>/` (gitignored):

| File | What it holds |
|---|---|
| `log.jsonl` | every line of the chat transcript as it happened, with the time. Your reply to a question about a code, OTP or password is written as `[hidden]` |
| `summary.json` | written when the session ends: outcome, seconds taken, model calls (attachments included), questions you were asked, the system (Workday, Greenhouse…) and the last page |
| `dom_<date-time>/` | one per `dump` (below) |

The transcript used to live only in memory and was lost when the server restarted. Nothing deletes these folders yet.

#### The form catalogue

`localData/form_catalogue.json` (gitignored) keeps a **shape-only** record of the forms you meet, filed by applicant tracking system — Workday, Phenom, Greenhouse, Lever, SuccessFactors, iCIMS, Taleo, Ashby, SmartRecruiters, Talentrecruit, UKG (UltiPro), Rippling, Jobvite. Shape means label, tag, type, section, role and required-ness. Every value and every piece of text is stripped before anything is written, because the live snapshot holds your employers, dates and contact details.

The system is named from the hostname when it says (`*.myworkdayjobs.com`), and otherwise from where the page loads its scripts and styles — a Phenom site on the employer's own domain names itself only by loading from `cdn.phenompeople.com`. The transcript says which system a form runs on (`This form runs on UKG (UltiPro).`) once per session, since it is the first thing worth knowing about a form that misbehaves.

It is **not read during an application**. What it stores is what the live page already shows at fill time, so a lookup would add nothing, and acting on a stale shape is how you fill the wrong box. It is a record of every form you have met — not only the ones you dump — with nothing personal in it. Delete the file any time; it rebuilds as you apply.

**The answer bank.** Any question you answer in chat is remembered in the `known_answers` table of `localData/job_history.db`, keyed by topic so every phrasing of "notice period" is one entry. Questions that share a topic but not an answer are kept apart: work authorisation or sponsorship for another country than yours, each way of working (remote, hybrid, on-site), a background check and a conviction, needing a sponsor and holding a visa, relocating and relocation assistance, serving notice and the notice period. A saved yes is never used for a question that wants a value ("Preferred work mode"), and a saved link must look like one. Next application, it's filled automatically: neutral answers silently (logged as `[saved]`), legal/eligibility answers only after you confirmed "remember this?" once — and every reuse prints a visible `[saved] question -> answer` line. OTPs, passwords and captchas are never stored, and neither is anything mentioning the specific company. Fix a wrong entry any time from the user menu → **Saved answers**: every entry with how often it was used, a search box, **Edit** (the same secret check applies) and **Delete** (the agent asks again next time a form wants it).

**Attachments are built when the form asks for them**, never ahead of time — a form that never wants a resume or cover letter costs nothing.

- **Resume.** The moment an upload field is detected, the tailored `.tex` is compiled (needs a LaTeX toolchain such as MiKTeX on PATH) and a dialog offers the **tailored PDF** or your **default resume** (`resume.local_path`, served via `/api/resume/default.pdf`), with the model's edit changelog and links to view both. *Edit source* reveals the tailored LaTeX for small fixes: it is validated, compiled and re-measured for one page before use, and a broken edit is rolled back with the error shown. The choice is remembered for the rest of the session.
- **Cover letter.** When a cover-letter field is detected, the model drafts a short one (~3 paragraphs, plain voice) and the dialog opens with it in an editable box. Edit it freely — that costs nothing — or *Ask for changes* to have the model revise it (one call each). On accept, a textarea gets the text and a file input gets a small compiled PDF written to the run folder. Letters are never reused across jobs and never stored in the answer bank.
- **If detection misses the field**, the **Attach resume** and **Cover letter** buttons beside the chat box (or typing `attach resume` / `cover letter`) start either flow by hand. *Skip* in either dialog leaves the field untouched.

What happens:

1. A visible Chrome opens on the apply URL, using the persistent profile at `localData/chrome-profile`. Log into the job site once yourself; the session is remembered. On an employer's own site the agent waits and asks you to type `done` when the page is ready, so you can clear a cookie wall or a region picker first.
2. **On a LinkedIn job it does not ask.** LinkedIn builds the job card after the page reports itself loaded, so the agent watches for whichever of three things arrives first — an apply control, a sign-in wall, or a closed banner — and each one it then handles itself. A posting that stopped accepting applications is recorded as closed and the session ends **without asking you anything**, instead of making you press a key to be told a job is dead. Otherwise it opens the apply flow: **Easy Apply** jobs get the in-page modal; **Apply on company website** jobs open the employer's form in a new tab, which the agent follows (the log shows `Switched to <url>`). If none of the three appears — a consent wall on a fresh profile, or something LinkedIn has changed — it hands back and asks rather than guessing. LinkedIn's button is routinely on screen before it works: in 25 of 26 real openings the first click did nothing, so a click that opens nothing is retried after 1.5 s rather than 4, and an employer's tab is read as soon as it has loaded rather than after a fixed 3 s.
   **An Indeed job does not ask either, and needs no model to start.** Asked instead, "Ready to start?" once sat unanswered for three minutes with the Apply button in plain view, and the model was then paid 9 s to find the button. The agent now presses the job's own button itself, found by the id Indeed gives it, or in Indeed's newer layout by the job header's apply block (an **Apply now** link) — **Apply with Indeed** opens Indeed's form, **Apply on company site** opens the employer's in a new tab — so an Apply belonging to some other job on the page is never the one pressed. If neither button shows within 10 s, the page is read as it stands. On Indeed's **Add a resume** step — two cards, *Build an Indeed Resume* and *Upload a resume*, whose radios and file input are all hidden behind them — the resume you choose goes straight into the hidden upload: no system file dialog, and *Build an Indeed Resume* is never touched. Any page that says "Upload a resume" / "Upload your CV" over a single hidden document input is handled the same way. So is the same step when your Indeed account already holds a CV from an earlier application: it shows that old CV pre-selected under **Add a resume**, and the resume you choose for this job replaces it, so the old one never goes out by default.

   **No other site asks either.** Most Indeed and many LinkedIn jobs link straight to the employer's own system (Workday, Phenom, Greenhouse…), and every one of those used to stop on "Ready to start? Type done" before anything happened. The agent now reads the page as soon as it loads: a page still drawing is waited for, a sign-in wall is named and handed to you, and a page that never shows a form is handed back with the reason.
3. The resolver fills what it can, the model plans the rest, and the agent stops and asks in the chat pane for anything unknown: captchas, consent and legal questions. A code sent to you (an OTP, "the 6 digit code sent to your email") or a password is never asked for in the chat: the agent tells you to type it into the browser and carries on once the page moves on. Answer, or handle it in the browser yourself and type `done`. Type `skip` to leave a field alone, paste a URL to send the agent there, `abort` to stop.
4. **The agent never clicks submit — you do.** When everything is filled it says so and waits; you review the form, click Submit in the browser yourself, and type `done`. This is enforced in code (a submit click raises), not just prompted.

   **Which button is which.** Words alone cannot tell a step's Continue from the one that sends everything, or a job page's Apply from the Apply at the end of a form. So at the end of each step, when a Next-like button or a submit-type step button is on screen, one quick model call labels the page's buttons — *next*, *final*, *opens the form*, or *other* — looking at the page around them: a step counter ("Step 4 of 4"), the headings ("Review your application"), a declaration, how much of the form is filled. The log says what it decided: `[buttons] 'Continue' next; 'Back' other (1.8s)`. The model can only make a click **safer**:
   - words that send an application — **Submit, Send, Complete, Finish** — stay yours whatever the model says;
   - a button the model calls *final* is never clicked, even a plain Continue; you are told why and asked to click it;
   - a submit-type step button (iCIMS's Continue, EPAM's Next) the model is sure is *next* is clicked like any Next, after you have reviewed the step;
   - when the model is unsure, or the call fails, the old rules stand.
   A plain Submit with nothing unclear beside it costs no call. Your review of every step is unchanged: the agent still stops at each filled step until you type `next` or click it yourself.

   The call has its own model (`claude.buttons_model`, Sonnet at low effort): on ten saved pages with known answers it took 3.9 s typically and 9 s at most, got 7 right and was unsure — so changed nothing — on the other 3. Haiku was slower and called a single-page form's final Apply "next" at 0.95, so it is not used. To click a submit-type step button the model must be at least 0.85 sure, and never on what it reads as the last page or a page mentioning a review, declaration or signature.

   **Add, Remove and Upload never send the form.** A `<button>` with no type inside a form submits it when clicked — "Add another" written that way could have sent an application. The page reader now records whether a button would send its form, and when the agent clicks one that is not the step's Next (Add, Remove, an upload tile, a Yes/No toggle) the form's submission is held off for the click: the page's own Add still adds and Upload still opens its picker. Every Enter the agent presses in a typeahead goes through the same hold.

### The queue

Tick the box on any number of shortlist rows and press **Start queue**: the agent works down them in order, opening the next as soon as the previous one is finished with.

It is worth being exact about what this does and does not do. **It does not reduce how often you are needed.** The agent never submits, so a queue of five is still five forms you read and five Submit buttons you press yourself. What it removes is everything either side of that: walking back to the table, finding the next row, pressing Start apply, and waiting for Chrome.

Strictly one job at a time, and that is a constraint rather than a choice. Chrome allows one instance per user-data-dir, and the persistent profile holding your logins is a single directory; Playwright's sync API is thread-affine on top of that. So the next job starts only once the previous browser has actually closed. After a submitted application that is at once: the page's confirmation sentence is already in the transcript, so the window is not held open.

Two ways out of a job, and they are opposites:

| | What it does |
| --- | --- |
| **Park** (or type `park`, `later`, `skip job`) | leaves **this** application and starts the next queued job. Nothing is submitted and nothing is recorded: the job keeps its place in the shortlist and stays eligible for a later scrape. This is the answer to a form that wants an employer account and an email verification before it will show you anything |
| **Abort** (or type `abort`, `stop`, `cancel`) | stops this application **and the rest of the queue** |

Only the bare word counts, so an answer that happens to contain it — a location of "Cyber Park, Bangalore" — is typed into the form like any other text.

A job that simply fails does not stop the queue; only `abort` does. Neither does a browser that would not open, but that one stops it deliberately: whatever blocked this job almost certainly blocks the next, so the queue halts and says why rather than churning through the rest. **Clear queued** drops what is still waiting without touching the job in front of you.

**There is no cap on the length, deliberately.** How many applications you can read properly in one sitting is your judgement, and a number picked in the code would only be a guess dressed as a rule. But know what you are trading: the walk back to the table is currently what makes you look properly at each form, and a queue removes it. The count beside the button is there so the length is a number you chose rather than one you drifted into.

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
| `done` | the page is ready, or you submitted it yourself (`applied`, `submitted`, `finished`, `ok`, `continue`, `ready`) — at a hand-off prompt only; at a question about one box, a reply is typed into that box |
| `i submitted` | record the application as applied and end the session, **from any prompt** — the way out when a site asks for an emailed code after the form has gone in, where `done` would be typed into the code box (`i applied`, `mark applied`, `already applied`, or the **I submitted it** button) |
| `skip` | leave the current field empty (`leave it`, `leave blank`, `ignore`, `no answer`) |
| `yes` / `no` | confirm or refuse a click, checkbox or radio the agent proposes — short replies only, so a sentence containing "yes" is guidance, never consent |
| `llm: <instruction>` | draft or redraft the answer to the question being asked; at either review prompt ("This step is filled in" or "click Submit yourself"), `llm: <question>` drafts an answer to a question the form asked — into the box when it can be found, otherwise handed back for you to paste |
| `redo` / `redo <words>` | reopen an answer you already gave, pre-filled, to edit or redraft |
| `attach resume` / `cover letter` | start either attachment flow by hand (same as the buttons) |
| `dump` / `dump 10` | save the page as it is (DOM with shadow roots and live values, every frame, the field snapshot, the page's data requests with their status codes, a screenshot) into the session's folder under `dumps/apply_dumps/` and keep waiting; `dump 10` waits ten seconds first so you can open a widget. The **Dump** button does the same and copies the folder path to your clipboard. Both work while the agent is busy too — during a model call the page is saved within a second, as it was when you asked |
| a URL | open that page when the agent is stuck |
| `retry` | try the model again after an outage (`try again`) |
| `closed` | record that the posting no longer accepts applications and stop |
| `park` | leave this application for later and start the next queued job; nothing is submitted or recorded (`later`, `skip job`) |
| `abort` | end the session, and the rest of the queue (`stop`, `cancel`) |

OTPs and other secrets are held in memory for the session and never written to disk. The agent will not invent visa status, salary, or legal answers: anything that reads like a legal or eligibility declaration (consent, terms, work authorisation, citizenship, background checks, demographics) is gated — answered from your confirmed saved answer with a loud log line, or asked. For radio groups the gate acts only on the option that matches your answer, so a saved "No" can never tick the "Yes" box. A yes/no you type is parsed on whole words with negatives winning — "I don't agree" leaves the box unticked. There is no captcha solving and no unattended mass-apply.

## Daily schedule

The web server runs a batch once a day (`src/daily.py`), at `daily.at` (11:30 by default, your local time) — or, if the server was not running then, as soon as it starts later that day. The day it last ran is kept in the database's `properties` table, so a restart never runs it twice; a batch cut short by a stop picks up where it was.

```yaml
daily:
  enabled: true
  at: "11:30"
  open_check: true        # 1. are waiting jobs still open?
  check_every_days: 2
  max_checks: 60          # pages per batch, oldest-checked first
  check_gap_seconds: 4
  search_run: true        # 2. a search over the last 24 hours
```

1. **Still open?** Half the jobs acted on in the first two big runs had closed by the time Start apply opened them, most after a resume had been tailored for them. So every shortlisted job you have not acted on is opened again on day 2, 4, 6… while it waits, one page every few seconds, in a headless browser of its own (never the apply sessions' Chrome profile). LinkedIn's own "No longer accepting applications" marks the job **closed**, as Start apply does. Closed or not-found wording on any other page only marks the row **looks closed?** for you to confirm with its Closed button — an employer's page is worded a thousand ways, and a wrong guess would hide a live job. A sign-in wall or a "verify you are human" check is left alone and tried again next time; nothing is ever solved or worked around.
2. **A search over the last 24 hours**, with your saved run settings, so new jobs are applied to while they are fresh. If a run of yours is already going, today's is skipped.

The line beside the run picker says when it runs next or what it last did; its log is `dumps/daily_batch.log`. It starts only with the real server (`python -m src.web`, not `--reload`), never when a check imports the app. `python -m src.cli run` is still fully non-interactive, for any other scheduler.

## Out of scope

Unattended mass-apply, captcha solving, LinkedIn login automation, Overleaf API, email, Google Sheets, Notion.
