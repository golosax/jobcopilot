# JobCopilot

Walks every job on a LinkedIn search-results page, scores each against your CV
with Claude, and for every job scoring `>= threshold` (default 70%) saves a
record to `output/matches.json` and generates a tailored English cover-letter PDF.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in LINKEDIN_EMAIL / LINKEDIN_PASSWORD
cp config.example.json config.json   # then fill in your name/contact/search URL/prompts
```

`config.json` and `data/` (your CV, browser session) are gitignored — they hold
your personal info and never get committed.

### LLM auth (no API key)

The tool calls Claude through the **Claude Code CLI** using your **Claude
Pro/Max subscription** — no `ANTHROPIC_API_KEY` and no per-token billing.
Make sure the CLI is installed and logged in:

```bash
claude --version      # should print a version
claude                # run once, then /login and pick your subscription
```

`src/llm.py` shells out to `claude -p ... --output-format json`. Override the
binary path with the `CLAUDE_CLI` env var if needed.


The CV text lives in `data/cv.txt`. To regenerate it from a PDF:

```bash
pdftotext -layout CV_YourName.pdf data/cv.txt
```

## Configure

Edit `config.json`:

| key | meaning |
|-----|---------|
| `search_url` | LinkedIn jobs search-results URL |
| `match_threshold` | minimum score (0-100) to save a match |
| `min_delay_sec` / `max_delay_sec` | random pause between jobs (anti-ban) |
| `max_jobs_per_run` | cap per run (`0` = no cap, walk everything) |
| `claude_model` | Anthropic model id |
| `headless` | run Chrome headless |

## Run

### Backoffice (recommended)

```bash
.venv/bin/python webapp.py      # open http://127.0.0.1:5000
```

The config page lets you edit everything without touching files:
- **Name, location, email, LinkedIn** (printed on the cover-letter header; name is also used in the filename, e.g. `CL_YourName_Company.pdf`)
- **Search URL** and **match threshold**
- **Additional points** — free text summed with the CV when matching
- **Prompts** for matching, cover letter, and the "why I'm a good fit" note
  (placeholders `{cv} {title} {company} {description}`)
- **CV upload** (pdf/docx/txt) — extracted to text once and reused, so PDF/DOCX
  parsing never costs tokens on repeat runs
- **Start analyzing** — launches the Selenium run in the background with a live log
- **Matches** page — table of `matches.json` with links to each cover-letter PDF
  and why-fit note
- **Visited** page — every scanned company/position; remove one or remove all

### CLI

```bash
.venv/bin/python main.py
```


First run opens a real Chrome window and logs in. If LinkedIn asks for 2FA /
a checkpoint, complete it manually in that window — the session is persisted in
`data/browser_profile/`, so later runs skip it. Every processed job's URL is
recorded in `output/visited.json`; on the next run those cards are skipped
**before** clicking or calling Claude, so a crashed/interrupted run resumes
without re-reviewing or re-sending anything. A job is only marked visited once
it has been scored, so failures (e.g. a rate limit) are retried next time.

## Output

`output/matches.json`:

```json
[
  {
    "url": "https://www.linkedin.com/jobs/view/4453655475/",
    "position": "Senior Software Engineer (m/w/d)",
    "company": "SomeCompany",
    "cover_letter": "SomeCompany/CL_YourName_SomeCompany.pdf",
    "score": 78
  }
]
```

Each match's docs are grouped under `output/<Company>/`: the cover-letter PDF
(`CL_<Name>_<Company>.pdf`), the short "why I'm a good fit" note (≤400 chars,
`WHY_<Name>_<Company>.txt`), and CV tailoring suggestions
(`CVTIPS_<Name>_<Company>.txt`).

## Note

Automating LinkedIn violates their Terms of Service and carries account-ban
risk. The delays reduce, but do not eliminate, that risk. Use at your own
discretion.
