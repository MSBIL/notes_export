"""
scrape_notes.py
───────────────
Playwright-based iCloud Notes scraper.

Usage
-----
  python scrape_notes.py                        # scrape all notes
  python scrape_notes.py --folder "Read"        # only one folder
  python scrape_notes.py --limit 20             # stop after 20 notes
  python scrape_notes.py --out ../output/notes_export.json

How it works
------------
1. Opens iCloud Notes in a headed Chromium window.
2. Pauses so you can log in (with 2FA) in the real browser.
3. Iterates every visible note in every sidebar folder.
4. Saves title / folder / body / timestamp to JSON.

Tips
----
- Run `playwright codegen https://www.icloud.com/notes` first to
  inspect live selectors if Apple updates their UI.
- Use --folder to export one folder at a time; merge later with
  utils/merge_exports.py.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─── selector banks (iCloud can change these) ────────────────────────────────
FOLDER_SELECTORS = [
    'li[role="treeitem"]',
    '[data-type="folder"]',
    '.folder-item',
    'li.source',
]

NOTE_SELECTORS = [
    'li[role="option"]',
    '[role="listitem"]',
    '.list-item',
    '.note-item',
    'li.note',
]

TITLE_SELECTORS = [
    'input[aria-label*="title" i]',
    '.editor-title',
    '.note-title',
    'h1',
    '[data-testid="note-title"]',
]

BODY_SELECTORS = [
    '[contenteditable="true"]',
    '.ck-content',
    '.note-editor',
    '.editor',
    'article',
    '[role="main"] [contenteditable]',
]

FOLDER_LABEL_SELECTORS = [
    '[aria-label*="folder" i]',
    '.folder-name',
    '.source-list-selected .source-title',
    '.sidebar-selected',
]


# ─── helpers ─────────────────────────────────────────────────────────────────

def safe_text(locator) -> str:
    try:
        if locator.count() > 0:
            return locator.first.inner_text().strip()
    except Exception:
        pass
    return ""


def try_selectors(page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                text = loc.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def current_folder_name(page) -> str:
    return try_selectors(page, FOLDER_LABEL_SELECTORS)


def scroll_note_list(page, note_sel: str, pause: float = 1.0):
    """Scroll the note list to load lazy-rendered items."""
    try:
        container = page.locator(note_sel).first.evaluate_handle(
            "el => el.closest('[style*=\"overflow\"]') || el.parentElement"
        )
        page.evaluate("(el) => { el.scrollTop += 2000; }", container)
        time.sleep(pause)
    except Exception:
        pass


# ─── main ────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Export iCloud Notes via Playwright")
    p.add_argument("--folder", default=None, help="Scrape only this folder name")
    p.add_argument("--limit",  type=int, default=0, help="Stop after N notes (0 = all)")
    p.add_argument("--out",    default="../output/notes_export.json",
                   help="Output JSON path")
    p.add_argument("--wait-login", type=int, default=0,
                   help="Extra seconds to wait after you press Enter (default 0)")
    p.add_argument("--pause",  type=float, default=1.2,
                   help="Seconds to wait after clicking a note (default 1.2)")
    return p.parse_args()


def scrape(args) -> list[dict]:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    exported: list[dict] = []
    seen: set[tuple] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        print("\n🔗  Opening iCloud Notes …")
        page.goto("https://www.icloud.com/notes", wait_until="domcontentloaded")

        print("\n🔐  Please log in (including any 2FA) in the browser window.")
        input("    Press Enter here once Notes has fully loaded ► ")

        if args.wait_login:
            print(f"    Waiting an extra {args.wait_login}s …")
            time.sleep(args.wait_login)

        # ── gather folders from sidebar ───────────────────────────────────────
        folders_to_visit: list = []

        for sel in FOLDER_SELECTORS:
            items = page.locator(sel)
            if items.count() > 0:
                print(f"  Found {items.count()} sidebar items with selector «{sel}»")
                folders_to_visit = [items.nth(i) for i in range(items.count())]
                break

        if not folders_to_visit:
            print("  ⚠️  Could not detect folder list — will scrape whatever is visible.")
            folders_to_visit = [None]  # sentinel → skip folder-clicking step

        for folder_handle in folders_to_visit:
            folder_name = ""

            if folder_handle is not None:
                try:
                    folder_name = folder_handle.inner_text().strip().splitlines()[0]
                except Exception:
                    folder_name = ""

                if args.folder and folder_name and args.folder.lower() not in folder_name.lower():
                    continue  # skip folders the user didn't request

                try:
                    folder_handle.click()
                    page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"  ⚠️  Could not click folder "{folder_name}": {e}")
                    continue

            print(f"\n📂  Folder: «{folder_name or '(current)'}»")

            # ── find note list items ──────────────────────────────────────────
            note_loc = None
            for sel in NOTE_SELECTORS:
                loc = page.locator(sel)
                if loc.count() > 0:
                    note_loc = loc
                    break

            if note_loc is None:
                print("    No notes found in this folder.")
                continue

            count = note_loc.count()
            print(f"    {count} note entries visible")

            for i in range(count):
                if args.limit and len(exported) >= args.limit:
                    print(f"\n✅  Reached --limit of {args.limit}. Stopping.")
                    break

                item = note_loc.nth(i)
                try:
                    item.click()
                    page.wait_for_timeout(int(args.pause * 1000))
                except Exception as e:
                    print(f"    ⚠️  Could not click note {i}: {e}")
                    continue

                title  = try_selectors(page, TITLE_SELECTORS)
                body   = try_selectors(page, BODY_SELECTORS)
                folder = folder_name or current_folder_name(page)

                key = (title, body[:200])
                if not (title or body):
                    continue
                if key in seen:
                    continue
                seen.add(key)

                note = {
                    "id":      f"note_{len(exported) + 1:04d}",
                    "folder":  folder,
                    "title":   title,
                    "raw_text": body,
                    "source":  "icloud_notes",
                    "status":  "inbox",
                }
                exported.append(note)
                print(f"    ✔  [{len(exported):04d}] {title[:70] or '(no title)'}")

            # scroll to load more and retry once
            scroll_note_list(page, NOTE_SELECTORS[0])
            new_count = note_loc.count()
            if new_count > count:
                print(f"    Scrolled — {new_count - count} more items appeared")
                for i in range(count, new_count):
                    if args.limit and len(exported) >= args.limit:
                        break
                    item = note_loc.nth(i)
                    try:
                        item.click()
                        page.wait_for_timeout(int(args.pause * 1000))
                    except Exception:
                        continue

                    title  = try_selectors(page, TITLE_SELECTORS)
                    body   = try_selectors(page, BODY_SELECTORS)
                    folder = folder_name or current_folder_name(page)
                    key = (title, body[:200])
                    if (title or body) and key not in seen:
                        seen.add(key)
                        note = {
                            "id":       f"note_{len(exported) + 1:04d}",
                            "folder":   folder,
                            "title":    title,
                            "raw_text": body,
                            "source":   "icloud_notes",
                            "status":   "inbox",
                        }
                        exported.append(note)
                        print(f"    ✔  [{len(exported):04d}] {title[:70] or '(no title)'}")

        browser.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(exported, f, ensure_ascii=False, indent=2)

    print(f"\n💾  Saved {len(exported)} notes → {out_path.resolve()}")
    return exported


if __name__ == "__main__":
    args = parse_args()
    scrape(args)
