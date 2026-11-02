"""JobCopilot backoffice - configure prompts/settings, upload CV, start the run,
and browse matches.json / visited.json.

Run:  .venv/bin/python webapp.py   then open http://127.0.0.1:5000
"""
import io
import json
import logging
import os
import threading
import time

from dotenv import load_dotenv
from markupsafe import Markup, escape
from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from src import cover_letter, cv_adjust, cv_suggestions, runner, settings
from src.store import Store

load_dotenv()

# quiet the per-request access log (the /status polling spam); keep real errors
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_ROOT = os.path.dirname(__file__)
_OUTPUT_DIR = os.path.join(_ROOT, "output")
_CV_PATH = os.path.join(_ROOT, "data", "cv.txt")

# a match counts as new for this long after it is first added
_NEW_MATCH_WINDOW_SECONDS = 24 * 3600

app = Flask(__name__, template_folder=os.path.join(_ROOT, "web", "templates"))

# in-memory run log (most recent lines)
_log_lines = []
_LOG_MAX = 500


def _log(line):
    print(line)
    _log_lines.append(str(line))
    del _log_lines[:-_LOG_MAX]


# -- CV parsing ----------------------------------------------------------

def _extract_text(filename, data):
    lower = filename.lower()
    if lower.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")
    if lower.endswith(".pdf"):
        import pdfplumber

        text = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text.append(page.extract_text() or "")
        return "\n".join(text)
    if lower.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    raise ValueError("unsupported file type (use pdf, docx or txt)")


