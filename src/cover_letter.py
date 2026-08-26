"""Generates a tailored English cover letter and renders it to DOCX."""
import os

from docx import Document
from docx.shared import Pt

from . import llm, settings


def generate_text(job, cv_text, cfg):
    prompt = settings.compose(
        settings.COVER_LETTER_SYSTEM,
        cv=cv_text,
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=cfg.get("cover_letter_notes", ""),
        global_notes=cfg.get("global_style_notes", ""),
    )
    return llm.complete(prompt, model=settings.active_model(cfg), backend=cfg.get("llm_backend", "claude")).strip()


def render_docx(job, body_text, cfg):
    name = cfg.get("candidate_name", "Candidate")
    company = job.get("company", "")
    title = job.get("title", "")
    abs_dir, rel_dir = settings.match_dir(company)
    filename = settings.unique_filename(
        abs_dir, "CL_{}_{}.docx".format(settings.slug(name), settings.slug(company))
    )
    path = os.path.join(abs_dir, filename)

    contact_parts = [
        p for p in [
            cfg.get("candidate_location", ""),
            cfg.get("candidate_email", ""),
            cfg.get("candidate_phone", ""),
            cfg.get("candidate_linkedin", ""),
        ] if p
    ]

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    name_p = doc.add_paragraph()
    name_run = name_p.add_run(name)
    name_run.bold = True
    name_run.font.size = Pt(16)

    if contact_parts:
        contact_p = doc.add_paragraph(" | ".join(contact_parts))
        contact_p.runs[0].font.size = Pt(9.5)

    doc.add_paragraph()
    doc.add_paragraph(company)
    doc.add_paragraph("Re: {}".format(title))
    doc.add_paragraph()
    doc.add_paragraph("Dear Hiring Team,")

    for para in [p for p in body_text.split("\n") if p.strip()]:
        doc.add_paragraph(para.strip())

    doc.add_paragraph()
    doc.add_paragraph("Kind regards,")
    doc.add_paragraph(name)

    doc.save(path)
    return "{}/{}".format(rel_dir, filename)


def generate(job, cv_text, cfg):
    """Return the relative path (company_folder/filename.docx) of the cover letter."""
    body = generate_text(job, cv_text, cfg)
    return render_docx(job, body, cfg)
