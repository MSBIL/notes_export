"""
quick_read.py
─────────────
Given an O'Reilly content URL (book, video course, learning path),
extracts in one page visit:

  • Confirmed title
  • Hours to complete  (explicit for videos; estimated from pages for books)
  • GitHub + companion website links from the description / overview
  • Category / topic tags
  • Publisher (for books)

Can also search O'Reilly for a book title and return the best match URL.

Usage (standalone)
------------------
  # Enrich a known URL
  python quick_read.py --url "https://learning.oreilly.com/library/view/..."

  # Search by title and enrich
  python quick_read.py --title "Building Microservices"

  # Output as JSON (useful for piping)
  python quick_read.py --url "..." --json

Programmatic use
----------------
  from quick_read import quick_read_url, search_oreilly_url

  result = quick_read_url(page, "https://learning.oreilly.com/...")
  # returns dict: title, oreilly_url, hours, links, tags, publisher, content_type
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from playwright.sync_api import Page

BASE = "https://learning.oreilly.com"

# ─── URL classification ───────────────────────────────────────────────────────

def classify_url(url: str) -> str:
    if "/library/view/" in url:
        return "book"
    if "/videos/" in url:
        return "video"
    if "/live-training/" in url or "/live/" in url:
        return "live"
    if "/learning-path/" in url:
        return "learning_path"
    if "/course/" in url:
        return "course"
    return "unknown"


# ─── link extraction helpers ──────────────────────────────────────────────────

# GitHub patterns: github.com/owner/repo (not github.com itself)
GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)

# Generic resource websites (not social / CDN noise)
WEBSITE_RE = re.compile(
    r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)

NOISE_DOMAINS = {
    "learning.oreilly.com", "oreilly.com", "twitter.com", "linkedin.com",
    "facebook.com", "instagram.com", "youtube.com", "bit.ly", "t.co",
    "amazon.com", "cdnjs.cloudflare.com", "fonts.googleapis.com",
    "google.com", "microsoft.com", "apple.com", "cloudfront.net",
    "w3.org", "mozilla.org", "schema.org", "ogp.me",
}


def _extract_links_from_text(text: str) -> list[str]:
    """Pull GitHub + useful companion URLs from raw text."""
    found: list[str] = []
    seen: set[str] = set()

    # GitHub first (deduplicate to owner/repo level)
    for m in GITHUB_RE.finditer(text):
        clean = f"https://github.com/{m.group(1)}"
        if clean not in seen:
            seen.add(clean)
            found.append(clean)

    # Other websites
    for m in WEBSITE_RE.finditer(text):
        url = m.group(0).rstrip(".,;)")
        domain = re.sub(r"https?://(?:www\.)?", "", url).split("/")[0].lower()
        if domain in NOISE_DOMAINS:
            continue
        if "github.com" in url:
            continue  # already handled
        if url not in seen:
            seen.add(url)
            found.append(url)

    return found


def _extract_links_from_page(page: Page) -> list[str]:
    """
    Extract links from the description / overview section of the O'Reilly page.
    Combines both anchor href attributes and raw text URLs.
    """
    found: list[str] = []
    seen: set[str] = set()

    # ── anchor tags in description area ──────────────────────────────────────
    for sel in [
        "[data-testid='detail-description'] a[href]",
        ".detail-description a[href]",
        ".book-description a[href]",
        "section.description a[href]",
        "article a[href]",
        ".toc a[href]",
        "main a[href*='github']",
        "main a[href*='://']",
    ]:
        try:
            for a in page.locator(sel).all()[:50]:
                href = (a.get_attribute("href") or "").strip()
                if not href or not href.startswith("http"):
                    continue
                domain = re.sub(r"https?://(?:www\.)?", "", href).split("/")[0].lower()
                if domain in NOISE_DOMAINS:
                    continue
                clean = href.rstrip(".,;)")
                if clean not in seen:
                    seen.add(clean)
                    found.append(clean)
        except Exception:
            pass

    # ── raw text scan for hidden URLs ─────────────────────────────────────────
    try:
        raw = page.evaluate("""() => {
            const sel = [
                '[data-testid="detail-description"]',
                '.detail-description', '.book-description',
                'section.description', '.overview', 'main'
            ];
            for (const s of sel) {
                const el = document.querySelector(s);
                if (el) return el.innerText;
            }
            return document.body.innerText;
        }""")
        for link in _extract_links_from_text(raw or ""):
            if link not in seen:
                seen.add(link)
                found.append(link)
    except Exception:
        pass

    # deduplicate: github ones first, then others
    github = [l for l in found if "github.com" in l]
    others = [l for l in found if "github.com" not in l]
    return github + others


# ─── duration / hours extraction ──────────────────────────────────────────────

def _parse_duration_text(text: str) -> Optional[float]:
    """
    Parse strings like '4 hours', '4h 30m', '4 hrs 30 mins', '1.5 hours'
    into a float number of hours.
    """
    text = text.lower().strip()

    # e.g. "4 hours 30 minutes" or "4h 30m"
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r?s?)?\s*(?:and\s*)?(\d+)\s*m(?:in(?:utes?)?)?", text)
    if m:
        return round(float(m.group(1)) + int(m.group(2)) / 60, 1)

    # e.g. "4 hours" or "4.5 hours"
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r?s?)\b", text)
    if m:
        return round(float(m.group(1)), 1)

    # e.g. "90 minutes"
    m = re.search(r"(\d+)\s*m(?:in(?:utes?)?)?\b", text)
    if m and int(m.group(1)) > 5:
        return round(int(m.group(1)) / 60, 1)

    return None


def _pages_to_hours(pages: int) -> float:
    """Rough estimate: 30 pages/hour for technical books."""
    return round(pages / 30, 1)


def _extract_hours(page: Page, content_type: str) -> Optional[float]:
    """
    Try several strategies to get the hours-to-complete figure.
    """

    # ── strategy 1: explicit duration text on page ────────────────────────────
    duration_selectors = [
        "[data-testid*='duration']",
        "[class*='duration']",
        "[class*='runtime']",
        "[class*='time']",
        ".detail-header-meta",
        "[data-testid='detail-length']",
        ".book-meta",
        "ul.detail-meta li",
        "[data-testid='topic-card'] span",
    ]
    for sel in duration_selectors:
        try:
            for el in page.locator(sel).all()[:6]:
                txt = (el.inner_text() or "").strip()
                h = _parse_duration_text(txt)
                if h:
                    return h
        except Exception:
            pass

    # ── strategy 2: scan full page text for duration patterns ─────────────────
    try:
        meta_text = page.evaluate("""() => {
            const sels = [
                '[data-testid="detail-header"]',
                '.detail-meta', '.book-meta', 'header',
                '[class*="meta"]', 'main'
            ];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el) {
                    const t = el.innerText;
                    if (/\\d+\\s*h(our|r)/i.test(t) || /\\d+\\s*min/i.test(t)) return t;
                }
            }
            return '';
        }""")
        if meta_text:
            h = _parse_duration_text(meta_text)
            if h:
                return h
    except Exception:
        pass

    # ── strategy 3: page count → estimate (books only) ────────────────────────
    if content_type == "book":
        try:
            full_text = page.evaluate("() => document.body.innerText")
            m = re.search(r"(\d{2,4})\s*(?:pages?|pp\.)", full_text or "", re.IGNORECASE)
            if m:
                pages = int(m.group(1))
                if 50 <= pages <= 2000:
                    return _pages_to_hours(pages)
        except Exception:
            pass

    return None


# ─── tags / topics ────────────────────────────────────────────────────────────

def _extract_tags(page: Page) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for sel in [
        "[data-testid='detail-topics'] a",
        "[class*='topic'] a",
        "[class*='tag'] a",
        ".topics a",
        "[aria-label*='topic']",
    ]:
        try:
            for el in page.locator(sel).all()[:15]:
                t = (el.inner_text() or "").strip()
                if t and t.lower() not in seen and len(t) < 50:
                    seen.add(t.lower())
                    tags.append(t)
        except Exception:
            pass
    return tags


# ─── publisher ────────────────────────────────────────────────────────────────

def _extract_publisher(page: Page) -> str:
    for sel in [
        "[data-testid='detail-publisher']",
        "[class*='publisher']",
        ".publisher",
    ]:
        try:
            el = page.locator(sel).first
            if el.count():
                return (el.inner_text() or "").strip()
        except Exception:
            pass
    return ""


# ─── core scrape ──────────────────────────────────────────────────────────────

def quick_read_url(page: Page, url: str, pause: float = 1.5) -> dict:
    """
    Navigate to an O'Reilly content URL and return enriched metadata dict:
      title, oreilly_url, content_type, hours, links, tags, publisher
    """
    content_type = classify_url(url)
    result = {
        "oreilly_url":   url,
        "content_type":  content_type,
        "title":         "",
        "hours":         None,
        "links":         [],
        "tags":          [],
        "publisher":     "",
        "raw_page_text": "",
    }

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        time.sleep(pause)

        # Title
        for sel in [
            "h1[data-testid*='title']", "h1.detail-title",
            "[data-testid='detail-title']", "h1",
        ]:
            try:
                el = page.locator(sel).first
                if el.count():
                    result["title"] = (el.inner_text() or "").strip()
                    if result["title"]:
                        break
            except Exception:
                pass

        result["hours"]     = _extract_hours(page, content_type)
        result["links"]     = _extract_links_from_page(page)
        result["tags"]      = _extract_tags(page)
        result["publisher"] = _extract_publisher(page)

    except Exception as e:
        result["error"] = str(e)

    return result


# ─── search O'Reilly by title ─────────────────────────────────────────────────

def search_oreilly_url(page: Page, title: str, content_hint: str = "") -> Optional[str]:
    """
    Search O'Reilly for `title` and return the URL of the best matching result.
    content_hint can be 'book', 'video', etc. to filter results.
    """
    q = quote_plus(title)
    search_url = f"{BASE}/search/?q={q}"
    if content_hint in ("video", "book"):
        search_url += f"&type={content_hint}"

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(1.5)

        # Result links
        for sel in [
            "a[href*='/library/view/']",
            "a[href*='/videos/']",
            "a[href*='/learning-path/']",
            "[data-testid='search-result'] a",
            ".search-result a[href]",
            "article a[href]",
        ]:
            try:
                els = page.locator(sel).all()
                for el in els[:5]:
                    href = (el.get_attribute("href") or "").strip()
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = BASE + href
                    # Prefer exact or near-exact title match
                    link_text = (el.inner_text() or "").strip().lower()
                    if title.lower()[:20] in link_text or link_text in title.lower():
                        return href
                # If no title match, return first plausible result
                for el in els[:3]:
                    href = (el.get_attribute("href") or "").strip()
                    if href:
                        return BASE + href if href.startswith("/") else href
            except Exception:
                pass
    except Exception:
        pass

    return None


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _cli():
    p = argparse.ArgumentParser(description="Quick-read metadata from an O'Reilly page")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url",   help="Direct O'Reilly URL to scrape")
    g.add_argument("--title", help="Search O'Reilly for this title first")
    p.add_argument("--json",  action="store_true", help="Output as JSON")
    p.add_argument("--pause", type=float, default=1.5)
    args = p.parse_args()

    # import here so the module can be imported without playwright installed check
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from auth import ensure_logged_in

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx, page = ensure_logged_in(browser)

        if args.title:
            print(f"🔍  Searching for: {args.title}")
            url = search_oreilly_url(page, args.title)
            if not url:
                print("❌  No result found.")
                browser.close()
                sys.exit(1)
            print(f"    Found: {url}")
        else:
            url = args.url

        result = quick_read_url(page, url, pause=args.pause)
        ctx.close()
        browser.close()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n📖  Title:     {result['title']}")
        print(f"🔗  URL:       {result['oreilly_url']}")
        print(f"⏱️  Hours:     {result['hours']}")
        print(f"🏷️  Tags:      {', '.join(result['tags'][:5])}")
        print(f"🏢  Publisher: {result['publisher']}")
        if result["links"]:
            print(f"🔗  Resources:")
            for lnk in result["links"][:5]:
                print(f"    {lnk}")


if __name__ == "__main__":
    _cli()
