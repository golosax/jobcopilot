"""Scores a job description against the CV using Claude.

The structural prompt is hardcoded in settings (read-only); the user's editable
match_notes are appended at send time.
"""
import os

from . import llm, settings

_MANUAL_JD_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "manual_jd.txt"))


def _build_prompt(job, cv_text, cfg):
    return settings.compose(
        settings.MATCH_SYSTEM,
        cv=cv_text,
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=cfg.get("match_notes", ""),
        global_notes=cfg.get("global_style_notes", ""),
    )


def score(job, cv_text, cfg):
    prompt = _build_prompt(job, cv_text, cfg)
    result = llm.complete_json(prompt, model=settings.active_model(cfg), backend=cfg.get("llm_backend", "claude"))
    result["score"] = int(result.get("score", 0))
    return result


if __name__ == "__main__":
    # manual sanity check for the match prompt - run with:
    #   .venv/bin/python -m src.matcher
    # paste the job description into data/manual_jd.txt (not into the terminal -
    # a large paste there can trip bracketed-paste/shell warnings) and re-run;
    # everything else (CV, extra_notes, match_notes, global style rules, score())
    # is the exact same code path the real run uses
    import sys

    from . import runner

    if not os.path.exists(_MANUAL_JD_PATH):
        os.makedirs(os.path.dirname(_MANUAL_JD_PATH), exist_ok=True)
        open(_MANUAL_JD_PATH, "w").close()
        sys.exit("created {} - paste the job description in there and re-run".format(_MANUAL_JD_PATH))

    with open(_MANUAL_JD_PATH, "r", encoding="utf-8") as f:
        description = f.read().strip()
    if not description:
        sys.exit("{} is empty - paste the job description in there and re-run".format(_MANUAL_JD_PATH))

    cfg = settings.load()
    cv_text = runner.load_cv()
    if not cv_text:
        sys.exit("no CV found - upload one in the backoffice first")
    eff_cv = settings.effective_cv(cfg, cv_text)

    job = {"title": "", "company": "", "description": description}

    print("=== PROMPT SENT TO THE MODEL ===")
    print(_build_prompt(job, eff_cv, cfg))
    print("=== END PROMPT ===\n")

    result = score(job, eff_cv, cfg)
    print("=== RESULT ===")
    print(result)
