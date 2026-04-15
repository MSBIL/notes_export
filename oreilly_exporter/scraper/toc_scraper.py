"""
toc_scraper.py
──────────────
Visits a single O'Reilly book / video / learning-path landing page
and extracts everything visible on that one page:

  title        – confirmed title from the page
  authors      – author name(s)
  hours        – duration (explicit for video; estimated from pages for books)
  links        – GitHub repos + companion websites from the description
  toc          – chapter / section titles from the Table of Contents block
  tags         – topic tags shown on the page
  publisher    – publisher name
  content_type – book | video | learning_path | live | course | unknown

Nothing is read from the actual chapter text — this is purely landing-page
metadata, one request per book.

Public API
----------
  from toc_scraper import scrape_landing_page

  result = scrape_landing_page(page, "https://learning.oreilly.com/library/view/...")
  # → dict with keys above
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote_plus

from playwright.sync_api import Page

BASE = "https://learning.oreilly.com"

# ── content type ──────────────────────────────────────────────────────────────

def _classify(url: str) -> str:
    if "/library/view/" in url:   return "book"
    if "/videos/"       in url:   return "video"
    if "/live-training/" in url or "/live/" in url: return "live"
    if "/learning-path/" in url:  return "learning_path"
    if "/course/"       in url:   return "course"
    return "unknown"


# ── links ─────────────────────────────────────────────────────────────────────

GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^\s\"'<>]*)?",
    re.I,
)
URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;%=-]+", re.I)

NOISE = {
    "learning.oreilly.com","oreilly.com","twitter.com","x.com",
    "linkedin.com","facebook.com","youtube.com","bit.ly","t.co",
    "amazon.com","google.com","microsoft.com","apple.com",
    "w3.org","mozilla.org","schema.org","cloudfront.net",
    "fonts.googleapis.com","cdnjs.cloudflare.com","ogp.me",
}

def _domain(url: str) -> str:
    return re.sub(r"https?://(www\.)?","",url).split("/")[0].lower()

def _clean_links(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out:  list[str] = []
    for u in raw:
        u = u.rstrip(".,;)'\"")
        d = _domain(u)
        if d in NOISE or not u.startswith("http"):
            continue
        # normalise github to owner/repo only
        m = GITHUB_RE.match(u)
        if m:
            u = f"https://github.com/{m.group(1)}"
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def _links_from_text(text: str) -> list[str]:
    github = [f"https://github.com/{m.group(1)}"
              for m in GITHUB_RE.finditer(text)]
    other  = [u for u in URL_RE.findall(text)
               if "github.com" not in u and _domain(u) not in NOISE]
    return _clean_links(github + other)


def _extract_links(page: Page) -> list[str]:
    raw: list[str] = []

    # 1. anchor hrefs in description / overview block
    for sel in [
        "[data-testid='detail-description'] a[href]",
        ".detail-description a[href]",
        ".book-description a[href]",
        "[class*='description'] a[href]",
        "[class*='overview']   a[href]",
        "main a[href*='github']",
        "main a[href*='://']",
    ]:
        try:
            for a in page.locator(sel).all()[:60]:
                h = (a.get_attribute("href") or "").strip()
                if h.startswith("http"):
                    raw.append(h)
        except Exception:
            pass

    # 2. raw text scan (catches URLs not wrapped in <a>)
    try:
        txt = page.evaluate("""() => {
            for (const s of ['[data-testid="detail-description"]',
                             '.detail-description','.book-description',
                             '[class*="overview"]','main']) {
                const el = document.querySelector(s);
                if (el) return el.innerText;
            }
            return '';
        }""")
        raw += _links_from_text(txt or "")
    except Exception:
        pass

    # github first, then other
    cl = _clean_links(raw)
    return [u for u in cl if "github.com" in u] + \
           [u for u in cl if "github.com" not in u]


# ── duration / hours ──────────────────────────────────────────────────────────

def _parse_duration(text: str) -> Optional[float]:
    t = text.lower()
    # "4 hours 30 minutes" / "4h30m"
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r?s?)?\s*(?:and\s*)?(\d+)\s*m(?:in)", t)
    if m:
        return round(float(m.group(1)) + int(m.group(2))/60, 1)
    # "4 hours" / "4.5 hrs"
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r?s?)\b", t)
    if m:
        return round(float(m.group(1)), 1)
    # "90 minutes"
    m = re.search(r"(\d+)\s*m(?:in(?:utes?)?)?\b", t)
    if m and int(m.group(1)) > 5:
        return round(int(m.group(1))/60, 1)
    return None

def _extract_hours(page: Page, ctype: str) -> Optional[float]:
    # explicit selectors
    for sel in [
        "[data-testid*='duration']","[class*='duration']","[class*='runtime']",
        "[data-testid='detail-length']",".book-meta li","ul.detail-meta li",
        "[class*='meta'] span","header span",
    ]:
        try:
            for el in page.locator(sel).all()[:8]:
                h = _parse_duration(el.inner_text() or "")
                if h:
                    return h
        except Exception:
            pass

    # full page text scan
    try:
        txt = page.evaluate("""() => {
            for (const s of ['[data-testid="detail-header"]',
                             '.detail-meta','.book-meta','header',
                             '[class*="meta"]']) {
                const el = document.querySelector(s);
                if (el) {
                    const t = el.innerText;
                    if (/\\d+\\s*h(our|r)/i.test(t)||/\\d+\\s*min/i.test(t))
                        return t;
                }
            }
            return '';
        }""")
        h = _parse_duration(txt or "")
        if h:
            return h
    except Exception:
        pass

    # fallback: page count → estimate for books
    if ctype == "book":
        try:
            full = page.evaluate("() => document.body.innerText") or ""
            m = re.search(r"(\d{2,4})\s*(?:pages?|pp\.)", full, re.I)
            if m:
                pages = int(m.group(1))
                if 50 <= pages <= 2000:
                    return round(pages / 30, 1)
        except Exception:
            pass
    return None


# ── table of contents ─────────────────────────────────────────────────────────

# Sections we don't want (nav chrome, not chapters)
TOC_NOISE = {
    "table of contents","contents","preface","foreword","introduction",
    "index","about the author","about the authors","about this book",
    "appendix","acknowledgments","bibliography","glossary","colophon",
    "cover","copyright","dedication","part","section","chapter",
    "","...",
}

def _extract_toc(page: Page) -> list[str]:
    entries: list[str] = []
    seen:    set[str]  = set()

    # Strategy 1 — dedicated TOC block selectors
    for sel in [
        "[data-testid='toc'] li",
        "[data-testid='table-of-contents'] li",
        "nav.toc li",
        ".toc li",
        ".book-toc li",
        "[class*='toc-item']",
        "[class*='toc'] [class*='item']",
        "ol.toc > li",
        "ul.toc > li",
    ]:
        try:
            items = page.locator(sel).all()
            if len(items) >= 3:
                for el in items[:80]:
                    txt = (el.inner_text() or "").strip().splitlines()[0].strip()
                    low = txt.lower()
                    if txt and low not in seen and low not in TOC_NOISE and len(txt) > 2:
                        seen.add(low)
                        entries.append(txt)
                if entries:
                    return entries
        except Exception:
            pass

    # Strategy 2 — look for a "Table of Contents" heading and grab siblings
    try:
        heading = page.locator(
            "h2:has-text('Table of Contents'), h3:has-text('Table of Contents'), "
            "[data-testid*='toc-heading'], [class*='toc-heading']"
        ).first
        if heading.count():
            # Get the next sibling list
            parent = heading.evaluate_handle(
                "el => el.nextElementSibling || el.parentElement?.nextElementSibling"
            )
            if parent:
                txt_block = page.evaluate("el => el ? el.innerText : ''", parent)
                for line in (txt_block or "").splitlines():
                    line = line.strip()
                    if line and len(line) > 3 and line.lower() not in seen \
                       and line.lower() not in TOC_NOISE:
                        seen.add(line.lower())
                        entries.append(line)
    except Exception:
        pass

    # Strategy 3 — "What you'll learn" bullets for video courses
    if not entries:
        for sel in [
            "[data-testid='what-youll-learn'] li",
            "[class*='learning-objectives'] li",
            "[class*='outcomes'] li",
            "ul[class*='learn'] li",
        ]:
            try:
                items = page.locator(sel).all()
                if len(items) >= 2:
                    for el in items[:30]:
                        txt = (el.inner_text() or "").strip()
                        if txt and txt.lower() not in seen:
                            seen.add(txt.lower())
                            entries.append(txt)
                    if entries:
                        return entries
            except Exception:
                pass

    return entries


# ── authors ───────────────────────────────────────────────────────────────────

def _extract_authors(page: Page) -> str:
    for sel in [
        "[data-testid='detail-author'] a",
        "[data-testid='detail-author']",
        "[class*='author'] a",
        ".author-name",
        "[class*='byline'] a",
        "[rel='author']",
    ]:
        try:
            els = page.locator(sel).all()
            if els:
                names = [e.inner_text().strip() for e in els[:6]
                         if e.inner_text().strip()]
                if names:
                    return ", ".join(names)
        except Exception:
            pass
    return ""


# ── tags ──────────────────────────────────────────────────────────────────────

def _extract_tags(page: Page) -> list[str]:
    tags: list[str] = []
    seen: set[str]  = set()
    for sel in [
        "[data-testid='detail-topics'] a",
        "[class*='topic'] a",
        "[class*='tag']  a",
        ".topics a",
        "[aria-label*='topic']",
        "[data-testid='topic-chip']",
    ]:
        try:
            for el in page.locator(sel).all()[:15]:
                t = (el.inner_text() or "").strip()
                if t and t.lower() not in seen and len(t) < 60:
                    seen.add(t.lower())
                    tags.append(t)
        except Exception:
            pass
    return tags


# ── publisher ─────────────────────────────────────────────────────────────────

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


# ── title ─────────────────────────────────────────────────────────────────────

def _extract_title(page: Page) -> str:
    for sel in [
        "[data-testid='detail-title']",
        "h1[class*='title']",
        ".detail-title",
        "h1",
    ]:
        try:
            el = page.locator(sel).first
            if el.count():
                t = (el.inner_text() or "").strip()
                if t:
                    return t
        except Exception:
            pass
    return ""


# ── search ────────────────────────────────────────────────────────────────────

def search_oreilly(page: Page, title: str) -> Optional[str]:
    """Search O'Reilly and return the best matching content URL."""
    q = quote_plus(title)
    try:
        page.goto(f"{BASE}/search/?q={q}", wait_until="domcontentloaded", timeout=20_000)
        time.sleep(1.2)

        content_sels = [
            "a[href*='/library/view/']",
            "a[href*='/videos/']",
            "a[href*='/learning-path/']",
        ]
        for sel in content_sels:
            els = page.locator(sel).all()
            for el in els[:5]:
                href = (el.get_attribute("href") or "").strip()
                if not href:
                    continue
                href = BASE + href if href.startswith("/") else href
                link_text = (el.inner_text() or "").strip().lower()
                # Prefer a match where the first ~20 chars of the title appear
                if title.lower()[:20] in link_text:
                    return href
            # No title match — take the first plausible result
            for el in els[:2]:
                href = (el.get_attribute("href") or "").strip()
                if href:
                    return BASE + href if href.startswith("/") else href
    except Exception:
        pass
    return None


# ── main entry ────────────────────────────────────────────────────────────────

def scrape_landing_page(page: Page, url: str, pause: float = 1.5) -> dict:
    """
    Load one O'Reilly landing page and return a metadata dict:
      title, authors, content_type, hours, links, toc, tags, publisher, oreilly_url
    """
    ctype = _classify(url)
    result: dict = {
        "oreilly_url":  url,
        "content_type": ctype,
        "title":        "",
        "authors":      "",
        "hours":        None,
        "links":        [],
        "toc":          [],
        "tags":         [],
        "publisher":    "",
        "error":        "",
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        time.sleep(pause)

        result["title"]     = _extract_title(page)
        result["authors"]   = _extract_authors(page)
        result["hours"]     = _extract_hours(page, ctype)
        result["links"]     = _extract_links(page)
        result["toc"]       = _extract_toc(page)
        result["tags"]      = _extract_tags(page)
        result["publisher"] = _extract_publisher(page)
    except Exception as e:
        result["error"] = str(e)

    return result
