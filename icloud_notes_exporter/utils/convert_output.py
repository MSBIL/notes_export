"""
convert_output.py
─────────────────
Converts notes_enriched.json to:
  • CSV  (flat, good for spreadsheet review)
  • Markdown  (one section per note, good for archiving)
  • Task list  (only item_kind == "task", markdown checklist)

Usage
-----
  python convert_output.py                     # produces all three formats
  python convert_output.py --format csv
  python convert_output.py --format md
  python convert_output.py --format tasks
  python convert_output.py --in ../output/notes_enriched.json --out-dir ../output/
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS_CSV = [
    "id", "folder", "title", "item_kind", "category",
    "priority", "next_action", "clean_summary", "tags", "status",
]


def to_csv(notes: list[dict], out_dir: Path):
    path = out_dir / "notes_export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS_CSV, extrasaction="ignore")
        writer.writeheader()
        for note in notes:
            row = {k: note.get(k, "") for k in FIELDS_CSV}
            # flatten tags list
            if isinstance(row["tags"], list):
                row["tags"] = ", ".join(row["tags"])
            writer.writerow(row)
    print(f"  ✔  CSV  → {path}")


def to_markdown(notes: list[dict], out_dir: Path):
    path = out_dir / "notes_archive.md"
    lines = ["# iCloud Notes Archive\n\n"]
    for note in notes:
        title  = note.get("title") or "(untitled)"
        kind   = note.get("item_kind", "")
        folder = note.get("folder", "")
        cat    = note.get("category", "")
        prio   = note.get("priority", "")
        tags   = note.get("tags", [])
        summary = note.get("clean_summary", "")
        raw    = note.get("raw_text", "")
        nid    = note.get("id", "")

        tag_str = ", ".join(tags) if isinstance(tags, list) else tags

        lines.append(f"## {title}\n")
        lines.append(f"**ID:** {nid}  |  **Folder:** {folder}  |  **Kind:** {kind}  "
                     f"|  **Category:** {cat}  |  **Priority:** {prio}\n\n")
        if tag_str:
            lines.append(f"**Tags:** {tag_str}\n\n")
        if summary:
            lines.append(f"**Summary:** {summary}\n\n")
        if note.get("next_action"):
            lines.append(f"**Next Action:** {note['next_action']}\n\n")
        if raw:
            lines.append("<details><summary>Raw text</summary>\n\n")
            lines.append(f"```\n{raw}\n```\n\n</details>\n\n")
        lines.append("---\n\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  ✔  Markdown → {path}")


def to_tasks(notes: list[dict], out_dir: Path):
    path = out_dir / "tasks.md"
    task_notes = [n for n in notes if n.get("item_kind") == "task"]
    lines = [f"# Task List  ({len(task_notes)} items)\n\n"]

    # group by priority
    for prio in ("high", "medium", "low", ""):
        group = [n for n in task_notes if n.get("priority", "") == prio]
        if not group:
            continue
        label = prio.capitalize() if prio else "Unset"
        lines.append(f"## {label} Priority\n\n")
        for n in group:
            title = n.get("title") or "(untitled)"
            action = n.get("next_action", "")
            folder = n.get("folder", "")
            line = f"- [ ] **{title}**"
            if action:
                line += f" — {action}"
            if folder:
                line += f" _(folder: {folder})_"
            lines.append(line + "\n")
        lines.append("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  ✔  Tasks   → {path}")


def parse_args():
    p = argparse.ArgumentParser(description="Convert enriched notes JSON to CSV/MD/tasks")
    p.add_argument("--in", dest="input", default="../output/notes_enriched.json")
    p.add_argument("--out-dir", default="../output/")
    p.add_argument("--format", choices=["csv", "md", "tasks", "all"], default="all")
    return p.parse_args()


def main():
    args = parse_args()
    in_path  = Path(args.input)
    out_dir  = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        print(f"❌  Input not found: {in_path.resolve()}")
        return

    with open(in_path, encoding="utf-8") as f:
        notes = json.load(f)
    print(f"📥  Loaded {len(notes)} notes from {in_path.resolve()}")

    fmt = args.format
    if fmt in ("csv",   "all"):
        to_csv(notes, out_dir)
    if fmt in ("md",    "all"):
        to_markdown(notes, out_dir)
    if fmt in ("tasks", "all"):
        to_tasks(notes, out_dir)


if __name__ == "__main__":
    main()
