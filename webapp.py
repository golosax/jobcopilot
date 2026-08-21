"""JobCopilot backoffice - configure prompts/settings, upload CV, start the run,
and browse matches.json / visited.json.

Run:  .venv/bin/python webapp.py   then open http://127.0.0.1:5000
"""
import io
import logging
import os
import threading

from dotenv import load_dotenv
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from src import runner, settings
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
        "generate_cover_letter": f.get("generate_cover_letter") == "on",
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
    return render_template("matches.html", matches=items, sort=sort)


@app.route("/matches/apply", methods=["POST"])
def matches_apply():
    url = request.form.get("url", "")
    applied = "1" in request.form.getlist("applied")
    sort = request.form.get("sort", "")
    if url:
        Store().set_applied(url, applied)
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


if __name__ == "__main__":
    import webbrowser

    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
