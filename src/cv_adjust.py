"""Tailors an uploaded DOCX CV to a job: the LLM proposes paragraph edits based on
the CV suggestions, the result is diffed for review and only saved once accepted.
"""
import difflib
import json
import os
import re
import shutil
import tempfile
import uuid

from docx import Document
from docx.oxml.ns import qn
from markupsafe import Markup, escape

from . import llm, settings

_PENDING_DIR = os.path.join(tempfile.gettempdir(), "jobcopilot_cv_adjust")


def _iter_paragraphs(doc):
    """Body paragraphs and table-cell paragraphs in document order."""
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            seen = set()
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                for p in cell.paragraphs:
                    yield p


def _ordered_paragraphs(doc):
    """Stable list of non-empty paragraphs; the index is the id shown to the LLM."""
    return [p for p in _iter_paragraphs(doc) if p.text.strip()]


def _is_locked(paragraph):
    return paragraph._p.find(qn("w:hyperlink")) is not None


def _numbered(paragraphs):
    lines = []
    for i, p in enumerate(paragraphs):
        lines.append("{}{}: {}".format(i, " [locked]" if _is_locked(p) else "", p.text.strip()))
    return "\n".join(lines)


def _set_text(paragraph, text):
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._r.getparent().remove(r._r)


# CV adjustment is the one task that runs at maximum effort
_EFFORT = "xhigh"
_TIMEOUT = 600

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")
_ALIASES = {"k8s": "kubernetes", "js": "javascript", "ts": "typescript", "postgres": "postgresql", "node": "nodejs"}


def _norm(token):
    t = token.lower().strip(".")
    return _ALIASES.get(t, t)


def _tokens(text):
    return {_norm(t) for t in _TOKEN.findall(text)}


def _tech_shaped(token, first_in_paragraph):
    t = token.strip(".")
    if len(t) < 2:
        return False
    if any(c.isdigit() or c in "+#" for c in t):
        return True
    if any(c.isupper() for c in t[1:]):
        return True
    return t[0].isupper() and not first_in_paragraph


def _new_technologies(new_text, original_text, allowed_text, missing):
    """Terms the edit introduces that are not in the original CV or the user's own instructions."""
    known = _tokens(original_text) | _tokens(allowed_text)
    found = []
    for i, m in enumerate(_TOKEN.finditer(new_text)):
        if _tech_shaped(m.group(), i == 0) and _norm(m.group()) not in known and m.group().strip(".") not in found:
            found.append(m.group().strip("."))
    lower_new, lower_known = new_text.lower(), (original_text + " " + allowed_text).lower()
    for skill in missing:
        sk = skill.strip().lower()
        if sk and sk in lower_new and sk not in lower_known and skill.strip() not in found:
            found.append(skill.strip())
    return found


def _numbered_edits(edits):
    return json.dumps({"edits": [{"id": i, "text": t} for i, t in sorted(edits.items())]}, ensure_ascii=False)


