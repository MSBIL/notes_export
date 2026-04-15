"""
run_pipeline.py
───────────────
End-to-end orchestrator for the iCloud Notes export pipeline.

Steps
-----
  1. scrape   – Playwright → notes_export.json
  2. enrich   – OpenAI     → notes_enriched.json
  3. convert  – JSON       → notes_export.csv + notes_archive.md + tasks.md

Usage
-----
  # Full pipeline (all three steps)
  python run_pipeline.py

  # Only scrape (skip enrich + convert)
  python run_pipeline.py --steps scrape

  # Only enrich (already have notes_export.json)
  python run_pipeline.py --steps enrich

  # Only convert (already have notes_enriched.json)
  python run_pipeline.py --steps convert

  # Scrape + enrich (skip convert)
  python run_pipeline.py --steps scrape enrich

  # Scrape options
  python run_pipeline.py --folder "Read" --limit 50 --pause 1.5

  # Enrich options
  python run_pipeline.py --steps enrich --model gpt-4o --batch-size 10 --resume

  # Skip browser (use existing export, just enrich + convert)
  python run_pipeline.py --steps enrich convert

Environment variables
---------------------
  OPENAI_API_KEY   required for the enrich step
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE      = Path(__file__).parent
SCRAPER   = BASE / "scraper"   / "scrape_notes.py"
CLEANER   = BASE / "cleaner"   / "categorize_notes.py"
UTILS_DIR = BASE / "utils"
CONVERTER = UTILS_DIR / "convert_output.py"
OUTPUT    = BASE / "output"
OUTPUT.mkdir(parents=True, exist_ok=True)

RAW_JSON      = OUTPUT / "notes_export.json"
ENRICHED_JSON = OUTPUT / "notes_enriched.json"


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'─'*60}")
    print(f"▶  {label}")
    print(f"   {' '.join(cmd)}")
    print(f"{'─'*60}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n❌  Step failed (exit code {result.returncode}): {label}")
    return result.returncode


def parse_args():
    p = argparse.ArgumentParser(
        description="iCloud Notes full-pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--steps", nargs="+",
        choices=["scrape", "enrich", "convert"],
        default=["scrape", "enrich", "convert"],
        help="Which steps to run (default: all three)",
    )
    # ── scrape options ──────────────────────────────────────────────────────
    scrape = p.add_argument_group("Scrape options")
    scrape.add_argument("--folder",     default=None,
                        help="Only scrape this folder name")
    scrape.add_argument("--limit",      type=int, default=0,
                        help="Stop after N notes (0 = all)")
    scrape.add_argument("--pause",      type=float, default=1.2,
                        help="Seconds to wait after clicking each note (default 1.2)")
    scrape.add_argument("--wait-login", type=int, default=0,
                        help="Extra seconds to wait after login (default 0)")
    # ── enrich options ──────────────────────────────────────────────────────
    enrich = p.add_argument_group("Enrich options")
    enrich.add_argument("--model",      default="gpt-4o-mini",
                        help="OpenAI model (default gpt-4o-mini)")
    enrich.add_argument("--batch-size", type=int, default=5)
    enrich.add_argument("--resume",     action="store_true",
                        help="Skip notes already enriched in notes_enriched.json")
    enrich.add_argument("--delay",      type=float, default=0.5)
    return p.parse_args()


def main():
    args = parse_args()
    py = sys.executable
    steps = args.steps
    failed = []

    print("\n🚀  iCloud Notes Export Pipeline")
    print(f"    Steps: {' → '.join(steps)}")
    print(f"    Output dir: {OUTPUT.resolve()}\n")

    # ── Step 1: Scrape ───────────────────────────────────────────────────────
    if "scrape" in steps:
        cmd = [py, str(SCRAPER), "--out", str(RAW_JSON)]
        if args.folder:
            cmd += ["--folder", args.folder]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        if args.pause != 1.2:
            cmd += ["--pause", str(args.pause)]
        if args.wait_login:
            cmd += ["--wait-login", str(args.wait_login)]

        rc = run(cmd, "STEP 1 / 3 — Scrape iCloud Notes (Playwright)")
        if rc != 0:
            failed.append("scrape")
            if "enrich" in steps or "convert" in steps:
                print("  ⚠️  Scrape failed — subsequent steps may fail too.")

    # ── Step 2: Enrich ───────────────────────────────────────────────────────
    if "enrich" in steps:
        if not RAW_JSON.exists():
            print(f"\n❌  notes_export.json not found at {RAW_JSON.resolve()}")
            print("    Run the scrape step first, or place notes_export.json in output/")
            failed.append("enrich")
        else:
            cmd = [
                py, str(CLEANER),
                "--in",         str(RAW_JSON),
                "--out",        str(ENRICHED_JSON),
                "--model",      args.model,
                "--batch-size", str(args.batch_size),
                "--delay",      str(args.delay),
            ]
            if args.resume:
                cmd.append("--resume")

            rc = run(cmd, "STEP 2 / 3 — Enrich notes with OpenAI")
            if rc != 0:
                failed.append("enrich")

    # ── Step 3: Convert ──────────────────────────────────────────────────────
    if "convert" in steps:
        src = ENRICHED_JSON if ENRICHED_JSON.exists() else RAW_JSON
        if not src.exists():
            print(f"\n❌  No JSON source found for conversion.")
            failed.append("convert")
        else:
            cmd = [
                py, str(CONVERTER),
                "--in",     str(src),
                "--out-dir", str(OUTPUT),
                "--format", "all",
            ]
            rc = run(cmd, f"STEP 3 / 3 — Convert to CSV / Markdown (source: {src.name})")
            if rc != 0:
                failed.append("convert")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    if failed:
        print(f"⚠️   Pipeline finished with errors in: {', '.join(failed)}")
    else:
        print("✅  Pipeline complete!")

    print(f"\n📁  Output files in: {OUTPUT.resolve()}")
    for f in sorted(OUTPUT.iterdir()):
        size_kb = f.stat().st_size / 1024
        print(f"    {f.name:<35}  {size_kb:>7.1f} KB")
    print()


if __name__ == "__main__":
    main()
