"""Generates a tailored English cover letter and renders it to DOCX."""
import os
import subprocess
import tempfile

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


def render_docx(job, body_text, cfg, directory):
    name = cfg.get("candidate_name", "Candidate")
    company = job.get("company", "")
    title = job.get("title", "")
    filename = "CL_{}_{}.docx".format(settings.slug(name), settings.slug(company))
    path = os.path.join(directory, filename)

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
    return path


def convert_to_pdf(docx_path):
    """Render a .docx to .pdf next to it via headless LibreOffice - the PDF is a
    direct rendering of the same document, so its content always matches.
    """
    abs_path = docx_path
    out_dir = os.path.dirname(abs_path)
    with tempfile.TemporaryDirectory() as profile_dir:
        subprocess.run(
            [
                "soffice", "--headless", "--norestore",
                "-env:UserInstallation=file://{}".format(profile_dir),
                "--convert-to", "pdf", "--outdir", out_dir, abs_path,
            ],
            check=True, capture_output=True, timeout=60,
        )
    pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
    if not os.path.isfile(pdf_path):
        raise RuntimeError("cover letter PDF was not created")
    return pdf_path


def generate(job, cv_text, cfg, user_id, url):
    """Return the relative path (company_folder/filename.docx) of the cover letter.
    A .pdf rendering of the same file is saved alongside it.
    """
    from . import database

    body = generate_text(job, cv_text, cfg)
    with tempfile.TemporaryDirectory() as directory:
        docx_path = render_docx(job, body, cfg, directory)
        pdf_path = convert_to_pdf(docx_path)
        with open(docx_path, "rb") as f:
            docx_content = f.read()
        with open(pdf_path, "rb") as f:
            pdf_content = f.read()
        return database.store_cover_letter(user_id, url, os.path.basename(docx_path), docx_content,
                                           os.path.basename(pdf_path), pdf_content)
