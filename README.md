# JobCopilot

Walks every job on a LinkedIn search-results page, scores each against your CV
with an LLM, and for every job scoring `>= threshold` (default 70%) saves a
record to `output/matches.json`. Cover letters are **never generated
automatically** — you trigger them per match, on demand, from the backoffice.
The job description is saved for **every** scanned job, match or not, so
low-scoring calls stay auditable later.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in LINKEDIN_EMAIL / LINKEDIN_PASSWORD
cp config.example.json config.json   # then fill in your name/contact/search URL/prompts
```

`config.json` and `data/` (your CV, browser session) are gitignored — they hold
your personal info and never get committed. `output/` (all generated matches,
cover letters, JD dumps) is gitignored too.

### LLM auth (no API key)

The tool talks to an LLM through a CLI, authenticated with your existing
subscription — no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` and no per-token
billing. Two backends are supported, selected via `llm_backend` in config
(`"claude"` default, or `"codex"`):

```bash
# Claude Code CLI (Claude Pro/Max subscription)
claude --version      # should print a version
claude                # run once, then /login and pick your subscription

# Codex CLI (ChatGPT Plus/Pro/Business/Edu/Enterprise plan)
codex --version
codex login            # sign in with ChatGPT, not an API key
```

`src/llm.py` shells out to `claude -p ... --output-format json`
(override the binary with the `CLAUDE_CLI` env var); `src/llm_codex.py` shells
out to `codex exec ...` and explicitly refuses to run unless `codex login
status` reports ChatGPT auth, so it can't silently fall back to billed API
access.

The CV text lives in `data/cv.txt`. Upload a PDF/DOCX/TXT through the
backoffice's CV card (parsed once and reused, so re-runs never re-spend tokens
on parsing), or drop the text in directly:

```bash
pdftotext -layout CV_YourName.pdf data/cv.txt
```

## Configure

The backoffice's Config page (`http://127.0.0.1:5000`) is the intended way to
set everything up for a new install — every field below is editable there,
with inline hints. Manually editing `config.json` works too; the full key
reference:

| key | meaning |
|-----|---------|
| `search_url` | LinkedIn jobs search-results URL |
| `candidate_name` | your name — used in generated filenames (`CL_YourName_Company.docx`) and the cover-letter header |
| `candidate_location` / `candidate_email` / `candidate_phone` / `candidate_linkedin` | printed on the cover-letter header, only the non-empty ones |
| `match_threshold` | minimum score (0-100) to save a match |
| `min_delay_sec` / `max_delay_sec` | random pause between jobs (anti-ban) |
| `max_jobs_per_run` | cap per run (`0` = no cap) |
| `max_pages` | cap on search-result pages walked (`0` = all) |
| `llm_backend` | `"claude"` or `"codex"` |
| `claude_model` | Claude model id, used when `llm_backend` is `"claude"` |
| `codex_model` | Codex model id, used when `llm_backend` is `"codex"` (blank = CLI default) |
| `headless` | run Chrome headless |
| `chrome_version` | pin a Chrome major version for `undetected-chromedriver` (blank = auto-detect) |
| `global_style_notes` | style rules appended to **every** AI call (matching, cover letter, why-fit, CV suggestions) |
| `match_notes` | your editable instructions appended to the matching prompt (fallback — see `data/matching_notes/` below) |
| `cover_letter_notes` | your editable instructions appended to the cover-letter prompt |
| `why_fit_notes` | your editable instructions appended to the why-fit prompt |
| `cv_suggestions_notes` | your editable instructions appended to the CV-suggestions prompt |

### Matching guidance / `data/matching_notes/`

