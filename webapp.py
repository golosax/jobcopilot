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
from datetime import datetime

from dotenv import load_dotenv
from markupsafe import Markup, escape
from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from src import database, portable, settings
from src.store import Store

load_dotenv()

# quiet the per-request access log (the /status polling spam); keep real errors
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_ROOT = os.path.dirname(__file__)

# a match counts as new for this long after it is first added
_NEW_MATCH_WINDOW_SECONDS = 24 * 3600

app = Flask(__name__, template_folder=os.path.join(_ROOT, "web", "templates"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "local-jobcopilot-session-key")


def current_user():
    return database.get_user(session.get("user_id"))


def active_user():
    user = current_user()
    if not user:
        abort(403, "select a user first")
    return user


@app.context_processor
def account_context():
    return {"current_user": current_user(), "viewer_mode": database.VIEWER}


@app.before_request
def viewer_guard():
    if database.VIEWER and request.endpoint in {"start", "stop", "matches_generate_cl", "upload_cv", "save",
                                                "create_user", "save_guidance", "delete_guidance", "import_changes",
                                                "save_credentials", "matches_apply", "matches_not_available",
                                                "matches_not_interested"}:
        abort(403)

# in-memory run log (most recent lines)
_log_lines = {}
_LOG_MAX = 500


def _log(line, user_id=None):
    print(line)
    if user_id:
        lines = _log_lines.setdefault(user_id, [])
        lines.append(str(line))
        del lines[:-_LOG_MAX]


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
    profile = database.get_settings(active_user()["id"])
    if not profile or not profile["cv_text"]:
        return {"exists": False, "chars": 0, "preview": ""}
    text = profile["cv_text"]
    snippet = " ".join(text.split())[:180]
    return {"exists": True, "chars": len(text), "preview": snippet}


# -- job description (JD) helpers -----------------------------------------

def _load_jd(jd_file):
    """Load a job_description.py-saved JSON dict, or None if missing/unreadable."""
    if not jd_file:
        return None
    row = database.get_job(active_user()["id"], jd_file)
    if not row:
        return None
    return dict(title=row["position"], company=row["company"], url=row["url"],
                description=row["description"], score=row["score"],
                matched=json.loads(row["matched"]) if row["matched"] else None,
                missing=json.loads(row["missing"]) if row["missing"] else None,
                reason=row["reason"], scored_at=row["scored_at"])


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

@app.route("/accounts")
def accounts():
    return render_template("accounts.html", users=database.list_users())


@app.route("/accounts/select", methods=["POST"])
def select_user():
    user = database.user_by_name(request.form.get("username", "").strip())
    if not user:
        abort(404, "unknown username")
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/accounts/create", methods=["POST"])
def create_user():
    try:
        user = database.create_user(request.form.get("username", ""))
    except ValueError as exc:
        abort(400, str(exc))
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/export")
def export_account():
    user = active_user()
    data = portable.export_account(user["id"])
    return send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True,
                     download_name="jobcopilot-{}-{}.zip".format(user["username"], datetime.now().strftime("%Y%m%d")))


@app.route("/import-account", methods=["POST"])
def import_account():
    if not database.VIEWER:
        abort(403)
    file = request.files.get("archive")
    if not file:
        abort(400)
    try:
        user = portable.import_account(file.read(portable.MAX_ARCHIVE + 1), replace=request.form.get("replace") == "1")
    except (ValueError, KeyError, json.JSONDecodeError, __import__("zipfile").BadZipFile) as exc:
        abort(400, str(exc))
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/export-changes")
def export_changes():
    if not database.VIEWER:
        abort(403)
    user = active_user()
    return send_file(io.BytesIO(portable.export_changes(user["id"])), mimetype="application/json",
                     as_attachment=True, download_name="jobcopilot-{}-changes.json".format(user["username"]))


@app.route("/import-changes", methods=["POST"])
def import_changes():
    if database.VIEWER:
        abort(403)
    file = request.files.get("changes")
    if not file:
        abort(400)
    try:
        data = file.read(portable.MAX_ARCHIVE + 1)
        payload = json.loads(data)
        if payload.get("user_id") != active_user()["id"]:
            abort(400, "changes belong to another account")
        result = portable.import_changes(data)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        abort(400, str(exc))
    return render_template("changes_result.html", result=result)


