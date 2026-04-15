"""
merge_exports.py
────────────────
Merges multiple per-folder JSON export files into one combined file.
Deduplicates by (title, raw_text[:200]).

Usage
-----
  python merge_exports.py                          # merges all *.json in ../output/
  python merge_exports.py folder1.json folder2.json --out merged.json
  python merge_exports.py --dir ../output/ --out ../output/notes_export.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Merge multiple JSON note export files")
    p.add_argument("files", nargs="*", help="JSON files to merge (optional)")
    p.add_argument("--dir", default="../output",
                   help="Directory to scan for *.json files if no files given")
    p.add_argument("--out", default="../output/notes_export.json",
                   help="Output file path")
    p.add_argument("--exclude", nargs="*", default=["notes_export.json", "notes_enriched.json"],
                   help="Filenames to exclude from auto-scan")
    return p.parse_args()


def main():
    args = parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Gather source files
    if args.files:
        sources = [Path(f) for f in args.files]
    else:
        directory = Path(args.dir)
        sources = [
            f for f in sorted(directory.glob("*.json"))
            if f.name not in (args.exclude or [])
               and f.resolve() != out_path.resolve()
        ]

    if not sources:
        print("❌  No input files found.")
        return

    print(f"📂  Merging {len(sources)} file(s):")
    for s in sources:
        print(f"    • {s}")

    all_notes: list[dict] = []
    seen: set[tuple] = set()

    for src in sources:
        if not src.exists():
            print(f"  ⚠️  File not found: {src}")
            continue
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        added = 0
        for note in data:
            key = (note.get("title", ""), note.get("raw_text", "")[:200])
            if key not in seen:
                seen.add(key)
                all_notes.append(note)
                added += 1
        print(f"  ✔  {src.name}: {len(data)} notes loaded, {added} unique added")

    # Re-number IDs
    for i, note in enumerate(all_notes, 1):
        note["id"] = f"note_{i:04d}"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_notes, f, ensure_ascii=False, indent=2)

    print(f"\n💾  Merged {len(all_notes)} unique notes → {out_path.resolve()}")


if __name__ == "__main__":
    main()
