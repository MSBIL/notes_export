"""
export_csv.py
─────────────
Produces two flat CSVs from oreilly_raw.json:

  oreilly_export.csv          – all items (one row per item)
  oreilly_collections.csv     – one row per collection (summary)

Usage
-----
  python export_csv.py
  python export_csv.py --in ../output/oreilly_raw.json --out-dir ../output/
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ITEM_FIELDS = [
    "collection_name", "collection_type", "collection_url",
    "position", "sub_list_parent",
    "item_title", "content_type", "author", "duration", "item_url",
]

COLLECTION_FIELDS = [
    "collection_name", "collection_type", "collection_url", "item_count",
]


def write_items_csv(data: dict, out_path: Path) -> int:
    rows = []
    for section_key, section_label in [
        ("playlists",        "my_playlist"),
        ("expert_playlists", "expert_playlist"),
        ("learning_paths",   "learning_path"),
    ]:
        for coll in data.get(section_key, []):
            for item in coll.get("items", []):
                rows.append({
                    "collection_name": coll["name"],
                    "collection_type": section_label,
                    "collection_url":  coll.get("url", ""),
                    "position":        item.get("position", ""),
                    "sub_list_parent": item.get("sub_list_parent", ""),
                    "item_title":      item.get("title", ""),
                    "content_type":    item.get("content_type", ""),
                    "author":          item.get("author", ""),
                    "duration":        item.get("duration", ""),
                    "item_url":        item.get("url", ""),
                })

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ITEM_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  ✔  Items CSV    → {out_path.resolve()}  ({len(rows)} rows)")
    return len(rows)


def write_collections_csv(data: dict, out_path: Path) -> None:
    rows = []
    for section_key, section_label in [
        ("playlists",        "my_playlist"),
        ("expert_playlists", "expert_playlist"),
        ("learning_paths",   "learning_path"),
    ]:
        for coll in data.get(section_key, []):
            rows.append({
                "collection_name": coll["name"],
                "collection_type": section_label,
                "collection_url":  coll.get("url", ""),
                "item_count":      len(coll.get("items", [])),
            })

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLLECTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  ✔  Collections  → {out_path.resolve()}  ({len(rows)} rows)")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--in",      dest="input",   default="../output/oreilly_raw.json")
    p.add_argument("--out-dir", dest="out_dir", default="../output/")
    return p.parse_args()


def main():
    args  = parse_args()
    in_p  = Path(args.input)
    out_d = Path(args.out_dir)
    out_d.mkdir(parents=True, exist_ok=True)

    if not in_p.exists():
        print(f"❌  Not found: {in_p.resolve()}")
        return

    with open(in_p, encoding="utf-8") as f:
        data = json.load(f)

    write_items_csv(data, out_d / "oreilly_export.csv")
    write_collections_csv(data, out_d / "oreilly_collections.csv")


if __name__ == "__main__":
    main()
