"""
run_pipeline.py
───────────────
Single entry-point for the O'Reilly bulk exporter.

Steps
-----
  1. scrape   – Playwright collects playlists / expert lists / learning paths
  2. excel    – Builds multi-sheet .xlsx with colour coding + hyperlinks
  3. csv      – Writes flat CSVs as a backup

Usage
-----
  python run_pipeline.py                          # all steps, all types
  python run_pipeline.py --steps scrape           # only scrape
  python run_pipeline.py --steps excel csv        # only export (raw JSON exists)
  python run_pipeline.py --resume                 # skip already-scraped collections
  python run_pipeline.py --types playlists        # only personal playlists
  python run_pipeline.py --pause 2.0              # slower for flaky connections
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE        = Path(__file__).parent
SCRAPER     = BASE / "scraper" / "scrape_oreilly.py"
EXCEL_EXP   = BASE / "utils"  / "export_excel.py"
CSV_EXP     = BASE / "utils"  / "export_csv.py"
OUTPUT      = BASE / "output"
RAW_JSON    = OUTPUT / "oreilly_raw.json"
EXCEL_FILE  = OUTPUT / "oreilly_export.xlsx"
OUTPUT.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'─'*60}")
    print(f"▶  {label}")
    print(f"   {' '.join(cmd)}")
    print(f"{'─'*60}")
    return subprocess.run(cmd).returncode


def parse_args():
    p = argparse.ArgumentParser(
        description="O'Reilly bulk exporter pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--steps", nargs="+",
        choices=["scrape", "excel", "csv"],
        default=["scrape", "excel", "csv"],
    )
    p.add_argument(
        "--types", nargs="+",
        choices=["playlists", "expert_playlists", "learning_paths"],
        default=["playlists", "expert_playlists", "learning_paths"],
    )
    p.add_argument("--pause",  type=float, default=1.2)
    p.add_argument("--resume", action="store_true",
                   help="Skip collections already in oreilly_raw.json")
    return p.parse_args()


def main():
    args   = parse_args()
    py     = sys.executable
    failed = []

    print("\n🚀  O'Reilly Bulk Exporter")
    print(f"    Steps : {' → '.join(args.steps)}")
    print(f"    Types : {', '.join(args.types)}")
    print(f"    Output: {OUTPUT.resolve()}\n")

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    if "scrape" in args.steps:
        cmd = [
            py, str(SCRAPER),
            "--types", *args.types,
            "--out",   str(RAW_JSON),
            "--pause", str(args.pause),
        ]
        if args.resume:
            cmd.append("--resume")
        if run(cmd, "STEP 1 — Scrape O'Reilly (Playwright)") != 0:
            failed.append("scrape")
            print("  ⚠️  Scrape failed — export steps will use existing raw JSON if present.")

    # ── Step 2: Excel ─────────────────────────────────────────────────────────
    if "excel" in args.steps:
        if not RAW_JSON.exists():
            print(f"\n❌  oreilly_raw.json not found — run the scrape step first.")
            failed.append("excel")
        else:
            cmd = [py, str(EXCEL_EXP), "--in", str(RAW_JSON), "--out", str(EXCEL_FILE)]
            if run(cmd, "STEP 2 — Build Excel workbook") != 0:
                failed.append("excel")

    # ── Step 3: CSV ───────────────────────────────────────────────────────────
    if "csv" in args.steps:
        if not RAW_JSON.exists():
            print(f"\n❌  oreilly_raw.json not found — skipping CSV.")
            failed.append("csv")
        else:
            cmd = [py, str(CSV_EXP), "--in", str(RAW_JSON), "--out-dir", str(OUTPUT)]
            if run(cmd, "STEP 3 — Export CSVs") != 0:
                failed.append("csv")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    if failed:
        print(f"⚠️   Finished with errors in: {', '.join(failed)}")
    else:
        print("✅  Pipeline complete!")

    print(f"\n📁  Output files ({OUTPUT.resolve()}):")
    if OUTPUT.exists():
        for f in sorted(OUTPUT.iterdir()):
            size_kb = f.stat().st_size / 1024
            print(f"    {f.name:<40}  {size_kb:>7.1f} KB")
    print()


if __name__ == "__main__":
    main()