@app.route("/guidance", methods=["POST"])
def save_guidance():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 255:
        abort(400)
    database.save_guidance(active_user()["id"], name, request.form.get("content", ""))
    return redirect(url_for("index"))


@app.route("/guidance/delete", methods=["POST"])
def delete_guidance():
    database.delete_guidance(active_user()["id"], request.form.get("name", ""))
    return redirect(url_for("index"))


@app.route("/credentials", methods=["POST"])
def save_credentials():
    uid = active_user()["id"]
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    if not email or not password:
        abort(400, "email and password are required")
    database.save_credentials(uid, email, password)
    return redirect(url_for("index"))

@app.route("/")
def index():
    if not database.VIEWER:
        from src import runner
    if not current_user():
        return redirect(url_for("accounts"))
    uid = active_user()["id"]
    cfg = settings.load(uid)
    return render_template(
        "config.html",
        cfg=cfg,
        cv=_cv_status(),
        running=False if database.VIEWER else runner.running_user() == uid,
        prompts=settings.PROMPTS,
        matching_notes_files=settings.matching_notes_files(uid),
        guidance=database.list_guidance(uid),
        credentials_saved=bool(database.get_credentials(uid)) if not database.VIEWER else False,
    )


@app.route("/save", methods=["POST"])
def save():
    active_user()
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
    settings.save(active_user()["id"], values)
    return redirect(url_for("index"))


@app.route("/upload-cv", methods=["POST"])
def upload_cv():
    active_user()
    file = request.files.get("cv_file")
    if not file or not file.filename:
        return redirect(url_for("index"))
    try:
        content = file.read()
        text = _extract_text(file.filename, content).strip()
        database.save_cv(active_user()["id"], text, file.filename, content)
        _log("[web] CV uploaded ({} chars) from {}".format(len(text), file.filename), active_user()["id"])
    except Exception as exc:
        _log("[web] CV upload failed: {}".format(exc), active_user()["id"])
    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
