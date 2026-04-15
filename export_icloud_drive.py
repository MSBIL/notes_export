#!/usr/bin/env python3
"""
export_icloud_drive.py — Unified entry point for exporting iCloud Drive files
AND/OR Apple Notes. Tries multiple backends per source and generates manifests.

Usage:
    # Export iCloud Drive files only (default)
    python export_icloud_drive.py --apple-id you@icloud.com --source files

    # Export Apple Notes only
    python export_icloud_drive.py --apple-id you@icloud.com --source notes

    # Export both
    python export_icloud_drive.py --apple-id you@icloud.com --source both

    # Test with 10 newest items from each source
    python export_icloud_drive.py --apple-id you@icloud.com --source both --last 10
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def try_ifetch(apple_id: str, dest: str, scope: str, workers: int) -> bool:
    """Attempt export via iFetch. Returns True on success."""
    print("🔄 Trying Backend A: iFetch...")

    ifetch_paths = [
        None,
        "/tmp/ifetch/ifetch/cli.py",
        os.path.expanduser("~/iFetch/ifetch/cli.py"),
    ]

    try:
        result = subprocess.run(
            [sys.executable, "-m", "ifetch.cli", "--help"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            cmd = [
                sys.executable, "-m", "ifetch.cli",
                scope if scope != "/" else "",
                dest,
                f"--email={apple_id}",
                f"--max-workers={workers}",
                "--max-retries=5",
                f"--log-file={os.path.join(dest, 'ifetch.log')}",
            ]
            print(f"  Running: {' '.join(cmd)}")
            proc = subprocess.run(cmd, timeout=7200)
            return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"  iFetch module not available: {e}")

    for cli_path in ifetch_paths:
        if cli_path and os.path.exists(cli_path):
            cmd = [
                sys.executable, cli_path,
                scope if scope != "/" else "",
                dest,
                f"--email={apple_id}",
                f"--max-workers={workers}",
                "--max-retries=5",
            ]
            print(f"  Running: {' '.join(cmd)}")
            try:
                proc = subprocess.run(cmd, timeout=7200)
                return proc.returncode == 0
            except Exception as e:
                print(f"  iFetch failed at {cli_path}: {e}")

    print("  ❌ iFetch not found or not installed.")
    return False


def try_pyicloud(apple_id: str, dest: str, scope: str, workers: int,
                 after=None, before=None, first=None, last=None, limit=None) -> bool:
    """Attempt export via pyicloud direct download. Returns True on success."""
    print("🔄 Trying Backend B: pyicloud direct...")

    script_dir = Path(__file__).parent
    pyicloud_script = script_dir / "pyicloud_export.py"

    if not pyicloud_script.exists():
        print(f"  ❌ pyicloud_export.py not found at {pyicloud_script}")
        return False

    cmd = [
        sys.executable, str(pyicloud_script),
        "--apple-id", apple_id,
        "--dest", dest,
        "--scope", scope,
        "--workers", str(workers),
    ]
    if after:
        cmd.extend(["--after", after])
    if before:
        cmd.extend(["--before", before])
    if first:
        cmd.extend(["--first", str(first)])
    if last:
        cmd.extend(["--last", str(last)])
    if limit:
        cmd.extend(["--limit", str(limit)])
    print(f"  Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, timeout=7200)
        return proc.returncode == 0
    except Exception as e:
        print(f"  pyicloud export failed: {e}")
        return False


def try_notes_export(dest: str, after=None, before=None, first=None,
                     last=None, limit=None, db=None, backend="auto") -> bool:
    """Attempt Apple Notes export. Returns True on success."""
    print("\n📝 Exporting Apple Notes...")

    script_dir = Path(__file__).parent
    notes_script = script_dir / "apple_notes_export.py"

    if not notes_script.exists():
        print(f"  ❌ apple_notes_export.py not found at {notes_script}")
        return False

    cmd = [
        sys.executable, str(notes_script),
        "--dest", dest,
        "--backend", backend,
    ]
    if db:
        cmd.extend(["--db", db])
    if after:
        cmd.extend(["--after", after])
    if before:
        cmd.extend(["--before", before])
    if first:
        cmd.extend(["--first", str(first)])
    if last:
        cmd.extend(["--last", str(last)])
    if limit:
        cmd.extend(["--limit", str(limit)])

    print(f"  Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, timeout=3600)
        return proc.returncode == 0
    except Exception as e:
        print(f"  Notes export failed: {e}")
        return False


def generate_manifest(dest: str, apple_id: str, backend: str) -> bool:
    """Generate the structured manifest from downloaded files."""
    print("📋 Generating file manifest...")

    script_dir = Path(__file__).parent
    manifest_script = script_dir / "generate_manifest.py"

    if not manifest_script.exists():
        print(f"  ❌ generate_manifest.py not found at {manifest_script}")
        return False

    cmd = [
        sys.executable, str(manifest_script),
        "--root", dest,
        "--output", os.path.join(dest, "manifest.json"),
        "--apple-id", apple_id,
        "--backend", backend,
    ]
    try:
        proc = subprocess.run(cmd, timeout=300)
        return proc.returncode == 0
    except Exception as e:
        print(f"  Manifest generation failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Export iCloud Drive files and/or Apple Notes"
    )
    parser.add_argument("--apple-id", default=None,
                        help="Apple ID email (required for iCloud Drive files)")
    parser.add_argument("--source", choices=["files", "notes", "both"],
                        default="files",
                        help="What to export: 'files' (iCloud Drive), 'notes' (Apple Notes), or 'both'")
    parser.add_argument("--dest", default=None,
                        help="Local destination directory")
    parser.add_argument("--scope", default="/",
                        help="iCloud Drive path to export (/ for everything)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel download threads (files only)")
    parser.add_argument("--backend", choices=["auto", "ifetch", "pyicloud", "sqlite", "parser"],
                        default="auto",
                        help="Force a specific backend")
    parser.add_argument("--notes-db", default=None,
                        help="Path to NoteStore.sqlite (auto-detected on macOS if omitted)")
    # Filtering options
    parser.add_argument("--after", default=None,
                        help="Only items modified after this date (e.g. 2025-01-01)")
    parser.add_argument("--before", default=None,
                        help="Only items modified before this date (e.g. 2025-12-31)")
    parser.add_argument("--first", type=int, default=None,
                        help="Export only the first (oldest) N items — great for testing")
    parser.add_argument("--last", type=int, default=None,
                        help="Export only the last (newest) N items — great for testing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max items to export (after date filtering)")
    args = parser.parse_args()

    # Validate
    limit_flags = [x for x in [args.first, args.last, args.limit] if x is not None]
    if len(limit_flags) > 1:
        parser.error("Use only one of --first, --last, --limit")

    if args.source in ("files", "both") and not args.apple_id:
        parser.error("--apple-id is required when exporting files")

    # Set default dest based on source
    if args.dest is None:
        if args.source == "files":
            args.dest = os.path.expanduser("~/icloud-drive-export")
        elif args.source == "notes":
            args.dest = os.path.expanduser("~/apple-notes-export")
        else:
            args.dest = os.path.expanduser("~/icloud-export")

    os.makedirs(args.dest, exist_ok=True)

    files_ok = True
    notes_ok = True

    # --- Export iCloud Drive files ---
    if args.source in ("files", "both"):
        files_dest = args.dest if args.source == "files" else os.path.join(args.dest, "files")
        os.makedirs(files_dest, exist_ok=True)

        file_backend = args.backend if args.backend in ("auto", "ifetch", "pyicloud") else "auto"

        success = False
        backend_used = "none"

        if file_backend in ("auto", "ifetch"):
            success = try_ifetch(args.apple_id, files_dest, args.scope, args.workers)
            if success:
                backend_used = "ifetch"

        if not success and file_backend in ("auto", "pyicloud"):
            success = try_pyicloud(args.apple_id, files_dest, args.scope, args.workers,
                                   after=args.after, before=args.before,
                                   first=args.first, last=args.last, limit=args.limit)
            if success:
                backend_used = "pyicloud"

        if success:
            generate_manifest(files_dest, args.apple_id, backend_used)
            print(f"\n✅ Files export complete ({backend_used})")
            print(f"   Manifest: {os.path.join(files_dest, 'manifest.json')}")
        else:
            files_ok = False
            print("\n⚠️  iCloud Drive export failed.")

    # --- Export Apple Notes ---
    if args.source in ("notes", "both"):
        notes_dest = args.dest if args.source == "notes" else os.path.join(args.dest, "notes")
        os.makedirs(notes_dest, exist_ok=True)

        notes_backend = args.backend if args.backend in ("auto", "sqlite", "parser") else "auto"

        success = try_notes_export(
            dest=notes_dest,
            after=args.after, before=args.before,
            first=args.first, last=args.last, limit=args.limit,
            db=args.notes_db, backend=notes_backend,
        )
        if success:
            print(f"\n✅ Notes export complete")
            print(f"   Manifest: {os.path.join(notes_dest, 'notes_manifest.json')}")
        else:
            notes_ok = False
            print("\n⚠️  Apple Notes export failed.")

    # --- Summary ---
    print(f"\n{'='*50}")
    if args.source == "both":
        if files_ok and notes_ok:
            print(f"🎉 Both exports complete!")
            print(f"   Files manifest:  {os.path.join(args.dest, 'files', 'manifest.json')}")
            print(f"   Notes manifest:  {os.path.join(args.dest, 'notes', 'notes_manifest.json')}")
        elif files_ok:
            print(f"⚠️  Files exported, but Notes failed.")
        elif notes_ok:
            print(f"⚠️  Notes exported, but Files failed.")
        else:
            print(f"❌ Both exports failed.")
            sys.exit(1)
    elif args.source == "files":
        if not files_ok:
            print("❌ Export failed. Check credentials and network.")
            sys.exit(1)
        print(f"🎉 Export complete!")
    else:
        if not notes_ok:
            print("❌ Export failed. Check database path and permissions.")
            sys.exit(1)
        print(f"🎉 Export complete!")

    print(f"\n   Next step: pipe the manifest(s) to your categorization tool.")


if __name__ == "__main__":
    main()
