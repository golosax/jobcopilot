"""Generates a short 'why this job is a top choice / why I fit' note (<= 400 chars)."""

from . import llm, settings

_MAX_CHARS = 400


def generate_text(job, cv_text, cfg):
    prompt = settings.compose(
        settings.WHY_FIT_SYSTEM,
        cv=cv_text,
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=cfg.get("why_fit_notes", ""),
        global_notes=cfg.get("global_style_notes", ""),
    )
    text = llm.complete(prompt, model=settings.active_model(cfg), backend=cfg.get("llm_backend", "claude")).strip()
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS].rstrip()
    return text
