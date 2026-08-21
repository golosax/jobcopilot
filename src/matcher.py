"""Scores a job description against the CV using Claude.

The structural prompt is hardcoded in settings (read-only); the user's editable
match_notes are appended at send time.
"""
from . import llm, settings


def score(job, cv_text, cfg):
    prompt = settings.compose(
        settings.MATCH_SYSTEM,
        cv=cv_text,
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=cfg.get("match_notes", ""),
        global_notes=cfg.get("global_style_notes", ""),
    )
    result = llm.complete_json(prompt, model=settings.active_model(cfg), backend=cfg.get("llm_backend", "claude"))
    result["score"] = int(result.get("score", 0))
    return result
