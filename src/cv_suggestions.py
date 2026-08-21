"""Generates tailored CV suggestions (what to add / remove) for a specific job."""
import os

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


def save_doc(job, text, cfg):
    name = cfg.get("candidate_name", "Candidate")
    company = job.get("company", "")
    abs_dir, rel_dir = settings.match_dir(company)
    filename = settings.unique_filename(
        abs_dir, "CVTIPS_{}_{}.txt".format(settings.slug(name), settings.slug(company))
    )
    with open(os.path.join(abs_dir, filename), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return "{}/{}".format(rel_dir, filename)


def generate(job, cv_text, cfg):
    """Return the relative path (company_folder/filename.txt) of the CV suggestions."""
    text = generate_text(job, cv_text, cfg)
    return save_doc(job, text, cfg)