Beyond the free-text `match_notes` field, you can drop one or more `.txt`
files into `data/matching_notes/` (e.g. ranking-priority instructions — "treat
AI-agent roles as top priority", "skip anything requiring German B2+"). When
that directory has files in it, **their combined content replaces
`match_notes` entirely** for matching (`settings.effective_match_notes()`) —
the field becomes a fallback used only when the directory is empty. This
content is appended to the **matching prompt only**, alongside `match_notes` —
it is never folded into the CV text, and never reaches the cover-letter,
why-fit, or CV-suggestions prompts (leaking ranking rules into those made the
model narrate its own instruction-following into the generated text). The
backoffice's Config page shows which mode is active and lists the files under
Job matching → Your instructions.

Each task has a fixed, read-only **system prompt** (holds the
`{cv} {title} {company} {description}` placeholders and required output
structure) and your own editable **notes** field appended at send time; both
are visible/editable on the Config page's Prompts card, for all four tasks:
matching, cover letter, why-fit, CV suggestions.

## Run

### Backoffice (recommended)

```bash
.venv/bin/python webapp.py      # open http://127.0.0.1:5000
```

- **Config page** — everything in the table above, CV upload, and the four
  tasks' prompts
- **Start analyzing** — launches the Selenium run in the background with a
  live log
- **Matches page** — table of `matches.json`: score (sortable), position,
  company, a link to the saved **JD**, **Generate CL** (disabled until a JD
  is saved for that match; becomes "open" + "regenerate" once a cover letter
  exists), **Why fit**, **CV suggestions**, and an **Applied** checkbox
  (saved instantly via `fetch()`, no page reload)
- **Visited page** — every scanned company/position with a link to its saved
  **JD**; remove one or remove all

Restart the process to pick up code changes — it runs with `debug=False`, so
there's no auto-reload.

### CLI

```bash
.venv/bin/python main.py
```

First run opens a real Chrome window and logs in. If LinkedIn asks for 2FA /
a checkpoint, complete it manually in that window — the session is persisted in
`data/browser_profile/`, so later runs skip it. Every processed job's URL is
recorded in `output/visited.json`; on the next run those cards are skipped
**before** clicking or calling the LLM, so a crashed/interrupted run resumes
without re-reviewing or re-sending anything. A job is only marked visited once
it has been scored, so failures (e.g. a rate limit) are retried next time.

### Manual matching (no browser, no LinkedIn)

To sanity-check the match prompt/score against a job description you already
have, without running a scan:

```bash
.venv/bin/python -m src.matcher
```

- First run: creates `data/manual_jd.txt` (empty) and exits, telling you where
  to paste the job description.
- Paste the job description into that file (not into the terminal — a large
  paste there can trip bracketed-paste/shell warnings) and save it.
- Run the command again: it prints the exact composed prompt (CV +
  `data/matching_notes/` or `match_notes` + `global_style_notes`)
  and the resulting `{"score", "matched", "missing", "reason"}` — the same
  `score()` code path a real run uses, so what you see here is what the real
  run would send and get back.

## Output

`output/matches.json`:

```json
[
  {
    "url": "https://www.linkedin.com/jobs/view/4453655475/",
    "position": "Senior Software Engineer (m/w/d)",
    "company": "SomeCompany",
    "cover_letter": "SomeCompany/CL_YourName_SomeCompany.docx",
    "why_fit_file": "SomeCompany/WHY_YourName_SomeCompany.txt",
    "cv_suggestions_file": "SomeCompany/CVTIPS_YourName_SomeCompany.txt",
    "jd_file": "SomeCompany/JD_YourName_SomeCompany.json",
    "score": 78,
    "applied": false
  }
]
```

`cover_letter` is `null` until you click "Generate CL" on the Matches page —
it's never populated by the scan itself.

Each match's docs are grouped under `output/<Company>/`:
- **Cover letter** — `CL_<Name>_<Company>.docx`, generated on demand
- **Why I'm a good fit** — `WHY_<Name>_<Company>.txt` (≤400 chars)
- **CV tailoring suggestions** — `CVTIPS_<Name>_<Company>.txt`
- **Job description** — `JD_<Name>_<Company>.json`: the raw JD text plus the
  scoring result (`score`/`matched`/`missing`/`reason`) and a timestamp,
  saved for **every** scanned job (match or not), viewable from either the
  Matches or Visited page

Filenames are collision-safe (`_2`, `_3`, ... appended) if two jobs at the same
company would otherwise produce the same name.

`output/visited.json` mirrors every scanned job (`url`, `company`, `position`,
`jd_file`) — this is the skip-list a re-run checks against, and what drives
the Visited page's JD link.

## Note

Automating LinkedIn violates their Terms of Service and carries account-ban
risk. The delays reduce, but do not eliminate, that risk. Use at your own
discretion.
