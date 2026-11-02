"""Generates tailored CV suggestions (what to add / remove) for a specific job."""

from . import llm, settings

_MAX_CHARS = 800


def generate_text(job, cv_text, cfg):
    prompt = settings.compose(
        settings.CV_SUGGESTIONS_SYSTEM,
        cv=cv_text,
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=cfg.get("cv_suggestions_notes", ""),
        global_notes=cfg.get("global_style_notes", ""),
    )
    text = llm.complete(prompt, model=settings.active_model(cfg), backend=cfg.get("llm_backend", "claude")).strip()
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS].rstrip()
    return text
