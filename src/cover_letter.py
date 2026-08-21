"""Generates a tailored English cover letter and renders it to PDF."""
import datetime
import os

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

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


def render_pdf(job, body_text, cfg):
    name = cfg.get("candidate_name", "Candidate")
    company = job.get("company", "")
    abs_dir, rel_dir = settings.match_dir(company)
    filename = settings.unique_filename(
        abs_dir, "CL_{}_{}.pdf".format(settings.slug(name), settings.slug(company))
    )
    path = os.path.join(abs_dir, filename)

    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
    )
    styles = getSampleStyleSheet()
    name_style = ParagraphStyle("Name", parent=styles["Normal"], fontSize=16, leading=20, spaceAfter=2)
    contact_style = ParagraphStyle(
        "Contact", parent=styles["Normal"], fontSize=9.5, leading=13, textColor="#555555"
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10.5, leading=15, alignment=TA_JUSTIFY, spaceAfter=10
    )
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=10.5, leading=15)

    date_str = datetime.datetime.now().strftime("%d %B %Y")
    title = job.get("title", "")

    contact_parts = [
        p for p in [
            cfg.get("candidate_location", ""),
            cfg.get("candidate_email", ""),
            cfg.get("candidate_phone", ""),
            cfg.get("candidate_linkedin", ""),
        ] if p
    ]

    story = [
        Paragraph(name, name_style),
        Paragraph(" &nbsp;|&nbsp; ".join(contact_parts), contact_style),
        Spacer(1, 0.6 * cm),
        Paragraph(date_str, meta_style),
        Spacer(1, 0.3 * cm),
        Paragraph(company, meta_style),
        Spacer(1, 0.5 * cm),
        Paragraph("Re: {}".format(title), meta_style),
        Spacer(1, 0.4 * cm),
        Paragraph("Dear Hiring Team,", body_style),
    ]
    for para in [p for p in body_text.split("\n") if p.strip()]:
        story.append(Paragraph(para.strip(), body_style))
    story += [
        Spacer(1, 0.3 * cm),
        Paragraph("Kind regards,", meta_style),
        Spacer(1, 0.2 * cm),
        Paragraph(name, meta_style),
    ]

    doc.build(story)
    return "{}/{}".format(rel_dir, filename)


def generate(job, cv_text, cfg):
    """Return the relative path (company_folder/filename.pdf) of the cover letter."""
    body = generate_text(job, cv_text, cfg)
    return render_pdf(job, body, cfg)
