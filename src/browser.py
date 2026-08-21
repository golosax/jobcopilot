"""LinkedIn browsing via undetected-chromedriver.

Handles login (with manual 2FA fallback), walks every job card on every page of
a search-results URL, and extracts the job detail for each one.
"""
import os
import random
import re
import time
import urllib.parse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import runlog

_PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile"))

_CARD_SELECTOR = "div[role='button'][componentkey^='job-card-component-ref-']"
_JOBID_RE = re.compile(r"job-card-component-ref-(\d+)")
_PAGE_SIZE = 25


class Browser:
    def __init__(self, headless=False, chrome_version=None, on_log=None):
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        self._on_log = on_log
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        self.driver = uc.Chrome(
            options=options,
            user_data_dir=_PROFILE_DIR,
            headless=headless,
            version_main=chrome_version,
        )
        self.wait = WebDriverWait(self.driver, 25)

    # -- lifecycle -------------------------------------------------------

    def _log(self, msg):
        if self._on_log:
            self._on_log(msg)
        else:
            runlog.log(msg)
            print(msg)

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def _sleep(self, lo, hi):
        time.sleep(random.uniform(lo, hi))

    # -- login -----------------------------------------------------------

    def _is_logged_in(self):
        url = self.driver.current_url
        if any(x in url for x in ("/login", "/checkpoint", "/authwall", "/uas/")):
            return False
        if self.driver.find_elements(By.CSS_SELECTOR, "input#password, input[name='session_password']"):
            return False
        return True

    def login(self, email, password):
        self.driver.get("https://www.linkedin.com/feed/")
        self._sleep(3, 5)
        if self._is_logged_in():
            self._log("[browser] existing session is valid, skipping login")
            return

        self._log("[browser] logging in")
        self.driver.get("https://www.linkedin.com/login")
        self._sleep(2, 4)
        if self._is_logged_in():
            self._log("[browser] already logged in")
            return
        fields = self.driver.find_elements(By.CSS_SELECTOR, "input#username")
        if fields:
            try:
                fields[0].send_keys(email)
                self._sleep(0.5, 1.5)
                self.driver.find_element(By.CSS_SELECTOR, "input#password").send_keys(password)
                self._sleep(0.5, 1.5)
                self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            except Exception as exc:
                self._log("[browser] could not fill login form: {}".format(exc))
        else:
            self._log("[browser] login form not found, waiting for manual login")

        self._wait_until_logged_in()

    def _wait_until_logged_in(self, timeout=300):
        deadline = time.time() + timeout
        warned = False
        while time.time() < deadline:
            if self._is_logged_in():
                self._log("[browser] login confirmed")
                return
            if not warned:
                self._log("[browser] complete any 2FA / checkpoint in the browser window...")
                warned = True
            time.sleep(3)
        raise RuntimeError("login not completed within timeout")

    # -- search iteration ------------------------------------------------

    def open_search(self, url):
        self._search_url = url
        self._page = 1
        self.driver.get(url)
        self._wait_for_cards()

    def _page_url(self, page):
        """Build the search URL for a 1-based page using the &start= parameter."""
        start = (page - 1) * _PAGE_SIZE
        parts = urllib.parse.urlparse(self._search_url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["start"] = str(start)
        return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query)))

    def _open_page(self, page):
        self._page = page
        url = self._search_url if page == 1 else self._page_url(page)
        self.driver.get(url)
        self._sleep(1.5, 3.0)
        try:
            self._wait_for_cards()
        except Exception:
            pass

    def _on_search_page(self):
        return "/jobs/" in self.driver.current_url and bool(self._all_cards())

    def _recover_if_drifted(self):
        """If the page wandered off the search results, reopen the current page."""
        if self._on_search_page():
            return False
        self._log(
            "[browser] drifted off search ({}) - reopening page {}".format(
                self.driver.current_url, self._page
            )
        )
        self._open_page(self._page)
        return True

    def _wait_for_cards(self):
        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, _CARD_SELECTOR)))
        self._sleep(1.5, 3.0)

    def _all_cards(self):
        return self.driver.find_elements(By.CSS_SELECTOR, _CARD_SELECTOR)

    def _card_id(self, card):
        key = card.get_attribute("componentkey") or ""
        m = _JOBID_RE.search(key)
        return m.group(1) if m else None

    def _open_card_el(self, card, job_id):
        """Click a live card element (avoids stale ids from the virtualized list)."""
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
            self._sleep(0.8, 1.8)
            try:
                card.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", card)
        except Exception:
            return False
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "a[href*='/jobs/view/{}']".format(job_id))
                )
            )
        except Exception:
            return False
        self._sleep(1.0, 2.0)
        return True

    def _scroll_list_down(self):
        """Advance the virtualized list by one chunk to render more cards."""
        cards = self._all_cards()
        if cards:
            try:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'end'});", cards[-1]
                )
            except Exception:
                pass
        try:
            self.driver.execute_script("window.scrollBy(0, 700);")
        except Exception:
            pass

    def _load_more_cards(self, handled, attempts=5):
        """Scroll harder (with retries) to render more cards. True if new appeared."""
        for _ in range(attempts):
            self._scroll_list_down()
            self._sleep(1.5, 2.8)
            for card in self._all_cards():
                jid = self._card_id(card)
                if jid and jid not in handled:
                    return True
        return False

    def _expand_description(self):
        btns = self.driver.find_elements(
            By.CSS_SELECTOR, "button[data-testid='expandable-text-button']"
        )
        for btn in btns:
            try:
                self.driver.execute_script("arguments[0].click();", btn)
                self._sleep(0.4, 0.9)
            except Exception:
                pass

    def _read_detail(self, job_id):
        title = ""
        links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/jobs/view/{}']".format(job_id))
        if links:
            title = links[0].text.strip()

        company = ""
        comp_els = self.driver.find_elements(By.CSS_SELECTOR, "div[aria-label^='Company,']")
        if comp_els:
            label = comp_els[0].get_attribute("aria-label") or ""
            company = label.replace("Company,", "").strip().rstrip(".").strip()
        if not company:
            comp_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/company/']")
            if comp_links:
                company = comp_links[0].text.strip()

        description = ""
        desc_els = self.driver.find_elements(
            By.CSS_SELECTOR,
            "div[componentkey^='JobDetails_AboutTheJob_'] span[data-testid='expandable-text-box']",
        )
        if desc_els:
            description = desc_els[0].text.strip()

        return {
            "job_id": job_id,
            "url": "https://www.linkedin.com/jobs/view/{}/".format(job_id),
            "title": title,
            "company": company,
            "description": description,
        }

    def _first_card_id(self):
        cards = self._all_cards()
        return self._card_id(cards[0]) if cards else None

    def iterate(self, max_pages=0, is_visited=None):
        """Yield job detail dicts for every card across every page.

        Pages are navigated by URL (&start=N), so a fresh page loads each time
        and the browser can't drift off the results while scroll-hunting a Next
        button. Within a page the virtualized list is scrolled to render all
        cards, which are clicked from live element references. Stops at
        `max_pages` (> 0) or when a page repeats earlier results / is empty.
        `is_visited(url)` skips a card before clicking or reading it.
        """
        page = 1
        seen_first_ids = []
        while True:
            self._recover_if_drifted()
            first_id = self._first_card_id()
            if not first_id:
                self._log("[browser] page {} has no results - stopping".format(page))
                break
            if first_id in seen_first_ids:
                self._log("[browser] page {} repeats earlier results - reached the end".format(page))
                break
            seen_first_ids.append(first_id)

            self._log("[browser] processing page {}".format(page))
            handled = set()
            while True:
                cards = self._all_cards()
                acted = False
                for card in cards:
                    jid = self._card_id(card)
                    if not jid or jid in handled:
                        continue
                    handled.add(jid)
                    acted = True
                    url = "https://www.linkedin.com/jobs/view/{}/".format(jid)
                    if is_visited and is_visited(url):
                        self._log("[browser] skip already visited {}".format(jid))
                        break  # re-query the (virtualized) card list
                    try:
                        if not self._open_card_el(card, jid):
                            self._log("[browser] could not open job {}".format(jid))
                            self._recover_if_drifted()
                        else:
                            self._expand_description()
                            detail = self._read_detail(jid)
                            if detail["description"]:
                                yield detail
                            else:
                                self._log("[browser] no description for job {}".format(jid))
                    except Exception as exc:
                        self._log("[browser] error on job {}: {}".format(jid, exc))
                    break  # DOM changed after click - re-query the card list
                if not acted:
                    if not self._load_more_cards(handled):
                        break

            self._log("[browser] finished page {} ({} cards)".format(page, len(handled)))
            if max_pages and page >= max_pages:
                self._log("[browser] reached max_pages ({})".format(max_pages))
                break
            page += 1
            self._open_page(page)

    def human_dwell(self, seconds, should_stop=None):
        """Stay on the current job for `seconds`, scrolling like a human reader.

        Returns early if `should_stop()` becomes True.
        """
        end = time.time() + seconds
        while time.time() < end:
            if should_stop and should_stop():
                return
            delta = random.randint(150, 550)
            if random.random() < 0.25:
                delta = -delta
            try:
                self.driver.execute_script("window.scrollBy(0, arguments[0]);", delta)
            except Exception:
                pass
            remaining = end - time.time()
            if remaining <= 0:
                break
            time.sleep(min(random.uniform(2.0, 6.0), remaining))
