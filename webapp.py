"""JobCopilot backoffice - configure prompts/settings, upload CV, start the run,
and browse matches.json / visited.json.

Run:  .venv/bin/python webapp.py   then open http://127.0.0.1:5000
"""
import io
import json
import logging
import os
import threading

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from src import cover_letter, runner, settings
from src.store import Store

load_dotenv()

# quiet the per-request access log (the /status polling spam); keep real errors
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_ROOT = os.path.dirname(__file__)
_OUTPUT_DIR = os.path.join(_ROOT, "output")
_CV_PATH = os.path.join(_ROOT, "data", "cv.txt")

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
        extra_notes_files=settings.extra_notes_files(),
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
        "extra_notes": f.get("extra_notes", ""),
        "global_style_notes": f.get("global_style_notes", ""),
        "match_notes": f.get("match_notes", ""),
        "cover_letter_notes": f.get("cover_letter_notes", ""),
        "why_fit_notes": f.get("why_fit_notes", ""),
        "cv_suggestions_notes": f.get("cv_suggestions_notes", ""),
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
    return render_template("matches.html", matches=items, sort=sort)


@app.route("/matches/apply", methods=["POST"])
def matches_apply():
    # called via fetch() from the checkbox's onchange - no page navigation, so
    # just acknowledge instead of redirecting back to /matches
    url = request.form.get("url", "")
    applied = request.form.get("applied") == "1"
    if url:
        Store().set_applied(url, applied)
    return ("", 204)


@app.route("/matches/generate-cl", methods=["POST"])
def matches_generate_cl():
    url = request.form.get("url", "")
    sort = request.form.get("sort", "")
    if url:
        match = next((m for m in Store().list_matches() if m.get("url") == url), None)
        if match:
            description = _jd_description(match.get("jd_file"))
            if not description:
                _log("[web] cover letter generation skipped for {} @ {} - no job description saved".format(
                    match.get("position", ""), match.get("company", "")
                ))
                return redirect(url_for("matches", sort=sort) if sort else url_for("matches"))
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
            except Exception as exc:
                _log("[web] cover letter generation failed: {}".format(exc))
    return redirect(url_for("matches", sort=sort) if sort else url_for("matches"))


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


@app.route("/view-extra-notes/<path:name>")
def view_extra_notes(name):
    # read-only viewer for data/extra_notes/*.txt - no edit route, files are
    # managed on disk, this is just for visibility from the config page
    if not name.lower().endswith(".txt"):
        abort(404)
    extra_notes_dir = os.path.realpath(settings.EXTRA_NOTES_DIR)
    path = os.path.realpath(os.path.join(extra_notes_dir, name))
    if not path.startswith(extra_notes_dir + os.sep) or not os.path.isfile(path):
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
    lines = [
        "Title: {}".format(jd.get("title", "")),
        "Company: {}".format(jd.get("company", "")),
        "Score: {}%".format(jd.get("score")) if jd.get("score") is not None else "Score: —",
    ]
    if jd.get("matched"):
        lines.append("Matched: {}".format(", ".join(jd["matched"])))
    if jd.get("missing"):
        lines.append("Missing: {}".format(", ".join(jd["missing"])))
    if jd.get("reason"):
        lines.append("Reason: {}".format(jd["reason"]))
    lines.append("")
    lines.append(jd.get("description", ""))
    text = "\n".join(lines)
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
