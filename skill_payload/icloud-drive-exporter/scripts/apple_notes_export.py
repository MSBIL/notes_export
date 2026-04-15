#!/usr/bin/env python3
"""
apple_notes_export.py — Bulk-export Apple Notes to a local directory with
structured manifest output. Supports two backends:

  A) Direct SQLite read (macOS only) — reads NoteStore.sqlite in-place
  B) apple_cloud_notes_parser (cross-platform) — parses a copied NoteStore.sqlite

Usage:
    # Auto-detect: tries SQLite direct, falls back to parser
    python apple_notes_export.py --dest ~/notes-export

    # Point at a specific NoteStore.sqlite (e.g. from a backup)
    python apple_notes_export.py --dest ~/notes-export \
        --db /path/to/NoteStore.sqlite

    # Test with just the 10 newest notes
    python apple_notes_export.py --dest ~/notes-export --last 10

    # Export notes modified in 2025
    python apple_notes_export.py --dest ~/notes-export \
        --after 2025-01-01 --before 2025-12-31
"""

import argparse
import gzip
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from html import escape as html_escape
from pathlib import Path

try:
    from dateutil.parser import parse as parse_date
except ImportError:
    print("ERROR: python-dateutil not installed. Run: pip install python-dateutil --break-system-packages")
    sys.exit(1)


# Apple's Core Data epoch: 2001-01-01 00:00:00 UTC
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def apple_timestamp_to_datetime(ts):
    """Convert Apple Core Data timestamp (seconds since 2001-01-01) to datetime."""
    if ts is None or ts == 0:
        return None
    try:
        return APPLE_EPOCH + timedelta(seconds=ts)
    except (ValueError, OverflowError):
        return None


