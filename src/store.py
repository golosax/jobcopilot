"""Persistence for visited jobs and matched results.

visited.json holds every job already processed (url + company + position), so
re-runs skip them before clicking the card or calling Claude. matches.json holds
the >= threshold results.
"""
import json
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
_VISITED_PATH = os.path.join(_OUTPUT_DIR, "visited.json")
_MATCHES_PATH = os.path.join(_OUTPUT_DIR, "matches.json")
_LEGACY_SEEN_PATH = os.path.join(_DATA_DIR, "seen_jobs.json")


def job_url(job_id):
    return "https://www.linkedin.com/jobs/view/{}/".format(job_id)


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


class Store:
    def __init__(self):
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        self._visited = {}  # url -> {url, company, position}
        self._load_visited()
        self._matches = _load(_MATCHES_PATH, [])
        self._migrate_legacy_seen()

    # -- visited ---------------------------------------------------------

    def _load_visited(self):
        raw = _load(_VISITED_PATH, [])
        for item in raw:
            if isinstance(item, str):  # legacy: bare url
                self._visited[item] = {"url": item, "company": "", "position": ""}
            elif isinstance(item, dict) and item.get("url"):
                self._visited[item["url"]] = {
                    "url": item["url"],
                    "company": item.get("company", ""),
                    "position": item.get("position", ""),
                }

    def _migrate_legacy_seen(self):
        if os.path.exists(_VISITED_PATH):
            return
        legacy = _load(_LEGACY_SEEN_PATH, None)
        if not legacy:
            return
        for job_id in legacy:
            url = job_url(job_id)
            self._visited[url] = {"url": url, "company": "", "position": ""}
        self._flush_visited()
        print("[store] migrated {} entries from seen_jobs.json".format(len(legacy)))

    def _flush_visited(self):
        with open(_VISITED_PATH, "w", encoding="utf-8") as f:
            json.dump(list(self._visited.values()), f, indent=2, ensure_ascii=False)

    def visited(self, url):
        return url in self._visited

    def mark_visited(self, url, company="", position=""):
        self._visited[url] = {"url": url, "company": company, "position": position}
        self._flush_visited()

    def list_visited(self):
        return list(self._visited.values())

    def remove_visited(self, url):
        if url in self._visited:
            del self._visited[url]
            self._flush_visited()
            return True
        return False

    def clear_visited(self):
        self._visited = {}
        self._flush_visited()

    # -- matches ---------------------------------------------------------

    def append_match(self, entry):
        entry.setdefault("applied", False)
        self._matches.append(entry)
        self._flush_matches()

    def _flush_matches(self):
        with open(_MATCHES_PATH, "w", encoding="utf-8") as f:
            json.dump(self._matches, f, indent=2, ensure_ascii=False)

    def list_matches(self):
        return list(self._matches)

    def set_applied(self, url, applied):
        changed = False
        for m in self._matches:
            if m.get("url") == url:
                m["applied"] = bool(applied)
                changed = True
        if changed:
            self._flush_matches()
        return changed

    @property
    def visited_count(self):
        return len(self._visited)

    @property
    def match_count(self):
        return len(self._matches)
