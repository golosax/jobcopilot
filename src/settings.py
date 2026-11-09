"""Configuration + prompt templates, with load/save and safe placeholder filling.

Prompts have two parts:
  * SYSTEM (structural) - hardcoded below, read-only. Holds the placeholders
    ({cv}/{title}/{company}/{description}) and the required output format. The
    program's functionality depends on these, so users cannot edit them.
  * <task>_notes (editable) - free-text stored in config.json that the user can
    adapt; it is concatenated onto the structural prompt at send time.
"""
import json
import os
import shutil

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")
_EXAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.example.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
MATCHING_NOTES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "matching_notes")

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

CV_EDITOR_ROLE = """You are a senior technical recruiter and resume editor. You tailor
existing CVs to a job by emphasising what the candidate really did. You are strictly
faithful to the source document: you never add facts, technologies or claims that are
not in it. You return only what is asked, in the requested format."""

CV_ADJUST_SYSTEM = """Tailor the candidate's existing CV (a numbered list of paragraphs
at the end of this message) to one specific job.

=== JOB ===
Title: {title}
Company: {company}

{description}

=== TAILORING SUGGESTIONS ===
{cv}

=== SKILLS ANALYSIS FOR THIS JOB ===
Already matched by the CV (safe to emphasise): {matched}
Missing from the CV (the candidate does NOT have these, never add or imply them): {missing}

=== HOW TO TAILOR, IN PRIORITY ORDER ===
1. Reorder and re-emphasise real experience so the most relevant work comes first.
2. Mirror the job description's wording only for things the candidate demonstrably did.
3. Trim or soften less relevant details.
4. Make the smallest set of edits that gives a real improvement. Fewer, better edits beat many cosmetic ones.

=== HARD RULES ===
- Never add a technology, tool, language, framework, employer, date, degree, number or achievement that is not already in the CV. The only exception is a technology that the user explicitly names in the ADDITIONAL INSTRUCTIONS section below, if there is one.
- Do not move content from one paragraph into another, and do not replace a bullet with different content.
- Do not touch names, contact details, dates, employers or education facts.
- Job titles and headline/role lines must stay short, plain and standard (for example "Senior Java Developer", "Senior Solution Architect", "Software Engineer"). Never append technologies, stacks, keywords or taglines to them, and never use separators such as "|", "/", "-" or "," to build a title like "Senior Java Backend Engineer | Spring Boot Microservices". Put keywords into summary or bullet text instead.
- Keep each paragraph's role (heading stays a heading, bullet stays a bullet) and a similar length. Write natural, specific sentences, not keyword lists glued together with slashes.
- Paragraphs marked [locked] must not be changed.
- Leave everything that does not need to change out of the answer.

=== EXAMPLES OF WHAT NOT TO DO ===
- CV says "Developed a Java 11 Spring Boot service". The job wants Kotlin and the CV never mentions Kotlin. Wrong: "Developed a Java/Kotlin Spring Boot service". Right: leave it, or stress the Spring Boot and service-design parts.
- Wrong: appending "MongoDB" to a technologies line when MongoDB appears nowhere else in the CV.
- Wrong: replacing a bullet about design patterns with a bullet about a different project.

Answer with ONLY a JSON object:
{"edits": [{"id": <paragraph number>, "text": "<new paragraph text>"}]}
Use an empty string as text to drop a paragraph.
"""

CV_VERIFY_SYSTEM = """You are a strict fact-checker for CV edits. Below are the original CV
paragraphs (numbered), the job, and a proposed list of edits. Review every edit and
return the corrected final list.

For each proposed edit, fix or remove it if it:
- adds a technology, tool, language, framework, employer, date, degree, number or achievement that is not in the original CV (a technology the user explicitly names in the ADDITIONAL INSTRUCTIONS section is allowed)
- moves or swaps content between paragraphs, or replaces a bullet with different content
- turns a job title or headline into something long or keyword-stuffed (titles must stay short and standard, like "Senior Java Developer")
- loses a key concrete fact (patterns, systems, results) that the original paragraph had
- reads like keyword stuffing instead of a natural sentence
- is only cosmetic and does not help for this job

Keep edits that are accurate and useful. You may rewrite an edit to fix it.
Answer with ONLY a JSON object:
{"edits": [{"id": <paragraph number>, "text": "<final paragraph text>"}]}
Use an empty string as text to drop a paragraph. Return {"edits": []} if nothing is worth changing.
"""

