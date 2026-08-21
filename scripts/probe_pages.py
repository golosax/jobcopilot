"""Read-only probe: verify &start= pagination returns distinct pages past page 4.

Does NOT call Claude or open individual jobs. Uses the persisted browser session.
"""
import sys

from src import settings
from src.browser import Browser


def main():
    cfg = settings.load()
    url = cfg["search_url"]
    pages_to_check = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    b = Browser(headless=cfg.get("headless", False), chrome_version=cfg.get("chrome_version"))
    try:
        b.open_search(url)
        first_ids = []
        for page in range(1, pages_to_check + 1):
            if page > 1:
                b._open_page(page)
            b._recover_if_drifted()
            # nudge the virtualized list so a few cards render
            b._load_more_cards(set(), attempts=2)
            fid = b._first_card_id()
            count = len(b._all_cards())
            dup = fid in first_ids
            first_ids.append(fid)
            print("page {:>2} | start={:<3} | first_id={} | rendered_cards={} | {}".format(
                page, (page - 1) * 25, fid, count, "DUP/END" if dup else "ok"
            ))
            if fid is None or dup:
                print(">> pagination ended at page {}".format(page))
                break
        distinct = len([x for x in set(first_ids) if x])
        print("\ndistinct pages reached: {}".format(distinct))
        print("RESULT:", "PASS (>4 pages)" if distinct > 4 else "CHECK (<=4 pages)")
    finally:
        b.close()


if __name__ == "__main__":
    main()
