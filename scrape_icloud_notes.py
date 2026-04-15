#!/usr/bin/env python3
"""
scrape_icloud_notes.py — Web-scrape Apple Notes from icloud.com/notes using
Playwright. This is the "fragile but no backup needed" path: it drives a real
browser so you can sign in with 2FA, then iterates the note list and dumps
each note's content.

⚠️  Apple changes iCloud web markup frequently. If selectors break, open
    DevTools on icloud.com/notes and update the SELECTORS dict below.

Usage:
    # Basic — opens browser, waits for you to sign in, exports all notes
    python scrape_icloud_notes.py --dest ~/icloud-notes-export

    # Limit to first 20 notes (good for testing)
    python scrape_icloud_notes.py --dest ~/icloud-notes-export --limit 20

    # Increase per-note wait (slow connections / large notes)
    python scrape_icloud_notes.py --dest ~/icloud-notes-export --note-delay 3000

Prerequisites:
    pip install playwright
    python -m playwright install chromium
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError:
    print("ERROR: playwright not installed. Run:")
    print("  pip install playwright")
    print("  python -m playwright install chromium")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CSS / aria selectors — UPDATE THESE when Apple changes the DOM
# ---------------------------------------------------------------------------
SELECTORS = {
    # Sidebar note list items (each clickable row) — try multiple patterns
    # iCloud Notes renders inside an iframe; these target the inner DOM
    "note_list_item": 'css=.notes-navigation-view [role="listbox"] [role="option"]',
    "note_list_item_alt1": 'css=[role="listbox"] [role="option"]',
    "note_list_item_alt2": 'css=.notes-navigation-view [role="grid"] [role="row"]',
    "note_list_item_alt3": 'css=[role="grid"] [role="row"]',
    "note_list_item_alt4": 'css=[role="list"] [role="listitem"]',
    "note_list_item_alt5": 'css=div[role="listbox"] > div',
    "note_list_item_alt6": "css=.note-snippet",
    "note_list_item_alt7": "css=.snippet-container",
    "note_list_item_alt8": "css=.note-list-item",
    "note_list_item_alt9": "css=.notes-navigation-view .note-list .note-item",
    # Note title inside the editor pane
    "note_title": "css=.notes-document-view h1, .editor-container h1, .editor-title, .note-title, h1.title",
    # Note body / content area
    "note_content": "css=.notes-document-view [contenteditable], .editor-container [contenteditable], .notes-document-view .ProseMirror, .editor-content, .note-content, [contenteditable]",
    # Folder / sidebar folder list
    "folder_list_item": 'css=div[role="tree"] div[role="treeitem"]',
    "folder_list_item_alt": "css=.folder-list .folder-item",
    # "All iCloud" / top-level button to show all notes
    "all_notes_button": 'css=div[role="treeitem"]:first-child',
}

ICLOUD_NOTES_URL = "https://www.icloud.com/notes"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    safe = safe.strip('. ')
    return (safe or "Untitled")[:200]


def try_selector(page, *selectors, timeout=5000):
    """Return the first selector that matches at least one element."""
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout, state="attached")
            return sel
        except PwTimeout:
            continue
    return None


def detect_note_list_via_js(page):
    """Use JS heuristics to find the note list container and return a working selector."""
    result = page.evaluate("""() => {
        // Strategy: find repeated sibling elements in the middle column that
        // look like note snippets (contain date-like text + short preview).
        const candidates = document.querySelectorAll('*');
        const seen = {};
        for (const el of candidates) {
            const tag = el.tagName.toLowerCase();
            const cls = el.className || '';
            const role = el.getAttribute('role') || '';
            const parent = el.parentElement;
            if (!parent) continue;
            // Count siblings with same tag+class combo
            const key = tag + '|' + cls + '|' + role + '|parent:' + (parent.className || '');
            if (!seen[key]) seen[key] = {count: 0, sample: null, tag, cls, role, parentCls: parent.className || '', parentRole: parent.getAttribute('role') || ''};
            seen[key].count++;
            if (!seen[key].sample) seen[key].sample = el.innerText?.slice(0, 80) || '';
        }
        // Filter to groups of 3+ siblings (likely a list)
        const lists = Object.values(seen)
            .filter(s => s.count >= 3)
            .sort((a, b) => b.count - a.count)
            .slice(0, 15);
        return lists.map(s => ({
            count: s.count,
            tag: s.tag,
            cls: s.cls,
            role: s.role,
            parentCls: s.parentCls,
            parentRole: s.parentRole,
            sample: s.sample,
        }));
    }""")
    return result


def dump_dom_diagnostic(page):
    """Print DOM info to help the user find the right selector."""
    print("\n🔍 DOM diagnostic — looking for repeated elements that might be note items:")
    candidates = detect_note_list_via_js(page)
    for i, c in enumerate(candidates[:10]):
        sel_hint = c['tag']
        if c['role']:
            sel_hint = f"[role=\"{c['role']}\"]"
        elif c['cls']:
            first_cls = c['cls'].split()[0] if c['cls'] else ''
            sel_hint = f".{first_cls}" if first_cls else c['tag']
        print(f"   {i+1}. {sel_hint}  (×{c['count']})  sample: {c['sample'][:60]}")
    return candidates


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scrape_notes(frame, args, note_sel, parent_page=None):
    """Click through each note in the sidebar and capture its content.

    Note content is rendered on <canvas> in iCloud Notes, so we use
    clipboard-based extraction (Ctrl+A → Ctrl+C) instead of innerText.
    `frame` is the Notes iframe; `parent_page` is the top-level Page
    (needed for keyboard access since Frame has no .keyboard).
    """
    note_delay = args.note_delay  # ms between note clicks

    # parent_page is the REAL Page object with .keyboard — use it directly
    print(f"   parent_page type: {type(parent_page).__name__}")
    print(f"   has keyboard: {hasattr(parent_page, 'keyboard')}")

    items = frame.query_selector_all(note_sel)
    total = len(items)
    print(f"\n📋 Found {total} notes in sidebar")

    if args.limit and args.limit < total:
        total = args.limit
        print(f"   (limiting to {total})")

    # ── Iterate notes ────────────────────────────────────────────────────
    notes = []
    for idx in range(total):
        # Re-query because DOM may have mutated
        items = frame.query_selector_all(note_sel)
        if idx >= len(items):
            print(f"   ⚠️ Note list shrank to {len(items)} — stopping.")
            break

        item = items[idx]

        # Extract title + preview from the sidebar item text
        title = "Untitled"
        sidebar_preview = ""
        try:
            sidebar_text = item.inner_text()
            if sidebar_text:
                lines = [l.strip() for l in sidebar_text.strip().splitlines() if l.strip()]
                if lines:
                    title = lines[0] or "Untitled"
                if len(lines) > 1:
                    sidebar_preview = "\n".join(lines[1:])
        except Exception:
            pass

        # Click note using JS only (avoids Playwright viewport/actionability stalls)
        try:
            item.evaluate("el => { el.scrollIntoView({block:'center'}); el.click(); }")
        except Exception as e:
            print(f"   ⚠️ Could not click note {idx+1}: {e}")
            continue

        frame.wait_for_timeout(note_delay)

        # ── Extract body ─────────────────────────────────────────────────
        body = ""

        # Method 1: Clipboard via parent_page.keyboard
        if parent_page and hasattr(parent_page, 'keyboard'):
            try:
                # Focus the editor area inside the iframe via JS
                frame.evaluate("""() => {
                    const ed = document.querySelector('.notes-document-view')
                            || document.querySelector('.editor-container')
                            || document.querySelector('[contenteditable]');
                    if (ed) ed.focus();
                }""")
                frame.wait_for_timeout(300)

                parent_page.keyboard.press("Control+a")
                parent_page.wait_for_timeout(300)
                parent_page.keyboard.press("Control+c")
                parent_page.wait_for_timeout(500)

                # Read clipboard
                body = parent_page.evaluate("""async () => {
                    try { return await navigator.clipboard.readText(); }
                    catch(e) { return ''; }
                }""").strip()
            except Exception as e:
                print(f"      clipboard failed: {e}")

        # Method 2: innerText from editor area
        if not body:
            try:
                body = frame.evaluate("""() => {
                    const el = document.querySelector('.notes-document-view')
                            || document.querySelector('[contenteditable]')
                            || document.querySelector('.editor-container');
                    return el ? el.innerText : '';
                }""").strip()
            except Exception:
                pass

        # Method 3: All text nodes in the document view
        if not body:
            try:
                body = frame.evaluate("""() => {
                    const el = document.querySelector('.notes-document-view');
                    if (!el) return '';
                    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
                    let text = '';
                    while (walker.nextNode()) text += walker.currentNode.textContent + '\\n';
                    return text.trim();
                }""").strip()
            except Exception:
                pass

        # Method 4: Sidebar preview as last resort
        if not body and sidebar_preview:
            body = sidebar_preview
            print(f"      (sidebar preview only)")

        # Strip the title line from the body if it's duplicated at the top
        if body and body.startswith(title):
            body = body[len(title):].lstrip("\n")

        notes.append({
            "title": title,
            "body": body,
            "body_length": len(body),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

        pct = (idx + 1) / total * 100
        print(f"   [{idx+1}/{total}] ({pct:.0f}%) {title[:50]}  — {len(body)} chars")

    return notes


# ---------------------------------------------------------------------------
# Output (matches apple_notes_export.py manifest format)
# ---------------------------------------------------------------------------

def write_output(notes, dest: Path):
    """Write individual .md files + notes_manifest.json."""
    dest.mkdir(parents=True, exist_ok=True)

    manifest_records = []
    errors = []

    for i, note in enumerate(notes, 1):
        title = note["title"]
        safe = sanitize_filename(title)
        filepath = dest / f"{safe}.md"
        counter = 1
        while filepath.exists():
            filepath = dest / f"{safe}_{counter}.md"
            counter += 1

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("---\n")
                f.write(f'title: "{title}"\n')
                f.write(f'folder: "Unknown"\n')
                f.write(f'account: "iCloud"\n')
                f.write(f'scraped_at: "{note["scraped_at"]}"\n')
                f.write("---\n\n")
                f.write(f"# {title}\n\n")
                f.write(note["body"])
                f.write("\n")

            record = {
                "id": f"note_{i:04d}",
                "title": title,
                "filename": filepath.name,
                "relative_path": str(filepath.relative_to(dest)),
                "absolute_path": str(filepath),
                "icloud_folder": "Unknown",
                "account": "iCloud",
                "extension": ".md",
                "mime_type": "text/markdown",
                "size_bytes": filepath.stat().st_size,
                "body_length": note["body_length"],
                "body_preview": (note["body"][:200] + "...") if len(note["body"]) > 200 else note["body"],
                "created": None,
                "modified": None,
                "scraped_at": note["scraped_at"],
                "category": None,
                "tags": [],
                "notes": "",
            }
            manifest_records.append(record)
        except Exception as e:
            errors.append(f"{title}: {e}")

    total_size = sum(r["size_bytes"] for r in manifest_records)

    manifest = {
        "export_metadata": {
            "source": "apple_notes",
            "method": "icloud_web_scrape",
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_notes": len(manifest_records),
            "total_size_bytes": total_size,
            "backend_used": "playwright_web_scrape",
            "errors": errors,
            "warning": "Web scraping is fragile. Folder names and dates are unavailable via this method.",
        },
        "summary": {
            "total_body_chars": sum(r["body_length"] for r in manifest_records),
            "avg_note_length": (
                sum(r["body_length"] for r in manifest_records) // len(manifest_records)
                if manifest_records else 0
            ),
        },
        "notes": manifest_records,
    }

    manifest_path = dest / "notes_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"\n✅ Export complete!")
    print(f"   Notes: {len(manifest_records)} ({len(errors)} errors)")
    print(f"   Size:  {total_size / 1024:.1f} KB")
    print(f"   Manifest: {manifest_path}")

    return manifest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Apple Notes from icloud.com via Playwright (fragile, no backup needed)"
    )
    parser.add_argument("--dest", default=os.path.expanduser("~/icloud-notes-export"),
                        help="Local destination directory (default: ~/icloud-notes-export)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of notes to scrape")
    parser.add_argument("--note-delay", type=int, default=1500,
                        help="Milliseconds to wait after clicking each note (default: 1500)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Seconds to wait for manual sign-in before aborting (default: 300)")
    parser.add_argument("--headed", action="store_true", default=True,
                        help="Run browser in headed mode (default, required for 2FA)")
    args = parser.parse_args()

    dest = Path(args.dest)

    print("🌐 iCloud Notes Web Scraper (Playwright)")
    print(f"   Destination: {dest}")
    print(f"   Note delay:  {args.note_delay} ms")
    if args.limit:
        print(f"   Limit:       {args.limit} notes")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        # Grant clipboard permissions so Ctrl+C / navigator.clipboard works
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()

        # Navigate to iCloud Notes
        print("🔗 Opening icloud.com/notes …")
        page.goto(ICLOUD_NOTES_URL, wait_until="domcontentloaded")

        # Wait for the user to sign in (2FA, etc.)
        print()
        print("=" * 60)
        print("  SIGN IN to your Apple ID in the browser window.")
        print("  Complete 2FA if prompted.")
        print(f"  You have {args.timeout} seconds.")
        print("=" * 60)
        print()

        # ── Step 1: wait for sign-in to complete ─────────────────────────
        # The flow is: Landing page → Sign In → 2FA → Notes app loads
        # The landing page has class "landing-page". We wait for it to
        # disappear OR for the authenticated Notes iframe to appear.
        deadline = time.time() + args.timeout
        notes_frame = None

        while time.time() < deadline:
            # Check if the authenticated Notes app iframe has appeared.
            # It has data-name="notes" and URL containing /applications/notes
            # (NOT the main page frame whose URL is just icloud.com/notes).
            try:
                iframe_el = page.query_selector('iframe[data-name="notes"]')
                if iframe_el is None:
                    iframe_el = page.query_selector('iframe.child-application')
                if iframe_el:
                    notes_frame = iframe_el.content_frame()
                    if notes_frame:
                        print("📦 Found Notes app iframe!")
                        break
            except Exception:
                pass

            # Also check via frame objects — look for the app URL, NOT the landing page
            if notes_frame is None:
                for frame in page.frames:
                    url = frame.url or ""
                    if "/applications/notes" in url.lower():
                        notes_frame = frame
                        print("📦 Found Notes app frame via URL!")
                        break

            if notes_frame:
                break

            remaining = int(deadline - time.time())
            # Provide different messages depending on what we see
            has_landing = False
            try:
                has_landing = page.query_selector('.landing-page') is not None
            except Exception:
                pass

            if has_landing:
                print(f"   ⏳ On landing page — waiting for sign-in… ({remaining}s remaining)")
            else:
                print(f"   ⏳ Sign-in in progress — waiting for Notes to load… ({remaining}s remaining)")
            page.wait_for_timeout(5000)

        # Fall back to main page if no iframe found
        target = notes_frame or page
        if notes_frame is None:
            print("   ⚠️  No iframe detected — searching main page instead.")

        # ── Step 2: wait for content, then dump DOM to find selectors ─────
        print("   ⏳ Giving the iframe 10s to fully render…")
        target.wait_for_timeout(10000)

        # Dump top-level DOM structure inside the iframe/target
        dom_tree = target.evaluate("""() => {
            function walk(el, depth) {
                if (depth > 6) return '';
                const tag = el.tagName?.toLowerCase() || '';
                const cls = el.className && typeof el.className === 'string' ? el.className.trim() : '';
                const role = el.getAttribute?.('role') || '';
                const childCount = el.children?.length || 0;
                const indent = '  '.repeat(depth);
                let desc = indent + '<' + tag;
                if (cls) desc += ' class="' + cls.slice(0, 80) + '"';
                if (role) desc += ' role="' + role + '"';
                desc += '> (' + childCount + ' children)';
                let result = desc + '\\n';
                if (childCount <= 30) {
                    for (const child of el.children || []) {
                        result += walk(child, depth + 1);
                    }
                } else {
                    result += indent + '  ... (' + childCount + ' children, showing first 5)\\n';
                    for (let i = 0; i < 5; i++) {
                        result += walk(el.children[i], depth + 1);
                    }
                }
                return result;
            }
            return walk(document.body, 0);
        }""")
        print("\n🔍 DOM structure inside iframe/target:")
        print(dom_tree[:5000])

        # ── Step 3: try CSS selectors, then JS fallback ───────────────────
        note_sel = None
        note_sels = [v for k, v in SELECTORS.items() if k.startswith("note_list_item")]
        note_sel = try_selector(target, *note_sels, timeout=5000)

        if note_sel:
            print(f"✅ Matched selector: {note_sel}\n")
        else:
            print("\n⚠️  No CSS selector matched. Trying JS auto-detect…")
            # Find the container with many similar children (the note list)
            auto_sel = target.evaluate("""() => {
                // Find elements with many children that look like a list
                const all = document.querySelectorAll('*');
                let best = null;
                let bestCount = 0;
                for (const el of all) {
                    const kids = el.children;
                    if (kids.length >= 3 && kids.length < 500) {
                        // Check if children are similar (same tag)
                        const tags = new Set();
                        for (const k of kids) tags.add(k.tagName);
                        if (tags.size <= 2) {
                            // Check if children have text content (not just wrappers)
                            let textKids = 0;
                            for (const k of kids) {
                                if (k.innerText && k.innerText.length > 10) textKids++;
                            }
                            if (textKids > bestCount && textKids >= 3) {
                                bestCount = textKids;
                                const cls = el.className && typeof el.className === 'string' ? el.className.trim().split(/\\s+/)[0] : '';
                                const role = el.getAttribute('role') || '';
                                const childTag = kids[0].tagName.toLowerCase();
                                const childCls = kids[0].className && typeof kids[0].className === 'string' ? kids[0].className.trim().split(/\\s+/)[0] : '';
                                const childRole = kids[0].getAttribute('role') || '';
                                best = {
                                    parentSel: cls ? '.' + cls : (role ? '[role=\"' + role + '\"]' : el.tagName.toLowerCase()),
                                    childSel: childCls ? '.' + childCls : (childRole ? '[role=\"' + childRole + '\"]' : childTag),
                                    count: textKids,
                                    sampleText: kids[0].innerText?.slice(0, 80) || '',
                                };
                            }
                        }
                    }
                }
                return best;
            }""")

            if auto_sel:
                sel_str = f"css={auto_sel['parentSel']} > {auto_sel['childSel']}"
                print(f"   🎯 Auto-detected: {sel_str}  (×{auto_sel['count']})")
                print(f"      Sample: {auto_sel['sampleText'][:60]}")
                # Verify it works
                try:
                    target.wait_for_selector(sel_str, timeout=3000, state="attached")
                    note_sel = sel_str
                    print(f"   ✅ Verified — using auto-detected selector\n")
                except Exception:
                    print(f"   ❌ Auto-detected selector didn't work as CSS.")
            else:
                print("   ❌ JS auto-detect found nothing.")

        if note_sel is None:
            print("\n❌ Could not find note list items.")
            print("   Paste the DOM structure above in the chat so we can fix it.")
            browser.close()
            sys.exit(1)

        print("✅ Notes loaded — starting scrape\n")

        notes = scrape_notes(target, args, note_sel, parent_page=page)

        browser.close()

    if not notes:
        print("\n⚠️  No notes were scraped.")
        sys.exit(1)

    write_output(notes, dest)

    print(f"\n🎉 Done! {len(notes)} notes saved to {dest}")
    print("   Next step: pipe notes_manifest.json to your categorization tool.")


if __name__ == "__main__":
    main()
