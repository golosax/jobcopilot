"""Per-user job and match access."""
import json
import time
from datetime import datetime

from . import database


def job_url(job_id):
    return "https://www.linkedin.com/jobs/view/{}/".format(job_id)


class Store:
    def __init__(self, user_id):
        self.user_id = user_id
        self._visited_urls = {j["url"] for j in database.list_jobs(user_id, visited_only=True)}

    def visited(self, url):
        return url in self._visited_urls

    def save_job(self, job, result):
        database.upsert_job(self.user_id, job["url"], company=job.get("company", ""),
                            position=job.get("title", ""), description=job.get("description", ""),
                            score=result.get("score"), matched=json.dumps(result.get("matched")),
                            missing=json.dumps(result.get("missing")), reason=result.get("reason"),
                            scored_at=datetime.now().isoformat(timespec="seconds"))

    def mark_visited(self, url, company="", position="", jd_file=None):
        existing = next((j for j in database.list_jobs(self.user_id) if j["url"] == url), None)
        values = {"visited": True}
        if not existing:
            values.update(company=company, position=position)
        database.upsert_job(self.user_id, url, **values)
        self._visited_urls.add(url)

    def list_visited(self):
        return [dict(j, jd_file=j["url"] if j["description"] else None)
                for j in database.list_jobs(self.user_id, visited_only=True)]

    def remove_visited(self, url):
        removed = bool(database.remove_visited(self.user_id, url))
        self._visited_urls.discard(url)
        return removed

    def clear_visited(self):
        database.remove_visited(self.user_id)
        self._visited_urls.clear()

    def append_match(self, entry):
        url = entry["url"]
        old = next((m for m in database.list_matches(self.user_id) if m["url"] == url), None)
        values = dict(company=entry.get("company", ""), position=entry.get("position", ""),
                      score=entry.get("score"), why_fit=entry.get("why_fit"),
                      cv_suggestions=entry.get("cv_suggestions"))
        if old:
            values["why_fit"] = values["why_fit"] if values["why_fit"] is not None else old["why_fit"]
            values["cv_suggestions"] = values["cv_suggestions"] if values["cv_suggestions"] is not None else old["cv_suggestions"]
        else:
            values["added_at"] = entry.get("added_at", int(time.time()))
        database.upsert_match(self.user_id, url, **values)

    def list_matches(self):
        job_map = {j["url"]: j for j in database.list_jobs(self.user_id)}
        pdf_map = {d["url"]: d["id"] for d in database.list_documents(self.user_id) if d["kind"] == "pdf"}
        return [dict(m, applied=m["status"] == "applied",
                     not_available=m["status"] == "not_available",
                     not_interested=m["status"] == "not_interested",
                     jd_file=m["url"] if job_map.get(m["url"], {}).get("description") else None,
                     cover_letter=m["cover_letter_id"], cover_letter_pdf=pdf_map.get(m["url"]),
                     why_fit_file=m["url"] if m["why_fit"] is not None else None,
                     cv_suggestions_file=m["url"] if m["cv_suggestions"] is not None else None)
                for m in database.list_matches(self.user_id)]

    def set_status(self, url, status):
        return database.edit_match(self.user_id, url, {"status": status}) == "ok"

    def set_applied(self, url, value):
        return self.set_status(url, "applied" if value else "")

    def set_not_available(self, url, value):
        return self.set_status(url, "not_available" if value else "")

    def set_not_interested(self, url, value):
        return self.set_status(url, "not_interested" if value else "")

    def set_cover_letter(self, url, doc_id):
        return database.edit_match(self.user_id, url, {"cover_letter_id": doc_id}) == "ok"

    @property
    def visited_count(self):
        return len(self._visited_urls)

    @property
    def match_count(self):
        return len(database.list_matches(self.user_id))