def _cv_status():
    if not os.path.exists(_CV_PATH):
        return {"exists": False, "chars": 0, "preview": ""}
    with open(_CV_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    snippet = " ".join(text.split())[:180]
    return {"exists": True, "chars": len(text), "preview": snippet}


# -- job description (JD) helpers -----------------------------------------

def _load_jd(jd_file):
    """Load a job_description.py-saved JSON dict, or None if missing/unreadable."""
    if not jd_file:
        return None
    path = os.path.realpath(os.path.join(_OUTPUT_DIR, jd_file))
    if not path.startswith(os.path.realpath(_OUTPUT_DIR) + os.sep) or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _jd_description(jd_file):
    """The saved job description text, or "" if there isn't a usable one."""
    jd = _load_jd(jd_file)
    return (jd.get("description") or "").strip() if jd else ""


# -- form parsing --------------------------------------------------------

def _to_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# -- routes --------------------------------------------------------------

@app.route("/")
def index():
    cfg = settings.load()
    return render_template(
        "config.html",
        cfg=cfg,
        cv=_cv_status(),
        running=runner.is_running(),
        prompts=settings.PROMPTS,
        matching_notes_files=settings.matching_notes_files(),
    )


@app.route("/save", methods=["POST"])
def save():
    f = request.form
    values = {
        "candidate_name": f.get("candidate_name", "").strip(),
        "candidate_location": f.get("candidate_location", "").strip(),
        "candidate_email": f.get("candidate_email", "").strip(),
        "candidate_phone": f.get("candidate_phone", "").strip(),
        "candidate_linkedin": f.get("candidate_linkedin", "").strip(),
        "search_url": f.get("search_url", "").strip(),
        "match_threshold": _to_int(f.get("match_threshold"), 70),
        "global_style_notes": f.get("global_style_notes", ""),
        "match_notes": f.get("match_notes", ""),
        "cover_letter_notes": f.get("cover_letter_notes", ""),
        "why_fit_notes": f.get("why_fit_notes", ""),
        "cv_suggestions_notes": f.get("cv_suggestions_notes", ""),
        "cv_adjust_notes": f.get("cv_adjust_notes", ""),
        "min_delay_sec": _to_int(f.get("min_delay_sec"), 15),
        "max_delay_sec": _to_int(f.get("max_delay_sec"), 90),
        "max_pages": _to_int(f.get("max_pages"), 0),
        "max_jobs_per_run": _to_int(f.get("max_jobs_per_run"), 0),
        "claude_model": f.get("claude_model", "sonnet").strip(),
        "llm_backend": f.get("llm_backend", "claude"),
        "codex_model": f.get("codex_model", "").strip(),
        "headless": f.get("headless") == "on",
    }
    cv = f.get("chrome_version", "").strip()
    values["chrome_version"] = _to_int(cv, None) if cv else None
    settings.save(values)
    return redirect(url_for("index"))


@app.route("/upload-cv", methods=["POST"])
def upload_cv():
    file = request.files.get("cv_file")
    if not file or not file.filename:
        return redirect(url_for("index"))
    try:
        text = _extract_text(file.filename, file.read()).strip()
        os.makedirs(os.path.dirname(_CV_PATH), exist_ok=True)
        with open(_CV_PATH, "w", encoding="utf-8") as out:
            out.write(text)
        _log("[web] CV uploaded ({} chars) from {}".format(len(text), file.filename))
    except Exception as exc:
        _log("[web] CV upload failed: {}".format(exc))
    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
def start():
    if runner.is_running():
        _log("[web] run already in progress")
    else:
        _log_lines.clear()
        _log("[web] starting analysis...")
        threading.Thread(target=runner.run, kwargs={"on_log": _log}, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    if runner.is_running():
        _log("[web] stop requested")
        runner.request_stop()
    return redirect(url_for("index"))


@app.route("/status")
def status():
    return {"running": runner.is_running(), "log": "\n".join(_log_lines[-200:])}


@app.route("/matches")
def matches():
    sort = request.args.get("sort", "")
    items = Store().list_matches()
    if sort == "asc":
        items = sorted(items, key=lambda m: m.get("score", 0))
    elif sort == "desc":
        items = sorted(items, key=lambda m: m.get("score", 0), reverse=True)
    for m in items:
        m["jd_description"] = _jd_description(m.get("jd_file"))
    # one group per company, ordered by first appearance so the chosen sort is kept
    groups = {}
    for m in items:
        groups.setdefault((m.get("company") or "").strip().lower(), []).append(m)
    now = time.time()
    for m in items:
        m["is_new"] = now - m.get("added_at", 0) < _NEW_MATCH_WINDOW_SECONDS
    grouped = [
        {
            "company": g[0].get("company"),
            "items": g,
            "top_score": max(m.get("score", 0) for m in g),
            "newest": max(m.get("added_at", 0) for m in g),
        }
        for g in groups.values()
    ]
    for g in grouped:
        g["is_new"] = now - g["newest"] < _NEW_MATCH_WINDOW_SECONDS
    # without an explicit sort, groups with a recently added position go to the bottom, newest last
    if not sort:
        fresh = sorted((g for g in grouped if g["is_new"]), key=lambda g: g["newest"])
        grouped = [g for g in grouped if not g["is_new"]] + fresh
    return render_template("matches.html", matches=items, groups=grouped, sort=sort)


@app.route("/matches/status", methods=["POST"])
def matches_status():
    # called via fetch() from the radio buttons - no page navigation
    url = request.form.get("url", "")
    status = request.form.get("status", "")
    if status not in ("", "applied", "not_available", "not_interested"):
        return ("", 400)
    if url:
        Store().set_status(url, status)
    return ("", 204)


@app.route("/matches/apply", methods=["POST"])
def matches_apply():
    # called via fetch() from the checkbox's onchange - no page navigation, so
    # just acknowledge instead of redirecting back to /matches
    url = request.form.get("url", "")
    applied = request.form.get("applied") == "1"
    if url:
        Store().set_applied(url, applied)
    return ("", 204)


@app.route("/matches/not-available", methods=["POST"])
def matches_not_available():
    # called via fetch() from the checkbox's onchange - no page navigation, so
    # just acknowledge instead of redirecting back to /matches
    url = request.form.get("url", "")
    not_available = request.form.get("not_available") == "1"
    if url:
        Store().set_not_available(url, not_available)
    return ("", 204)


@app.route("/matches/not-interested", methods=["POST"])
def matches_not_interested():
    # called via fetch() from the checkbox's onchange - no page navigation
    url = request.form.get("url", "")
    not_interested = request.form.get("not_interested") == "1"
    if url:
        Store().set_not_interested(url, not_interested)
    return ("", 204)


@app.route("/matches/generate-cl", methods=["POST"])
def matches_generate_cl():
    # called via fetch() from the Matches page - no page navigation, so the
    # button state updates in place instead of a full reload resetting scroll
    url = request.form.get("url", "")
    if not url:
        return {"ok": False, "error": "missing url"}, 400
    match = next((m for m in Store().list_matches() if m.get("url") == url), None)
    if not match:
        return {"ok": False, "error": "match not found"}, 404
    description = _jd_description(match.get("jd_file"))
    if not description:
        _log("[web] cover letter generation skipped for {} @ {} - no job description saved".format(
            match.get("position", ""), match.get("company", "")
        ))
        return {"ok": False, "error": "No job description saved for this match"}, 400
    job = {
        "title": match.get("position", ""),
        "company": match.get("company", ""),
        "description": description,
    }
    try:
        cfg = settings.load()
        cl_file = cover_letter.generate(job, runner.load_cv(), cfg)
        Store().set_cover_letter(url, cl_file)
        _log("[web] cover letter generated for {} @ {}".format(job["title"], job["company"]))
        return {"ok": True, "file_url": url_for("output_file", name=cl_file)}
    except Exception as exc:
        _log("[web] cover letter generation failed: {}".format(exc))
        return {"ok": False, "error": str(exc)}, 500


# -- adjust CV to a position ----------------------------------------------

def _match_job(url):
    """(match, job dict) for a match url, or (None, None)."""
    match = next((m for m in Store().list_matches() if m.get("url") == url), None)
    if not match:
        return None, None
    job = {
        "title": match.get("position", ""),
        "company": match.get("company", ""),
        "description": _jd_description(match.get("jd_file")),
    }
    return match, job


def _read_output_text(rel):
    path = os.path.realpath(os.path.join(_OUTPUT_DIR, rel or ""))
    if not rel or not path.startswith(os.path.realpath(_OUTPUT_DIR) + os.sep) or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


@app.route("/matches/adjust-cv")
def adjust_cv():
    url = request.args.get("url", "")
    match, job = _match_job(url)
    if not match:
        abort(404)
    sort = request.args.get("sort", "")
    back_url = url_for("matches", sort=sort) if sort else url_for("matches")
    jd = _load_jd(match.get("jd_file")) or {}
    return render_template(
        "adjust_cv.html", match=match, has_jd=bool(job["description"]), back_url=back_url,
        suggestions=_read_output_text(match.get("cv_suggestions_file")),
        missing=jd.get("missing") or [], matched=jd.get("matched") or [],
    )


@app.route("/matches/adjust-cv/process", methods=["POST"])
def adjust_cv_process():
    url = request.form.get("url", "")
    match, job = _match_job(url)
    if not match:
        return {"ok": False, "error": "match not found"}, 404
    if not job["description"]:
        return {"ok": False, "error": "No job description saved for this match"}, 400
    token = request.form.get("token", "")
    try:
        upload = request.files.get("file")
        if upload and upload.filename:
            if not upload.filename.lower().endswith(".docx"):
                return {"ok": False, "error": "Please upload a .docx file"}, 400
            data = upload.read()
            try:
                import docx
                docx.Document(io.BytesIO(data))
            except Exception:
                return {"ok": False, "error": "Could not read the file as a Word document"}, 400
            token = cv_adjust.new_session(data)
        original = cv_adjust.original_path(token)
        cfg = settings.load()
        suggestions = _read_output_text(match.get("cv_suggestions_file"))
        if not suggestions:
            suggestions = cv_suggestions.generate_text(job, runner.load_cv(), cfg)
        proposed = cv_adjust.proposed_path(token)
        changed = cv_adjust.propose(original, proposed, job, suggestions, cfg, request.form.get("extra", ""))
        rows = cv_adjust.diff_rows(original, proposed)
        _log("[web] CV adjusted for {} @ {}: {} paragraphs changed".format(job["title"], job["company"], changed))
        return {
            "ok": True,
            "token": token,
            "changed": sum(1 for r in rows if r["kind"] != "equal"),
            "html": render_template("adjust_cv_diff.html", rows=rows),
        }
    except Exception as exc:
        _log("[web] CV adjustment failed: {}".format(exc))
        return {"ok": False, "error": str(exc)}, 500


@app.route("/matches/adjust-cv/accept", methods=["POST"])
def adjust_cv_accept():
    match, job = _match_job(request.form.get("url", ""))
    if not match:
        return {"ok": False, "error": "match not found"}, 404
    try:
        rel = cv_adjust.save_accepted(request.form.get("token", ""), job, settings.load())
        _log("[web] adjusted CV saved to output/{}".format(rel))
        return {"ok": True, "file": rel, "file_url": url_for("output_file", name=rel)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


@app.route("/visited")
def visited():
    items = Store().list_visited()
    return render_template("visited.html", items=items, count=len(items))


@app.route("/visited/delete", methods=["POST"])
def visited_delete():
    url = request.form.get("url", "")
    if url:
        Store().remove_visited(url)
    return redirect(url_for("visited"))


@app.route("/visited/clear", methods=["POST"])
def visited_clear():
    Store().clear_visited()
    return redirect(url_for("visited"))


@app.route("/file/<path:name>")
def output_file(name):
    return send_from_directory(_OUTPUT_DIR, name, as_attachment=False)


@app.route("/view/<path:name>")
def view_text(name):
    # why-fit / cv-suggestions notes only - the cover letter is a downloadable
    # DOCX, linked directly via output_file() instead
    if not name.lower().endswith(".txt"):
        abort(404)
    path = os.path.realpath(os.path.join(_OUTPUT_DIR, name))
    if not path.startswith(os.path.realpath(_OUTPUT_DIR) + os.sep) or not os.path.isfile(path):
        abort(404)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    title = os.path.basename(name)
    sort = request.args.get("sort", "")
    back_url = url_for("matches", sort=sort) if sort else url_for("matches")
    return render_template("view_text.html", title=title, text=text, back_url=back_url)


@app.route("/view-matching-notes/<path:name>")
def view_matching_notes(name):
    # read-only viewer for data/matching_notes/*.txt - no edit route, files are
    # managed on disk, this is just for visibility from the config page
    if not name.lower().endswith(".txt"):
        abort(404)
    matching_notes_dir = os.path.realpath(settings.MATCHING_NOTES_DIR)
    path = os.path.realpath(os.path.join(matching_notes_dir, name))
    if not path.startswith(matching_notes_dir + os.sep) or not os.path.isfile(path):
        abort(404)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return render_template("view_text.html", title=name, text=text, back_url=url_for("index"))


@app.route("/view-jd/<path:name>")
def view_jd(name):
    # formats a job_description.py JSON file into readable text and renders it
    # through the same reading page used for why-fit/cv-suggestions - linked
    # from both the Matches and Visited tabs, covers matches and non-matches alike
    if not name.lower().endswith(".json"):
        abort(404)
    jd = _load_jd(name)
    if jd is None:
        abort(404)
    # (key, value) pairs - keys are rendered bold so they stand out when scanning the page
    fields = [
        ("Title", jd.get("title", "")),
        ("Company", jd.get("company", "")),
        ("Score", "{}%".format(jd.get("score")) if jd.get("score") is not None else "—"),
    ]
    if jd.get("matched"):
        fields.append(("Matched", ", ".join(jd["matched"])))
    if jd.get("missing"):
        fields.append(("Missing", ", ".join(jd["missing"])))
    if jd.get("reason"):
        fields.append(("Reason", jd["reason"]))
    text = Markup("\n").join(Markup("<strong>{}:</strong> {}").format(k, v) for k, v in fields)
    text += Markup("\n\n") + escape(jd.get("description", ""))
    origin = "matches" if request.args.get("from") == "matches" else "visited"
    sort = request.args.get("sort", "")
    if origin == "matches":
        back_url = url_for("matches", sort=sort) if sort else url_for("matches")
    else:
        back_url = url_for("visited")
    return render_template(
        "view_text.html", title=os.path.basename(name), text=text, back_url=back_url, back_label=origin
    )


if __name__ == "__main__":
    import webbrowser

    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
