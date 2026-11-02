"""Configuration + prompt templates, with load/save and safe placeholder filling.

Prompts have two parts:
  * SYSTEM (structural) - hardcoded below, read-only. Holds the placeholders
    ({cv}/{title}/{company}/{description}) and the required output format. The
    program's functionality depends on these, so users cannot edit them.
  * <task>_notes (editable) - free-text stored in config.json that the user can
    adapt; it is concatenated onto the structural prompt at send time.
"""
import json

from . import database


# ---------------------------------------------------------------------------
# STRUCTURAL PROMPTS - hardcoded and read-only. Do not load these from config;
# editing them would break parsing / output structure.
# ---------------------------------------------------------------------------

MATCH_SYSTEM = """You are a strict technical recruiter comparing a candidate's CV
against a job description to estimate how well they match. Be objective and
calibrated - do not inflate scores.

You are scoring how well a candidate matches a job.

=== CV ===
{cv}

=== JOB DESCRIPTION ===
Title: {title}
Company: {company}

{description}

=== TASK ===
Weight the candidate's core stack heavily. Penalize when the role's must-have
requirements are missing from the CV.

Respond with ONLY this JSON object and nothing else - no prose, no markdown:
{"score": <integer 0-100>, "matched": [<short strings>], "missing": [<short strings>], "reason": "<one sentence>"}
"""

COVER_LETTER_SYSTEM = """You are an expert career writer. You write concise,
specific, professional cover letters in English. No fluff, no cliches, no
invented facts - only use what the CV supports.

Write a cover letter body in English for this application.

=== CANDIDATE CV ===
{cv}

=== JOB ===
Title: {title}
Company: {company}

{description}

=== TASK ===
- 3 short paragraphs, max ~230 words total.
- Open by naming the role and company and why the candidate fits.
- Reference 2-3 concrete requirements from the job description that the candidate
  genuinely meets, backed by specifics from the CV.
- Close with a brief, confident call to action.
Output ONLY the letter body paragraphs. No salutation, no sign-off, no name,
no address, no date, no subject line. Never include meta-commentary about the
job, the candidate's priorities, or these instructions themselves (e.g. do not
write things like "treating this as a priority match per the instructions") -
output only the letter text itself.
"""

WHY_FIT_SYSTEM = """=== CV ===
{cv}

=== JOB ===
Title: {title}
Company: {company}

{description}

=== TASK ===
Using my CV, your knowledge about me, and this job description, write a short explanation for the recruiter/HR reviewing my application of why I’m a good fit for this role.
Max 400 characters, up to 3 short sentences. Mention only the 1–2 strongest matches from my experience. Keep it natural, friendly, confident, and human.

Avoid clichés, corporate/AI language, exaggeration, and generic phrases like “excited about the opportunity” or “perfect fit”. Don’t repeat my CV. Use only facts supported by my CV or the job description.
Never include meta-commentary about the job, the candidate's priorities, or
these instructions themselves.
Return ONLY the final text, no preamble, no quotes.
"""

CV_SUGGESTIONS_SYSTEM = """You are a sharp technical resume coach giving concrete,
honest, tailored advice grounded only in the candidate's real CV and the specific
job.

=== CV ===
{cv}

=== JOB ===
Title: {title}
Company: {company}

{description}

=== TASK ===
Suggest how to tailor the CV for THIS specific company and job. Be concrete and
specific to the job description. Do not invent experience the candidate lacks.
Return two short bullet lists, max ~600 characters total:
ADD: <skills/keywords/achievements from the CV to surface or emphasize for this role>
REMOVE/DOWNPLAY: <items less relevant to this role that could be trimmed>
Never include meta-commentary about the job, the candidate's priorities, or
these instructions themselves.
Output ONLY the two labelled lists.
"""