def _parse_edits(result, paragraphs):
    edits = {}
    for edit in (result or {}).get("edits", []):
        try:
            idx = int(edit["id"])
            text = str(edit["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= idx < len(paragraphs):
            continue
        p = paragraphs[idx]
        if _is_locked(p) or text == p.text.strip():
            continue
        edits[idx] = text
    return edits


def _ask(prompt, cfg):
    return llm.complete_json(
        prompt,
        model=settings.active_model(cfg),
        backend=cfg.get("llm_backend", "claude"),
        system=settings.CV_EDITOR_ROLE,
        effort=_EFFORT,
        isolated=True,
        timeout=_TIMEOUT,
    )


def propose(docx_path, out_path, job, suggestions, cfg, extra="", matched=(), missing=()):
    """Write the adjusted copy to out_path; returns (changed paragraph count, blocked edits).

    Two LLM passes (propose, then fact-check), then a code guard that blocks any edit
    introducing a technology that is neither in the original CV nor named in the
    user's own instructions. Blocked edits are not applied and are returned for display.
    """
    doc = Document(docx_path)
    paragraphs = _ordered_paragraphs(doc)
    if not paragraphs:
        raise ValueError("the document has no text")
    custom = "\n".join(n for n in (cfg.get("cv_adjust_notes", "").strip(), extra.strip()) if n)
    global_notes = cfg.get("global_style_notes", "")
    numbered = "\n\n=== CV PARAGRAPHS ===\n" + _numbered(paragraphs)

    first_prompt = settings.compose(
        settings.CV_ADJUST_SYSTEM.replace("{cv}", suggestions)
        .replace("{matched}", ", ".join(matched) or "none recorded")
        .replace("{missing}", ", ".join(missing) or "none recorded"),
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
        notes=custom,
        global_notes=global_notes,
    ) + numbered
    edits = _parse_edits(_ask(first_prompt, cfg), paragraphs)

    if edits:
        verify_prompt = settings.compose(
            settings.CV_VERIFY_SYSTEM,
            title=job.get("title", ""),
            company=job.get("company", ""),
            description=job.get("description", ""),
            notes=custom,
            global_notes=global_notes,
        ) + numbered + "\n\n=== PROPOSED EDITS ===\n" + _numbered_edits(edits)
        edits = _parse_edits(_ask(verify_prompt, cfg), paragraphs)

    original_text = "\n".join(p.text for p in paragraphs)
    blocked = []
    changed = 0
    to_remove = []
    for idx, text in edits.items():
        p = paragraphs[idx]
        terms = _new_technologies(text, original_text, custom, missing) if text else []
        if terms:
            blocked.append({"original": p.text.strip(), "text": text, "terms": terms})
            continue
        changed += 1
        if text:
            _set_text(p, text)
        elif p._p.getparent().tag == qn("w:body"):
            to_remove.append(p)
        else:
            _set_text(p, "")
    for p in to_remove:
        p._p.getparent().remove(p._p)
    doc.save(out_path)
    return changed, blocked


def _inline_diff(old, new):
    """(left, right) Markup: words removed are marked on the left, words added on the right."""
    a, b = old.split(), new.split()
    left, right = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            text = escape(" ".join(a[i1:i2]))
            left.append(text)
            right.append(text)
            continue
        if i2 > i1:
            left.append(Markup('<del class="d">{}</del>').format(" ".join(a[i1:i2])))
        if j2 > j1:
            right.append(Markup('<ins class="a">{}</ins>').format(" ".join(b[j1:j2])))
    return Markup(" ").join(left), Markup(" ").join(right)


def diff_rows(original_path, proposed_path):
    """The whole document as aligned rows for a side-by-side view:
    [{"kind": "equal"|"changed"|"removed"|"added", "left": Markup|None, "right": Markup|None}].
    """
    old = [p.text.strip() for p in _ordered_paragraphs(Document(original_path))]
    new = [p.text.strip() for p in _ordered_paragraphs(Document(proposed_path))]
    rows = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            for t in old[i1:i2]:
                rows.append({"kind": "equal", "left": escape(t), "right": escape(t)})
            continue
        olds, news = old[i1:i2], new[j1:j2]
        for k in range(max(len(olds), len(news))):
            o = olds[k] if k < len(olds) else None
            n = news[k] if k < len(news) else None
            if o is not None and n is not None and difflib.SequenceMatcher(None, o, n).ratio() >= 0.4:
                left, right = _inline_diff(o, n)
                rows.append({"kind": "changed", "left": left, "right": right})
                continue
            if o is not None:
                rows.append({"kind": "removed", "left": Markup('<del class="d">{}</del>').format(o), "right": None})
            if n is not None:
                rows.append({"kind": "added", "left": None, "right": Markup('<ins class="a">{}</ins>').format(n)})
    return rows


# -- pending files (kept until accepted) ----------------------------------

def new_session(upload_bytes):
    token = uuid.uuid4().hex
    os.makedirs(os.path.join(_PENDING_DIR, token), exist_ok=True)
    with open(_path(token, "original"), "wb") as f:
        f.write(upload_bytes)
    return token


def _path(token, name):
    if len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        raise ValueError("invalid token")
    return os.path.join(_PENDING_DIR, token, name + ".docx")


def original_path(token):
    path = _path(token, "original")
    if not os.path.isfile(path):
        raise ValueError("session expired, upload the file again")
    return path


def proposed_path(token):
    return _path(token, "proposed")


def discard(token):
    shutil.rmtree(os.path.dirname(_path(token, "original")), ignore_errors=True)


def _dashed(text):
    return "-".join(re.findall(r"[^\W_]+", text))


def _filename(docx_path, cfg):
    """<Name>_<Title-From-The-CV>.docx, e.g. Alex-Golosun_Senior-Java-Engineer.docx.
    The title is the headline line right under the name at the top of the CV.
    """
    paragraphs = [p.text.strip() for p in _ordered_paragraphs(Document(docx_path))]
    name = cfg.get("candidate_name") or (paragraphs[0] if paragraphs else "") or "CV"
    headline = paragraphs[1] if len(paragraphs) > 1 else ""
    title = headline if len(headline) <= 60 and not re.search(r"[@|\d]", headline) else ""
    parts = [_dashed(name), _dashed(title)]
    return "_".join(p for p in parts if p) + ".docx"


def save_accepted(token, job, cfg):
    """Copy the accepted proposal into the company folder; returns company_folder/filename."""
    src = proposed_path(token)
    if not os.path.isfile(src):
        raise ValueError("nothing to save, process the file first")
    abs_dir, rel_dir = settings.match_dir(job.get("company", ""))
    filename = settings.unique_filename(abs_dir, _filename(src, cfg))
    shutil.copyfile(src, os.path.join(abs_dir, filename))
    discard(token)
    return "{}/{}".format(rel_dir, filename)
