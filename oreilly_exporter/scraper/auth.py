"""
auth.py
───────
Handles O'Reilly authentication for company SSO on Windows.

SSO (Okta / Azure AD / Google Workspace) cannot be automated —
the script opens a real Chromium window, you complete login once,
and the session cookies are saved to oreilly_auth_state.json.
All subsequent runs reuse the saved session (no login required).

Public API
----------
  ensure_logged_in(page, context) -> None
"""

from __future__ import annotations

import json
import time
from pathlib import Path

AUTH_FILE = Path("oreilly_auth_state.json")
BASE_URL  = "https://learning.oreilly.com"
CHECK_URL = f"{BASE_URL}/playlists/"

# Strings that only appear when NOT logged in
LOGGED_OUT_MARKERS = [
    "sign in",
    "log in",
    "start a free trial",
    "create a free account",
    "try it free",
]


def _is_logged_in(page) -> bool:
    """Return True if the current page looks like a logged-in state."""
    try:
        content = page.content().lower()
        return not any(m in content for m in LOGGED_OUT_MARKERS)
    except Exception:
        return False


def _try_restore_session(browser) -> tuple:
    """
    Try to create a context from the saved auth state.
    Returns (context, page) on success, (None, None) on failure.
    """
    if not AUTH_FILE.exists():
        return None, None
    try:
        ctx  = browser.new_context(storage_state=str(AUTH_FILE))
        page = ctx.new_page()
        page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(1)
        if _is_logged_in(page):
            print("✅  Restored saved session (no login needed).")
            return ctx, page
        page.close()
        ctx.close()
    except Exception as e:
        print(f"  ⚠️  Saved session invalid or expired: {e}")
    return None, None


def ensure_logged_in(browser) -> tuple:
    """
    Returns (context, page) guaranteed to be logged in.

    Flow
    ────
    1. If oreilly_auth_state.json exists and is valid → use it.
    2. Otherwise open a headed browser, print instructions,
       wait until the user completes SSO, save state.
    """

    # ── try saved session first ───────────────────────────────────────────────
    ctx, page = _try_restore_session(browser)
    if ctx and page:
        return ctx, page

    # ── fresh login ───────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  O'Reilly Login Required")
    print("═" * 60)
    print("  A browser window will open.")
    print("  Complete your company SSO login (Okta / Azure / Google).")
    print("  The script will resume automatically once you're in.")
    print("═" * 60 + "\n")

    ctx  = browser.new_context(
        viewport={"width": 1280, "height": 900},
        # A realistic user-agent reduces bot-detection blocks
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30_000)

    # Poll until login is detected (up to 10 minutes)
    for tick in range(600):
        time.sleep(1)
        try:
            current_url = page.url
            if "sign" in current_url.lower():
                continue
            page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=15_000)
            time.sleep(1)
            if _is_logged_in(page):
                break
        except Exception:
            pass
    else:
        raise RuntimeError(
            "Login not detected after 10 minutes.\n"
            "Please run the script again and complete SSO login."
        )

    # Save session
    ctx.storage_state(path=str(AUTH_FILE))
    print(f"\n💾  Session saved → {AUTH_FILE.resolve()}")
    print("    Future runs will skip login automatically.\n")
    return ctx, page