def start():
    from src import runner
    user_id = active_user()["id"]
    if runner.is_running():
        _log("[web] run already in progress", user_id)
    else:
        _log_lines[user_id] = []
        _log("[web] starting analysis...", user_id)
        threading.Thread(target=runner.run, args=(user_id,), kwargs={"on_log": lambda line: _log(line, user_id)}, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    from src import runner
    if runner.running_user() == active_user()["id"]:
        _log("[web] stop requested", active_user()["id"])
        runner.request_stop()
    return redirect(url_for("index"))


@app.route("/status")
def status():
    if not database.VIEWER:
        from src import runner
    uid = active_user()["id"]
    return {"running": False if database.VIEWER else runner.running_user() == uid,
            "log": "\n".join(_log_lines.get(uid, [])[-200:])}


@app.route("/matches")
def matches():
    sort = request.args.get("sort", "")
    items = Store(active_user()["id"]).list_matches()
    if sort == "asc":
        items = sorted(items, key=lambda m: m.get("score") or 0)
    elif sort == "desc":
        items = sorted(items, key=lambda m: m.get("score") or 0, reverse=True)
    for m in items:
        m["added_at"] = m["added_at"] or 0
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
            "top_score": max(m.get("score") or 0 for m in g),
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
        if database.VIEWER:
            ok = database.edit_offline(active_user()["id"], url, "status", status)
        else:
            ok = Store(active_user()["id"]).set_status(url, status)
        if not ok:
            abort(404)
    return ("", 204)


@app.route("/matches/apply", methods=["POST"])
def matches_apply():
    # called via fetch() from the checkbox's onchange - no page navigation, so
    # just acknowledge instead of redirecting back to /matches
    url = request.form.get("url", "")
    applied = request.form.get("applied") == "1"
    if url:
        Store(active_user()["id"]).set_applied(url, applied)
    return ("", 204)


@app.route("/matches/not-available", methods=["POST"])
def matches_not_available():
    # called via fetch() from the checkbox's onchange - no page navigation, so
    # just acknowledge instead of redirecting back to /matches
    url = request.form.get("url", "")
    not_available = request.form.get("not_available") == "1"
    if url:
        Store(active_user()["id"]).set_not_available(url, not_available)
    return ("", 204)


@app.route("/matches/not-interested", methods=["POST"])
def matches_not_interested():
    # called via fetch() from the checkbox's onchange - no page navigation
    url = request.form.get("url", "")
    not_interested = request.form.get("not_interested") == "1"
    if url:
        Store(active_user()["id"]).set_not_interested(url, not_interested)
    return ("", 204)


@app.route("/matches/generate-cl", methods=["POST"])
def matches_generate_cl():
    # called via fetch() from the Matches page - no page navigation, so the
    # button state updates in place instead of a full reload resetting scroll
    url = request.form.get("url", "")
    if not url:
        return {"ok": False, "error": "missing url"}, 400
    from src import cover_letter, runner
    uid = active_user()["id"]
    match = next((m for m in Store(uid).list_matches() if m.get("url") == url), None)
    if not match:
        return {"ok": False, "error": "match not found"}, 404
    if match["cover_letter"]:
        return {"ok": False, "error": "cover letter already exists"}, 409
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
        cfg = settings.load(uid)
        cl_file = cover_letter.generate(job, runner.load_cv(uid), cfg, uid, url)
        _log("[web] cover letter generated for {} @ {}".format(job["title"], job["company"]))
        return {"ok": True, "file_url": url_for("output_file", name=cl_file)}
    except Exception as exc:
        _log("[web] cover letter generation failed: {}".format(exc))
        return {"ok": False, "error": str(exc)}, 500


@app.route("/visited")
def visited():
    items = Store(active_user()["id"]).list_visited()
    return render_template("visited.html", items=items, count=len(items))


@app.route("/visited/delete", methods=["POST"])
def visited_delete():
    if database.VIEWER:
        abort(403)
    url = request.form.get("url", "")
    if url:
        Store(active_user()["id"]).remove_visited(url)
    return redirect(url_for("visited"))


@app.route("/visited/clear", methods=["POST"])
def visited_clear():
    if database.VIEWER:
        abort(403)
    Store(active_user()["id"]).clear_visited()
    return redirect(url_for("visited"))


@app.route("/file/<path:name>")
def output_file(name):
    doc = database.get_document(active_user()["id"], name)
    if not doc:
        abort(404)
    return send_file(io.BytesIO(doc["content"]), download_name=doc["filename"], as_attachment=True,
                     mimetype="application/pdf" if doc["kind"] == "pdf" else
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.route("/view")
def view_text():
    name = request.args.get("url", "")
    # why-fit / cv-suggestions notes only - the cover letter is a downloadable
    # DOCX, linked directly via output_file() instead
    match = database.get_match(active_user()["id"], name)
    if not match:
        abort(404)
    field = request.args.get("field", "why_fit")
    if field not in ("why_fit", "cv_suggestions"):
        abort(404)
    text = match[field] or ""
    title = field.replace("_", " ").title()
    sort = request.args.get("sort", "")
    back_url = url_for("matches", sort=sort) if sort else url_for("matches")
    return render_template("view_text.html", title=title, text=text, back_url=back_url,
                           edit_field=field, edit_url=name)


@app.route("/matches/edit", methods=["POST"])
def edit_match():
    uid = active_user()["id"]
    url = request.form.get("url", "")
    field = request.form.get("field", "")
    if field not in ("note", "why_fit", "cv_suggestions"):
        abort(400)
    value = request.form.get("value", "")
    ok = (database.edit_offline(uid, url, field, value) if database.VIEWER else
          database.edit_match(uid, url, {field: value}) == "ok")
    if not ok:
        abort(404)
    return redirect(url_for("matches"))


@app.route("/view-matching-notes/<path:name>")
def view_matching_notes(name):
    # read-only viewer for data/matching_notes/*.txt - no edit route, files are
    # managed on disk, this is just for visibility from the config page
    if not name.lower().endswith(".txt"):
        abort(404)
    entry = next((g for g in database.list_guidance(active_user()["id"]) if g["name"] == name), None)
    if not entry:
        abort(404)
    text = entry["content"]
    return render_template("view_text.html", title=name, text=text, back_url=url_for("index"))


@app.route("/view-jd")
def view_jd():
    name = request.args.get("url", "")
    # formats a job_description.py JSON file into readable text and renders it
    # through the same reading page used for why-fit/cv-suggestions - linked
    # from both the Matches and Visited tabs, covers matches and non-matches alike
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
    return render_template("view_text.html", title=jd["title"], text=text, back_url=back_url, back_label=origin)


if __name__ == "__main__":
    import webbrowser

    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