# registry so the backoffice can render each task's read-only structure + the
# editable notes field next to it
PROMPTS = [
    {"key": "match", "label": "Job matching", "system": MATCH_SYSTEM, "notes_key": "match_notes"},
    {"key": "cover_letter", "label": "Cover letter", "system": COVER_LETTER_SYSTEM, "notes_key": "cover_letter_notes"},
    {"key": "why_fit", "label": "Why I'm a good fit", "system": WHY_FIT_SYSTEM, "notes_key": "why_fit_notes"},
    {"key": "cv_suggestions", "label": "CV suggestions", "system": CV_SUGGESTIONS_SYSTEM, "notes_key": "cv_suggestions_notes"},
    {"key": "cv_adjust", "label": "Adjust CV to position", "system": CV_ADJUST_SYSTEM, "notes_key": "cv_adjust_notes"},
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
    "cv_adjust_notes": "",
}


def load():
    # first run on a fresh clone: config.json is gitignored (it holds personal
    # info), so bootstrap it from the tracked example rather than silently
    # running on blank defaults with no signal that setup is needed
    if not os.path.exists(_CONFIG_PATH) and os.path.exists(_EXAMPLE_CONFIG_PATH):
        shutil.copyfile(_EXAMPLE_CONFIG_PATH, _CONFIG_PATH)
        print("[settings] created config.json from config.example.json - open the backoffice to fill in your details")

    cfg = dict(DEFAULTS)
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save(values):
    """Merge `values` into the stored config and write it back."""
    cfg = load()
    cfg.update(values)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


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


def matching_notes_files():
    """Sorted list of .txt filenames in data/matching_notes/, or [] if the
    directory doesn't exist or is empty.
    """
    if not os.path.isdir(MATCHING_NOTES_DIR):
        return []
    return sorted(f for f in os.listdir(MATCHING_NOTES_DIR) if f.lower().endswith(".txt"))


def _read_matching_notes_files():
    """Combined content of every file in data/matching_notes/, in filename order."""
    parts = []
    for name in matching_notes_files():
        try:
            with open(os.path.join(MATCHING_NOTES_DIR, name), "r", encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            continue
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def effective_match_notes(cfg):
    """Matching guidance appended to the MATCH_SYSTEM prompt (never the CV, and
    never the generation tasks - cover letter / why-fit / CV suggestions).

    data/matching_notes/*.txt takes priority when present (lets multiple
    ranking-rule fragments be managed as files instead of one textarea); falls
    back to the match_notes config field when that directory is empty or
    missing.
    """
    notes = _read_matching_notes_files()
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


def match_dir(company):
    """Return (absolute_dir, relative_dir) for a company's grouped output folder,
    creating it if needed. All docs for a match (cover letter, why-fit, CV
    suggestions) are grouped under output/<company_slug>/.
    """
    rel_dir = slug(company)
    abs_dir = os.path.join(OUTPUT_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    return abs_dir, rel_dir


def unique_filename(abs_dir, filename):
    """Avoid silently overwriting a different job's file with the same name
    (e.g. two matched openings at the same company): append _2, _3, ... if the
    exact filename already exists.
    """
    if not os.path.exists(os.path.join(abs_dir, filename)):
        return filename
    stem, ext = os.path.splitext(filename)
    n = 2
    while os.path.exists(os.path.join(abs_dir, "{}_{}{}".format(stem, n, ext))):
        n += 1
    return "{}_{}{}".format(stem, n, ext)
