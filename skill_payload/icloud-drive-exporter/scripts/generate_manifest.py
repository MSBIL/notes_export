#!/usr/bin/env python3
"""
generate_manifest.py — Scan a downloaded iCloud Drive export and produce a
structured JSON manifest for downstream categorization tools.

This is useful when files were downloaded by iFetch (or any other tool) that
doesn't produce a manifest. It walks the local tree and builds one.

Usage:
    python generate_manifest.py \
        --root ~/icloud-drive-export \
        --output ~/icloud-drive-export/manifest.json \
        --apple-id user@example.com
"""

import argparse
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path


# Broad category hints based on extension — NOT the final categorization,
# just a convenience pre-label that downstream tools can override.
EXTENSION_HINTS = {
    # Documents
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".txt": "document",
    ".rtf": "document",
    ".odt": "document",
    ".pages": "document",
    ".md": "document",
    ".tex": "document",
    # Spreadsheets
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
    ".tsv": "spreadsheet",
    ".numbers": "spreadsheet",
    ".ods": "spreadsheet",
    # Presentations
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".key": "presentation",
    ".odp": "presentation",
    # Images
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".svg": "image",
    ".webp": "image",
    ".heic": "image",
    ".heif": "image",
    ".tiff": "image",
    ".raw": "image",
    # Audio
    ".mp3": "audio",
    ".wav": "audio",
    ".aac": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".wma": "audio",
    # Video
    ".mp4": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    ".wmv": "video",
    ".m4v": "video",
    ".webm": "video",
    # Archives
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".7z": "archive",
    ".rar": "archive",
    ".bz2": "archive",
    # Code
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".java": "code",
    ".cpp": "code",
    ".c": "code",
    ".h": "code",
    ".rs": "code",
    ".go": "code",
    ".rb": "code",
    ".swift": "code",
    ".sh": "code",
    ".json": "data",
    ".xml": "data",
    ".yaml": "data",
    ".yml": "data",
    ".html": "web",
    ".css": "web",
}


def scan_tree(root: Path) -> list:
    """Walk the export directory and build file records."""
    records = []
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories and the manifest itself
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for fname in filenames:
            if fname.startswith(".") or fname == "manifest.json":
                continue

            filepath = Path(dirpath) / fname
            rel_path = str(filepath.relative_to(root))
            folder = str(Path(rel_path).parent) if "/" in rel_path else ""

            ext = filepath.suffix.lower()
            mime, _ = mimetypes.guess_type(fname)

            stat = filepath.stat()

            records.append({
                "filename": fname,
                "relative_path": rel_path,
                "absolute_path": str(filepath),
                "icloud_folder": folder,
                "extension": ext,
                "mime_type": mime or "application/octet-stream",
                "size_bytes": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "extension_hint": EXTENSION_HINTS.get(ext, "other"),
                "category": None,
                "tags": [],
                "notes": "",
            })

    return records


def main():
    parser = argparse.ArgumentParser(
        description="Generate a structured manifest from a downloaded iCloud Drive export"
    )
    parser.add_argument("--root", required=True, help="Root directory of the export")
    parser.add_argument("--output", default=None,
                        help="Output manifest path (default: <root>/manifest.json)")
    parser.add_argument("--apple-id", default="unknown",
                        help="Apple ID for metadata (cosmetic)")
    parser.add_argument("--backend", default="ifetch",
                        help="Which backend was used (ifetch or pyicloud)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output) if args.output else root / "manifest.json"

    if not root.is_dir():
        print(f"❌ Directory not found: {root}")
        return

    print(f"📂 Scanning {root}...")
    records = scan_tree(root)

    # Assign sequential IDs and sort
    records.sort(key=lambda r: r["relative_path"])
    for i, rec in enumerate(records, 1):
        rec["id"] = f"file_{i:04d}"

    total_size = sum(r["size_bytes"] for r in records)

    # Extension distribution summary
    ext_counts = {}
    for r in records:
        ext = r["extension"] or "(none)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    top_extensions = sorted(ext_counts.items(), key=lambda x: -x[1])[:15]

    # Folder distribution
    folder_counts = {}
    for r in records:
        folder = r["icloud_folder"] or "(root)"
        folder_counts[folder] = folder_counts.get(folder, 0) + 1
    top_folders = sorted(folder_counts.items(), key=lambda x: -x[1])[:20]

    # Hint distribution
    hint_counts = {}
    for r in records:
        h = r.get("extension_hint", "other")
        hint_counts[h] = hint_counts.get(h, 0) + 1

    manifest = {
        "export_metadata": {
            "source": "icloud_drive",
            "apple_id": args.apple_id,
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_files": len(records),
            "total_size_bytes": total_size,
            "backend_used": args.backend,
            "errors": [],
        },
        "summary": {
            "top_extensions": dict(top_extensions),
            "top_folders": dict(top_folders),
            "type_distribution": hint_counts,
        },
        "files": records,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"\n✅ Manifest generated: {output}")
    print(f"   Total files: {len(records)}")
    print(f"   Total size:  {total_size / (1024*1024):.1f} MB")
    print(f"\n📊 Type distribution:")
    for hint, count in sorted(hint_counts.items(), key=lambda x: -x[1]):
        print(f"   {hint:15s} {count:5d}")
    print(f"\n📁 Top folders:")
    for folder, count in top_folders[:10]:
        print(f"   {folder:40s} {count:5d}")


if __name__ == "__main__":
    main()
