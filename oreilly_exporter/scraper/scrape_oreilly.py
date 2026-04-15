"""
scrape_oreilly.py
─────────────────
Scrapes three content collection types from learning.oreilly.com:

  1. My Playlists     – user-created lists at /playlists/
  2. Expert Playlists – O'Reilly curated lists (same page, different badge)
  3. Learning Paths   – structured paths in the account

For each collection it extracts every item:
  title · url · content_type · author · duration · position

Handles sub-lists: if a playlist item is itself a playlist link it
is flagged as sub_list=True and its children are scraped recursively
(max depth 2 to avoid infinite loops).

Outputs
-------
  output/oreilly_raw.json   – all collections + items (resume-safe)

Usage
-----
  python scrape_oreilly.py
  python scrape_oreilly.py --types playlists expert_playlists
  python scrape_oreilly.py --resume          # skip already-done collections
  python scrape_oreilly.py --pause 1.5       # slower for flaky connections
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# bring in auth helper (same package)
import sys
sys.path.insert(0, str(Path(__file__).parent))
from auth import ensure_logged_in

# ─── constants ────────────────────────────────────────────────────────────────

BASE          = "https://learning.oreilly.com"
PLAYLISTS_URL = f"{BASE}/playlists/"
PATHS_URL     = f"{BASE}/home/"          # learning paths live in the home dashboard

OUTPUT_FILE   = Path("../output/oreilly_raw.json")

# Content-type detection from URL fragments
TYPE_PATTERNS = {
    "book":          r"/library/view/",
    "video":         r"/videos/",
    "live-training": r"/live-training/|/live/",
    "course":        r"/course/|/learning-path/",
    "sandbox":       r"/scenarios/|/sandbox/",
    "article":       r"/library/view/.*?/ch\d|/articles/",
}

# Noise text to skip when reading item titles
SKIP_TITLES = {
    "", "share", "sign out", "sign in", "home", "playlists", "settings",
    "search", "back", "try it free", "start free trial", "more",
    "privacy policy", "terms of service", "contact us", "help",
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE + href
    return href


def detect_type(url: str) -> str:
    for ctype, pattern in TYPE_PATTERNS.items():
        if re.search(pattern, url):
            return ctype
    return "unknown"


def auto_scroll(page, rounds: int = 40, pause: float = 0.7) -> None:
    last = 0
    for _ in range(rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        h = page.evaluate("document.body.scrollHeight")
        if h == last:
            break
        last = h


def safe_text(el) -> str:
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def save_progress(data: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_progress() -> dict:
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"playlists": [], "expert_playlists": [], "learning_paths": []}


# ─── item extraction ──────────────────────────────────────────────────────────

def extract_items_from_page(page, depth: int = 0) -> list[dict]:
    """
    Extract content items from the currently loaded playlist / path page.
    Returns list of item dicts.
    """
    auto_scroll(page, rounds=60, pause=0.5)

    # Try to narrow to the main content area
    try:
        main_el = page.locator("main, [role='main'], #content").first
        anchors  = main_el.locator("a[href]").all()
    except Exception:
        anchors = page.locator("a[href]").all()

    items: list[dict] = []
    seen:  set[tuple] = set()

    for a in anchors:
        try:
            href  = a.get_attribute("href") or ""
            title = safe_text(a)
        except Exception:
            continue

        if not title or title.lower() in SKIP_TITLES or len(title) < 3:
            continue

        url = abs_url(href)
        if not url:
            continue

        # Only keep links that look like O'Reilly content
        is_content = any(p in url for p in [
            "/library/view/", "/videos/", "/live", "/course/",
            "/learning-path/", "/scenarios/", "/sandbox/",
        ])
        # Also flag sub-list links
        is_sub_list = bool(re.search(r"/playlists/[0-9a-fA-F\-]{20,}", url))

        if not is_content and not is_sub_list:
            continue

        key = (title[:100], url)
        if key in seen:
            continue
        seen.add(key)

        # Try to read author and duration from sibling elements
        author   = ""
        duration = ""
        try:
            card = a.evaluate_handle(
                "el => el.closest('li') || el.closest('[class*=\"card\"]') "
                "     || el.closest('[class*=\"item\"]') || el.parentElement"
            )
            card_text = page.evaluate("el => el ? el.innerText : ''", card)
            lines = [l.strip() for l in card_text.splitlines() if l.strip()]
            # heuristic: author is often the second non-title line
            for line in lines[1:4]:
                if line.lower() != title.lower() and not line.startswith("http"):
                    if not author:
                        author = line
                    elif re.search(r"\d+\s*(min|hr|hour|h\b)", line, re.I):
                        duration = line
                        break
        except Exception:
            pass

        item = {
            "title":        title,
            "url":          url,
            "content_type": detect_type(url),
            "author":       author,
            "duration":     duration,
            "is_sub_list":  is_sub_list,
            "depth":        depth,
        }
        items.append(item)

    return items


# ─── sub-list expansion ───────────────────────────────────────────────────────

def expand_sub_lists(page, items: list[dict], pause: float, max_depth: int = 2) -> list[dict]:
    """
    For any item flagged is_sub_list, navigate to it and prepend its
    children into the list (flagged with sub_list_parent).
    """
    expanded: list[dict] = []
    for item in items:
        expanded.append(item)
        if item["is_sub_list"] and item["depth"] < max_depth:
            print(f"      ↳  sub-list detected: {item['title'][:60]}")
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=20_000)
                time.sleep(pause)
                children = extract_items_from_page(page, depth=item["depth"] + 1)
                for child in children:
                    child["sub_list_parent"] = item["title"]
                expanded.extend(children)
            except Exception as e:
                print(f"      ⚠️  Could not expand sub-list: {e}")
    return expanded


# ─── playlist scraper ─────────────────────────────────────────────────────────

EXPERT_MARKERS = [
    "expert playlist", "curated by", "o'reilly", "oreilly",
    "learning path by", "recommended by",
]

def _is_expert_playlist(name: str, badge_text: str) -> bool:
    combined = (name + " " + badge_text).lower()
    return any(m in combined for m in EXPERT_MARKERS)


def scrape_playlists(page, pause: float, resume_ids: set[str]) -> tuple[list, list]:
    """
    Returns (my_playlists, expert_playlists).
    Each entry: {id, name, url, source, items:[...]}
    """
    print("\n📋  Loading playlists page …")
    page.goto(PLAYLISTS_URL, wait_until="domcontentloaded", timeout=30_000)
    time.sleep(2)
    auto_scroll(page, rounds=50, pause=0.6)

    # ── gather playlist cards ──────────────────────────────────────────────────
    # O'Reilly uses anchors with UUIDs in the /playlists/<uuid>/ pattern
    all_anchors = page.locator("a[href*='/playlists/']").all()
    seen_urls:  dict[str, str] = {}   # url → name

    for a in all_anchors:
        href = a.get_attribute("href") or ""
        if not re.search(r"/playlists/[0-9a-fA-F\-]{20,}", href):
            continue
        url  = abs_url(href)
        name = safe_text(a)
        if not name:
            # try parent card text
            try:
                name = page.evaluate(
                    "el => { "
                    "  let p = el.closest('li') || el.closest('[class*=\"card\"]') || el.parentElement; "
                    "  return p ? p.innerText.split('\\n')[0].trim() : ''; "
                    "}", a.element_handle()
                )
            except Exception:
                pass
        if name and url:
            seen_urls[url] = name

    # ── classify into mine vs expert ──────────────────────────────────────────
    my_raw:     list[tuple[str, str]] = []
    expert_raw: list[tuple[str, str]] = []

    for url, name in seen_urls.items():
        # Try to read badge text near the link
        badge = ""
        try:
            badge = page.evaluate(
                "url => { "
                "  const a = document.querySelector(`a[href*='${url.replace('https://learning.oreilly.com','')}']`); "
                "  if (!a) return ''; "
                "  const card = a.closest('li') || a.closest('[class*=\"card\"]') || a.parentElement; "
                "  return card ? card.innerText : ''; "
                "}", url
            )
        except Exception:
            pass

        if _is_expert_playlist(name, badge):
            expert_raw.append((name, url))
        else:
            my_raw.append((name, url))

    print(f"  Found {len(my_raw)} personal playlists, {len(expert_raw)} expert playlists")

    # ── scrape items for each ──────────────────────────────────────────────────
    def scrape_list(raw_list: list[tuple], source_label: str) -> list[dict]:
        results = []
        for idx, (name, url) in enumerate(raw_list, 1):
            pid = re.search(r"/playlists/([0-9a-fA-F\-]+)", url)
            pid = pid.group(1) if pid else url
            label = f"[{idx}/{len(raw_list)}]"

            if pid in resume_ids:
                print(f"  {label} ⏭  Skipping (already done): {name[:60]}")
                continue

            print(f"  {label} 🔍  {source_label}: {name[:60]}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                time.sleep(pause)
                items = extract_items_from_page(page)
                items = expand_sub_lists(page, items, pause)

                # re-number positions after expansion
                for pos, item in enumerate(items, 1):
                    item["position"] = pos

                entry = {
                    "id":      pid,
                    "name":    name,
                    "url":     url,
                    "source":  source_label,
                    "items":   items,
                }
                results.append(entry)
                print(f"      → {len(items)} items")
            except PWTimeout:
                print(f"      ⚠️  Timeout — skipping")
            except Exception as e:
                print(f"      ⚠️  Error: {e} — skipping")

        return results

    my_playlists     = scrape_list(my_raw,     "my_playlist")
    expert_playlists = scrape_list(expert_raw, "expert_playlist")
    return my_playlists, expert_playlists


# ─── learning path scraper ────────────────────────────────────────────────────

def scrape_learning_paths(page, pause: float, resume_ids: set[str]) -> list[dict]:
    """
    O'Reilly learning paths may appear on the home page or a dedicated
    /learning-path/ listing.  We try both entry points.
    """
    print("\n🎓  Looking for learning paths …")

    candidate_urls: dict[str, str] = {}  # url → title

    # ── approach 1: home dashboard ────────────────────────────────────────────
    try:
        page.goto(f"{BASE}/home/", wait_until="domcontentloaded", timeout=20_000)
        time.sleep(2)
        auto_scroll(page, rounds=30, pause=0.6)

        for a in page.locator("a[href*='/learning-path/']").all():
            href  = a.get_attribute("href") or ""
            title = safe_text(a)
            if title and href:
                candidate_urls[abs_url(href)] = title
    except Exception as e:
        print(f"  ⚠️  Home page scan failed: {e}")

    # ── approach 2: dedicated learning paths listing ───────────────────────────
    for list_url in [
        f"{BASE}/learning-path/",
        f"{BASE}/playlists/?type=learning-path",
    ]:
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)
            auto_scroll(page)
            for a in page.locator("a[href*='/learning-path/']").all():
                href  = a.get_attribute("href") or ""
                title = safe_text(a)
                if title and href and len(title) > 3:
                    candidate_urls[abs_url(href)] = title
        except Exception:
            pass

    # Filter: only actual path detail pages (not root /learning-path/)
    paths_to_scrape = [
        (title, url) for url, title in candidate_urls.items()
        if re.search(r"/learning-path/.+", url)
        and title.lower() not in SKIP_TITLES
    ]

    print(f"  Found {len(paths_to_scrape)} learning path candidates")

    results: list[dict] = []
    for idx, (title, url) in enumerate(paths_to_scrape, 1):
        pid = re.sub(r"[^a-z0-9]", "_", title.lower())[:40]

        if pid in resume_ids:
            print(f"  [{idx}/{len(paths_to_scrape)}] ⏭  Skipping: {title[:60]}")
            continue

        print(f"  [{idx}/{len(paths_to_scrape)}] 🔍  Path: {title[:60]}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            time.sleep(pause)
            items = extract_items_from_page(page)
            for pos, item in enumerate(items, 1):
                item["position"] = pos

            results.append({
                "id":     pid,
                "name":   title,
                "url":    url,
                "source": "learning_path",
                "items":  items,
            })
            print(f"      → {len(items)} items")
        except PWTimeout:
            print(f"      ⚠️  Timeout — skipping")
        except Exception as e:
            print(f"      ⚠️  Error: {e} — skipping")

    return results


# ─── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Export O'Reilly playlists and learning paths")
    p.add_argument(
        "--types", nargs="+",
        choices=["playlists", "expert_playlists", "learning_paths"],
        default=["playlists", "expert_playlists", "learning_paths"],
    )
    p.add_argument("--pause",   type=float, default=1.2,
                   help="Seconds between page interactions (default 1.2)")
    p.add_argument("--resume",  action="store_true",
                   help="Skip collections already in oreilly_raw.json")
    p.add_argument("--out",     default="../output/oreilly_raw.json")
    return p.parse_args()


def main():
    args = parse_args()
    global OUTPUT_FILE
    OUTPUT_FILE = Path(args.out)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load previous progress if resuming
    data = load_progress() if args.resume else {
        "playlists": [], "expert_playlists": [], "learning_paths": []
    }
    done_ids: set[str] = set()
    if args.resume:
        for section in data.values():
            done_ids.update(c["id"] for c in section)
        print(f"🔄  Resume mode: {len(done_ids)} collections already exported")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx, page = ensure_logged_in(browser)

        types = args.types

        if "playlists" in types or "expert_playlists" in types:
            my_pl, ex_pl = scrape_playlists(page, args.pause, done_ids)
            if "playlists"        in types: data["playlists"].extend(my_pl)
            if "expert_playlists" in types: data["expert_playlists"].extend(ex_pl)
            save_progress(data)

        if "learning_paths" in types:
            lp = scrape_learning_paths(page, args.pause, done_ids)
            data["learning_paths"].extend(lp)
            save_progress(data)

        ctx.close()
        browser.close()

    # ── summary ───────────────────────────────────────────────────────────────
    total_items = sum(
        len(c["items"])
        for section in data.values()
        for c in section
    )
    print(f"\n{'═'*55}")
    print(f"✅  Done.")
    print(f"    My playlists:     {len(data['playlists'])}")
    print(f"    Expert playlists: {len(data['expert_playlists'])}")
    print(f"    Learning paths:   {len(data['learning_paths'])}")
    print(f"    Total items:      {total_items}")
    print(f"    Raw JSON:         {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
