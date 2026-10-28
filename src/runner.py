"""The analysis loop, reusable by the CLI (main.py) and the web backoffice."""
import os
import random
import subprocess
import sys
import threading

from dotenv import load_dotenv

from . import cv_suggestions, job_description, matcher, runlog, settings, why_fit
from .browser import Browser
from .store import Store

_CV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cv.txt")

# simple single-run guard shared with the web app
_lock = threading.Lock()
_running = False
_stop = threading.Event()


def is_running():
    return _running


def request_stop():
    """Signal a running analysis to stop after the current job."""
    _stop.set()


def _start_keep_awake(emit):
    """Prevent the Mac from sleeping during a run (no mouse wiggling needed).

    Uses macOS `caffeinate`, which holds a power-assertion for as long as it
    runs - it never moves the cursor or steals focus, so it won't disturb you
    if you're working on the laptop in parallel. No-op on non-macOS.
    """
    if sys.platform != "darwin":
        return None
    try:
        # -d display, -i idle system, -m disk, -s system (on AC) sleep prevented
        proc = subprocess.Popen(
            ["caffeinate", "-dims"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        emit("[runner] keep-awake enabled (caffeinate) - Mac won't sleep during the run")
        return proc
    except Exception as exc:
        emit("[runner] could not enable keep-awake: {}".format(exc))
        return None


def _stop_keep_awake(proc):
    if not proc:
        return
    try:
        proc.terminate()
    except Exception:
        pass


def load_cv():
    if not os.path.exists(_CV_PATH):
        return ""
    with open(_CV_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def run(on_log=print):
    """Run one full analysis pass. `on_log(str)` receives progress lines."""
    def emit(msg):
        runlog.log(msg)
        on_log(msg)

    global _running
    with _lock:
        if _running:
            emit("[runner] a run is already in progress")
            return
        _running = True
    _stop.clear()
    keep_awake = _start_keep_awake(emit)

    try:
        load_dotenv()
        cfg = settings.load()
        cv_text = load_cv()
        if not cv_text:
            emit("[runner] no CV found - upload one first")
            return
        if not cfg.get("search_url"):
            emit("[runner] no search_url configured")
            return

        email = os.environ.get("LINKEDIN_EMAIL")
        password = os.environ.get("LINKEDIN_PASSWORD")
        if not email or not password:
            emit("[runner] set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env")
            return

        threshold = cfg["match_threshold"]
        lo, hi = cfg["min_delay_sec"], cfg["max_delay_sec"]
        max_jobs = cfg.get("max_jobs_per_run", 0)
        max_pages = cfg.get("max_pages", 0)

        store = Store()
        browser = Browser(headless=cfg.get("headless", False), chrome_version=cfg.get("chrome_version"), on_log=emit)
        processed = matched = 0

        try:
            browser.login(email, password)
            browser.open_search(cfg["search_url"])

            for job in browser.iterate(max_pages=max_pages, is_visited=store.visited):
                if _stop.is_set():
                    emit("[runner] stop requested - ending run")
                    break
                processed += 1
                try:
                    result = matcher.score(job, cv_text, cfg)
                    score = result["score"]
                    emit("[{}%] {} @ {}".format(score, job["title"], job["company"]))

                    # JD is saved for every scored job (match or not), so a
                    # low/no-score job is still auditable later from the
                    # Visited tab, not just actual matches
                    try:
                        jd_file = job_description.save(job, result, cfg)
                    except Exception as exc:
                        emit("[job_description] failed: {}".format(exc))
                        jd_file = None

                    if score >= threshold:
                        # cover letters are never auto-generated here - only on
                        # demand, via the "Generate CL" button on the Matches page
                        try:
                            why_file = why_fit.generate(job, cv_text, cfg)
                        except Exception as exc:
                            emit("[why_fit] failed: {}".format(exc))
                            why_file = None
                        try:
                            cvtips_file = cv_suggestions.generate(job, cv_text, cfg)
                        except Exception as exc:
                            emit("[cv_suggestions] failed: {}".format(exc))
                            cvtips_file = None
                        store.append_match(
                            {
                                "url": job["url"],
                                "position": job["title"],
                                "company": job["company"],
                                "cover_letter": None,
                                "why_fit_file": why_file,
                                "cv_suggestions_file": cvtips_file,
                                "jd_file": jd_file,
                                "score": score,
                                "applied": False,
                            }
                        )
                        matched += 1
                        emit(
                            "  -> match saved (why: {}, cv tips: {})".format(
                                why_file, cvtips_file
                            )
                        )

                    store.mark_visited(job["url"], job.get("company", ""), job.get("title", ""), jd_file=jd_file)
                except Exception as exc:
                    emit("[matcher] failed for {}: {}".format(job["job_id"], exc))

                if max_jobs and processed >= max_jobs:
                    emit("[runner] reached max_jobs_per_run ({})".format(max_jobs))
                    break

                delay = random.uniform(lo, hi)
                emit("  ...staying on page ~{:.0f}s".format(delay))
                browser.human_dwell(delay, should_stop=_stop.is_set)
        finally:
            browser.close()

        emit(
            "Done. processed={} matched={} total_visited={} total_matches={}".format(
                processed, matched, store.visited_count, store.match_count
            )
        )
    finally:
        _running = False
        _stop_keep_awake(keep_awake)