def find_notestore_db() -> Path | None:
    """Find the NoteStore.sqlite on macOS."""
    home = Path.home()
    candidates = [
        home / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite",
        # Older macOS versions
        home / "Library/Containers/com.apple.Notes/Data/Library/Notes/NotesV7.storedata",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def extract_plaintext_from_blob(zdata: bytes) -> str:
    """
    Extract plaintext from Apple Notes ZDATA blob.
    The blob is gzipped protobuf. We extract the text strings from it.
    This is a best-effort parser — complex formatting and embeds are stripped.
    """
    if not zdata:
        return ""

    try:
        # Decompress gzip
        decompressed = gzip.decompress(zdata)
    except Exception:
        # Maybe it's not compressed (older format)
        decompressed = zdata

    # Extract readable text from the protobuf
    # The note text is typically in the first few string fields
    # We use a simple heuristic: find runs of printable UTF-8 characters
    try:
        text = decompressed.decode("utf-8", errors="ignore")
    except Exception:
        text = decompressed.decode("latin-1", errors="ignore")

    # Strip protobuf control characters but keep newlines and printable text
    # Protobuf strings are length-prefixed; the text content is interspersed
    # with binary framing bytes. Clean it up.
    lines = []
    for line in text.split("\n"):
        # Remove non-printable characters except common whitespace
        cleaned = re.sub(r'[^\x20-\x7E\u00A0-\uFFFF\t]', '', line).strip()
        if cleaned and len(cleaned) > 1:
            lines.append(cleaned)

    return "\n".join(lines)


def export_via_sqlite(db_path: Path, dest: Path, args) -> dict:
    """
    Read NoteStore.sqlite directly and export notes.
    Returns manifest dict.
    """
    print(f"📂 Reading SQLite database: {db_path}")

    # Work on a copy to avoid locking issues
    import shutil
    work_db = dest / ".notestore_copy.sqlite"
    shutil.copy2(db_path, work_db)

    # Also copy WAL and SHM if they exist (for consistency)
    for suffix in ["-wal", "-shm"]:
        src = db_path.parent / (db_path.name + suffix)
        if src.exists():
            shutil.copy2(src, dest / (work_db.name + suffix))

    conn = sqlite3.connect(str(work_db))
    conn.row_factory = sqlite3.Row

    # Query notes with folder info
    # Schema varies by macOS version; try the modern schema first
    try:
        rows = conn.execute("""
            SELECT
                n.Z_PK as note_pk,
                n.ZTITLE1 as title,
                n.ZCREATIONDATE as created,
                n.ZMODIFICATIONDATE1 as modified,
                n.ZFOLDER as folder_pk,
                nd.ZDATA as zdata,
                nd.ZHTMLSTRING as html_body,
                f.ZTITLE2 as folder_name,
                a.ZNAME as account_name
            FROM ZICCLOUDSYNCINGOBJECT n
            LEFT JOIN ZICNOTEDATA nd ON nd.ZNOTE = n.Z_PK
            LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER
            LEFT JOIN ZICCLOUDSYNCINGOBJECT a ON a.Z_PK = n.ZACCOUNT4
            WHERE n.ZTITLE1 IS NOT NULL
              AND n.ZMARKEDFORDELETION != 1
            ORDER BY n.ZMODIFICATIONDATE1 DESC
        """).fetchall()
    except sqlite3.OperationalError:
        # Fallback for older schema
        try:
            rows = conn.execute("""
                SELECT
                    n.Z_PK as note_pk,
                    n.ZTITLE as title,
                    n.ZCREATIONDATE as created,
                    n.ZMODIFICATIONDATE as modified,
                    n.ZFOLDER as folder_pk,
                    nd.ZDATA as zdata,
                    nd.ZHTMLSTRING as html_body,
                    f.ZTITLE as folder_name,
                    NULL as account_name
                FROM ZNOTE n
                LEFT JOIN ZNOTEBODY nd ON nd.ZNOTE = n.Z_PK
                LEFT JOIN ZFOLDER f ON f.Z_PK = n.ZFOLDER
                WHERE n.ZTITLE IS NOT NULL
                ORDER BY n.ZMODIFICATIONDATE DESC
            """).fetchall()
        except sqlite3.OperationalError as e:
            print(f"❌ Could not query notes database: {e}")
            print("   The database schema may be unsupported. Try using --backend parser instead.")
            conn.close()
            return None

    print(f"📊 Found {len(rows)} notes in database")

    # Convert to note records
    notes = []
    for row in rows:
        created_dt = apple_timestamp_to_datetime(row["created"])
        modified_dt = apple_timestamp_to_datetime(row["modified"])

        # Extract body text
        body = ""
        if row["zdata"]:
            body = extract_plaintext_from_blob(row["zdata"])
        elif row["html_body"]:
            # Strip HTML tags for plaintext
            body = re.sub(r'<[^>]+>', '', row["html_body"] or "")

        title = row["title"] or "Untitled"
        folder = row["folder_name"] or "Notes"
        account = row["account_name"] or "Local"

        notes.append({
            "note_pk": row["note_pk"],
            "title": title,
            "folder": folder,
            "account": account,
            "body": body.strip(),
            "body_length": len(body.strip()),
            "created": created_dt,
            "modified": modified_dt,
        })

    conn.close()

    # Clean up work copy
    for f in [work_db, dest / (work_db.name + "-wal"), dest / (work_db.name + "-shm")]:
        if f.exists():
            f.unlink()

    # Apply filters
    notes = apply_note_filters(notes, args)

    # Write notes to files and build manifest
    return write_notes_output(notes, dest, args, backend="sqlite_direct")


def export_via_parser(db_path: Path, dest: Path, args) -> dict:
    """
    Use apple_cloud_notes_parser (Ruby) to parse the NoteStore.sqlite.
    Falls back to a Docker-based invocation if Ruby isn't available.
    """
    print(f"📂 Using apple_cloud_notes_parser on: {db_path}")

    parser_output = dest / ".parser_output"
    parser_output.mkdir(parents=True, exist_ok=True)

    # Try Docker first (most portable)
    docker_ok = False
    try:
        result = subprocess.run([
            "docker", "run", "--rm",
            "-v", f"{db_path.parent}:/data:ro",
            "-v", f"{parser_output}:/app/output",
            "ghcr.io/threeplanetssoftware/apple_cloud_notes_parser",
            "-f", f"/data/{db_path.name}",
            "--one-output-folder",
        ], capture_output=True, text=True, timeout=600)
        docker_ok = result.returncode == 0
        if docker_ok:
            print("  ✅ Parsed via Docker")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not docker_ok:
        # Try local Ruby installation
        try:
            # Check for the parser
            parser_script = None
            for candidate in [
                Path("/tmp/apple_cloud_notes_parser/notes_cloud_ripper.rb"),
                Path.home() / "apple_cloud_notes_parser/notes_cloud_ripper.rb",
            ]:
                if candidate.exists():
                    parser_script = candidate
                    break

            if parser_script:
                result = subprocess.run([
                    "ruby", str(parser_script),
                    "-f", str(db_path),
                    "-o", str(parser_output),
                ], capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    docker_ok = True
                    print("  ✅ Parsed via local Ruby")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not docker_ok:
        print("  ❌ apple_cloud_notes_parser not available.")
        print("     Install via Docker: docker pull ghcr.io/threeplanetssoftware/apple_cloud_notes_parser")
        print("     Or clone: git clone https://github.com/threeplanetssoftware/apple_cloud_notes_parser.git")
        return None

    # Read the parser's JSON output
    json_files = list(parser_output.rglob("*.json"))
    if not json_files:
        # Fall back to HTML output
        html_files = list(parser_output.rglob("*.html"))
        if not html_files:
            print("  ❌ No output files found from parser")
            return None

        # Convert HTML files to note records
        notes = []
        for hf in html_files:
            body = re.sub(r'<[^>]+>', '', hf.read_text(errors="ignore"))
            stat = hf.stat()
            notes.append({
                "title": hf.stem,
                "folder": hf.parent.name if hf.parent != parser_output else "Notes",
                "account": "iCloud",
                "body": body.strip(),
                "body_length": len(body.strip()),
                "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            })
    else:
        # Parse JSON output (the parser produces a summary JSON)
        notes = []
        for jf in json_files:
            try:
                data = json.loads(jf.read_text())
                # Parser JSON structure varies; handle both formats
                if isinstance(data, dict) and "notes" in data:
                    for n in data["notes"]:
                        notes.append({
                            "title": n.get("title", "Untitled"),
                            "folder": n.get("folder", "Notes"),
                            "account": n.get("account", "iCloud"),
                            "body": n.get("plaintext", n.get("content", "")),
                            "body_length": len(n.get("plaintext", n.get("content", ""))),
                            "created": parse_date(n["created"]) if n.get("created") else None,
                            "modified": parse_date(n["modified"]) if n.get("modified") else None,
                        })
                elif isinstance(data, list):
                    for n in data:
                        notes.append({
                            "title": n.get("title", "Untitled"),
                            "folder": n.get("folder", "Notes"),
                            "account": n.get("account", "iCloud"),
                            "body": n.get("plaintext", n.get("content", "")),
                            "body_length": len(n.get("plaintext", n.get("content", ""))),
                            "created": parse_date(n["created"]) if n.get("created") else None,
                            "modified": parse_date(n["modified"]) if n.get("modified") else None,
                        })
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  ⚠️ Skipped malformed JSON {jf.name}: {e}")

    print(f"📊 Parsed {len(notes)} notes")

    notes = apply_note_filters(notes, args)
    return write_notes_output(notes, dest, args, backend="apple_cloud_notes_parser")


def apply_note_filters(notes: list, args) -> list:
    """Filter notes by date range and first/last/limit."""

    # Date range filter
    if args.after or args.before:
        after_dt = parse_date(args.after).replace(tzinfo=timezone.utc) if args.after else None
        before_dt = parse_date(args.before).replace(tzinfo=timezone.utc) if args.before else None
        filtered = []
        skipped = 0
        for note in notes:
            mod = note.get("modified")
            if mod is None:
                filtered.append(note)  # conservative: keep if no date
                continue
            if mod.tzinfo is None:
                mod = mod.replace(tzinfo=timezone.utc)
            if after_dt and mod < after_dt:
                skipped += 1
                continue
            if before_dt and mod > before_dt:
                skipped += 1
                continue
            filtered.append(note)
        if skipped:
            print(f"📅 Date filter: kept {len(filtered)}, skipped {skipped}")
        notes = filtered

    # Sort by modified date (oldest first)
    def sort_key(n):
        d = n.get("modified")
        if d is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if d.tzinfo is None:
            return d.replace(tzinfo=timezone.utc)
        return d

    notes.sort(key=sort_key)

    # Limit
    if args.first:
        notes = notes[: args.first]
        print(f"🔢 Taking first (oldest) {args.first} notes")
    elif args.last:
        notes = notes[-args.last :]
        print(f"🔢 Taking last (newest) {args.last} notes")
    elif args.limit:
        notes = notes[: args.limit]
        print(f"🔢 Limiting to {args.limit} notes")

    return notes


def sanitize_filename(title: str) -> str:
    """Make a note title safe for use as a filename."""
    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    safe = safe.strip('. ')
    if not safe:
        safe = "Untitled"
    return safe[:200]  # cap length


def write_notes_output(notes: list, dest: Path, args, backend: str) -> dict:
    """Write notes to individual files and produce manifest."""

    manifest_records = []
    errors = []

    for i, note in enumerate(notes, 1):
        title = note.get("title", "Untitled")
        folder = note.get("folder", "Notes")
        safe_title = sanitize_filename(title)
        safe_folder = sanitize_filename(folder)

        # Write note body to a text file
        folder_path = dest / safe_folder
        folder_path.mkdir(parents=True, exist_ok=True)

        # Avoid collisions
        filepath = folder_path / f"{safe_title}.md"
        counter = 1
        while filepath.exists():
            filepath = folder_path / f"{safe_title}_{counter}.md"
            counter += 1

        try:
            # Write as markdown with frontmatter
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"---\n")
                f.write(f"title: \"{title}\"\n")
                f.write(f"folder: \"{folder}\"\n")
                f.write(f"account: \"{note.get('account', 'iCloud')}\"\n")
                if note.get("created"):
                    f.write(f"created: \"{note['created'].isoformat()}\"\n")
                if note.get("modified"):
                    f.write(f"modified: \"{note['modified'].isoformat()}\"\n")
                f.write(f"---\n\n")
                f.write(f"# {title}\n\n")
                f.write(note.get("body", ""))
                f.write("\n")

            rel_path = str(filepath.relative_to(dest))

            record = {
                "id": f"note_{i:04d}",
                "title": title,
                "filename": filepath.name,
                "relative_path": rel_path,
                "absolute_path": str(filepath),
                "icloud_folder": folder,
                "account": note.get("account", "iCloud"),
                "extension": ".md",
                "mime_type": "text/markdown",
                "size_bytes": filepath.stat().st_size,
                "body_length": note.get("body_length", 0),
                "body_preview": (note.get("body", "")[:200] + "...") if len(note.get("body", "")) > 200 else note.get("body", ""),
                "created": note["created"].isoformat() if note.get("created") else None,
                "modified": note["modified"].isoformat() if note.get("modified") else None,
                "category": None,
                "tags": [],
                "notes": "",
            }
            manifest_records.append(record)

        except Exception as e:
            errors.append(f"{title}: {str(e)}")

    # Folder distribution
    folder_counts = {}
    for r in manifest_records:
        f = r["icloud_folder"] or "Notes"
        folder_counts[f] = folder_counts.get(f, 0) + 1

    total_size = sum(r["size_bytes"] for r in manifest_records)

    manifest = {
        "export_metadata": {
            "source": "apple_notes",
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_notes": len(manifest_records),
            "total_size_bytes": total_size,
            "backend_used": backend,
            "filters": {
                "after": args.after,
                "before": args.before,
                "first": args.first,
                "last": args.last,
                "limit": args.limit,
            },
            "errors": errors,
        },
        "summary": {
            "folder_distribution": dict(sorted(folder_counts.items(), key=lambda x: -x[1])),
            "total_body_chars": sum(r["body_length"] for r in manifest_records),
            "avg_note_length": (
                sum(r["body_length"] for r in manifest_records) // len(manifest_records)
                if manifest_records else 0
            ),
        },
        "notes": manifest_records,
    }

    manifest_path = dest / "notes_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"\n✅ Notes export complete!")
    print(f"   Notes: {len(manifest_records)} ({len(errors)} errors)")
    print(f"   Size:  {total_size / 1024:.1f} KB")
    print(f"   Manifest: {manifest_path}")
    print(f"\n📁 Folder distribution:")
    for folder, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
        print(f"   {folder:30s} {count:5d}")

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Export Apple Notes to local files with structured manifest"
    )
    parser.add_argument("--dest", default=os.path.expanduser("~/apple-notes-export"),
                        help="Local destination directory")
    parser.add_argument("--db", default=None,
                        help="Path to NoteStore.sqlite (auto-detected on macOS if omitted)")
    parser.add_argument("--backend", choices=["auto", "sqlite", "parser"],
                        default="auto",
                        help="Force a specific backend (auto tries sqlite then parser)")
    # Filtering options
    parser.add_argument("--after", default=None,
                        help="Only notes modified after this date (e.g. 2025-01-01)")
    parser.add_argument("--before", default=None,
                        help="Only notes modified before this date (e.g. 2025-12-31)")
    parser.add_argument("--first", type=int, default=None,
                        help="Export only the first (oldest) N notes — useful for testing")
    parser.add_argument("--last", type=int, default=None,
                        help="Export only the last (newest) N notes — useful for testing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of notes to export (after date filtering)")
    args = parser.parse_args()

    # Validate mutually exclusive limit options
    limit_flags = [x for x in [args.first, args.last, args.limit] if x is not None]
    if len(limit_flags) > 1:
        parser.error("Use only one of --first, --last, --limit")

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Find the database
    db_path = Path(args.db) if args.db else find_notestore_db()
    if db_path is None:
        print("❌ Could not find NoteStore.sqlite.")
        print("   On macOS, it should be at:")
        print("   ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite")
        print("")
        print("   If you have a backup copy, pass it with --db /path/to/NoteStore.sqlite")
        print("   You can also copy it from an iPhone backup (iTunes/Finder backup).")
        sys.exit(1)

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    print(f"📱 Apple Notes Export")
    print(f"   Database: {db_path}")
    print(f"   Destination: {dest}")

    result = None

    # Try backends
    if args.backend in ("auto", "sqlite"):
        result = export_via_sqlite(db_path, dest, args)

    if result is None and args.backend in ("auto", "parser"):
        result = export_via_parser(db_path, dest, args)

    if result is None:
        print("\n❌ All backends failed.")
        print("   For SQLite direct: ensure the database is not locked (close Notes.app)")
        print("   For parser: install apple_cloud_notes_parser via Docker or Ruby")
        sys.exit(1)

    print(f"\n🎉 Export complete!")
    print(f"   Notes at: {dest}")
    print(f"   Manifest: {dest / 'notes_manifest.json'}")
    print(f"\n   Next step: pipe notes_manifest.json to your categorization tool.")


if __name__ == "__main__":
    main()