# registry so the backoffice can render each task's read-only structure + the
# editable notes field next to it
PROMPTS = [
    {"key": "match", "label": "Job matching", "system": MATCH_SYSTEM, "notes_key": "match_notes"},
    {"key": "cover_letter", "label": "Cover letter", "system": COVER_LETTER_SYSTEM, "notes_key": "cover_letter_notes"},
    {"key": "why_fit", "label": "Why I'm a good fit", "system": WHY_FIT_SYSTEM, "notes_key": "why_fit_notes"},
    {"key": "cv_suggestions", "label": "CV suggestions", "system": CV_SUGGESTIONS_SYSTEM, "notes_key": "cv_suggestions_notes"},
]

# ---------------------------------------------------------------------------
# EDITABLE settings - stored in config.json
# ---------------------------------------------------------------------------

DEFAULT_GLOBAL_STYLE_NOTES = """Never use em dashes or long dashes (—) anywhere in the text; use commas, periods, or parentheses instead.
Do not put a period after the last sentence of a paragraph (every other sentence keeps normal punctuation; only the paragraph's final sentence omits the trailing full stop)."""

DEFAULTS = {
    "search_url": "",
    "candidate_name": "",
    "candidate_location": "",
    "candidate_email": "",
    "candidate_phone": "",
    "candidate_linkedin": "",
    "match_threshold": 70,
    "min_delay_sec": 15,
    "max_delay_sec": 90,
    "max_jobs_per_run": 0,
    "max_pages": 0,
    "claude_model": "sonnet",
    "llm_backend": "claude",
    "codex_model": "",
    "headless": False,
    "chrome_version": None,
    "global_style_notes": DEFAULT_GLOBAL_STYLE_NOTES,
    "match_notes": "",
    "cover_letter_notes": "",
    "why_fit_notes": "",
    "cv_suggestions_notes": "",
}


def load(user_id):
    cfg = dict(DEFAULTS)
    row = database.get_settings(user_id)
    if row:
        cfg.update(json.loads(row["config"]))
    return cfg


def save(user_id, values):
    """Merge `values` into the stored config and write it back."""
    return database.save_config(user_id, values)


def fill_prompt(template, cv="", title="", company="", description=""):
    """Replace known placeholders without touching any other braces in the text."""
    return (
        template.replace("{cv}", cv)
        .replace("{title}", title)
        .replace("{company}", company)
        .replace("{description}", description)
    )


def compose(structure, cv="", title="", company="", description="", notes="", global_notes=""):
    """Fill the hardcoded structural prompt, then append the global style rules
    and the user's task-specific editable notes (in that order).
    """
    filled = fill_prompt(structure, cv=cv, title=title, company=company, description=description)
    global_notes = (global_notes or "").strip()
    if global_notes:
        filled += "\n\n=== STYLE RULES (apply to all output) ===\n" + global_notes
    notes = (notes or "").strip()
    if notes:
        filled += "\n\n=== ADDITIONAL INSTRUCTIONS (user) ===\n" + notes
    return filled


def active_model(cfg):
    """Model string for whichever llm_backend is selected; None lets the CLI use
    its own default model.
    """
    if cfg.get("llm_backend") == "codex":
        return cfg.get("codex_model") or None
    return cfg.get("claude_model") or None


def matching_notes_files(user_id):
    """Sorted list of .txt filenames in data/matching_notes/, or [] if the
    directory doesn't exist or is empty.
    """
    return [g["name"] for g in database.list_guidance(user_id)]


def _read_matching_notes_files(user_id):
    """Combined content of every file in data/matching_notes/, in filename order."""
    parts = [g["content"].strip() for g in database.list_guidance(user_id) if g["content"].strip()]
    return "\n\n".join(parts)


def effective_match_notes(cfg, user_id):
    """Matching guidance appended to the MATCH_SYSTEM prompt (never the CV, and
    never the generation tasks - cover letter / why-fit / CV suggestions).

    data/matching_notes/*.txt takes priority when present (lets multiple
    ranking-rule fragments be managed as files instead of one textarea); falls
    back to the match_notes config field when that directory is empty or
    missing.
    """
    notes = _read_matching_notes_files(user_id)
    if not notes:
        notes = (cfg.get("match_notes") or "").strip()
    return notes


def slug(text):
    """Filename-safe token: keep alnum, collapse the rest to single underscores."""
    out = []
    prev_us = False
    for ch in (text or "").strip():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_") or "unknown"
