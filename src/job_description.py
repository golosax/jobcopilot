"""Persists the raw job description plus the scoring result for a match, so a
past match's score can be re-run later (against the current CV/config) and
compared against what the automated run actually produced - this is how we'd
catch/debug the kind of LLM score drift seen between a live crawl and a later
manual re-check.
"""
import datetime
import json
import os

from . import settings


def save(job, result, cfg):
    """Return the relative path (company_folder/filename.json) of the saved JD."""
    name = cfg.get("candidate_name", "Candidate")
    company = job.get("company", "")
    abs_dir, rel_dir = settings.match_dir(company)
    filename = settings.unique_filename(
        abs_dir, "JD_{}_{}.json".format(settings.slug(name), settings.slug(company))
    )
    data = {
        "title": job.get("title", ""),
        "company": company,
        "url": job.get("url", ""),
        "description": job.get("description", ""),
        "score": result.get("score"),
        "matched": result.get("matched"),
        "missing": result.get("missing"),
        "reason": result.get("reason"),
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(abs_dir, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return "{}/{}".format(rel_dir, filename)
