#!/usr/bin/env python3
"""
pyicloud_export.py — Bulk-download iCloud Drive files via pyicloud.

Usage:
    python pyicloud_export.py \
        --apple-id user@example.com \
        --dest ~/icloud-drive-export \
        --scope "/"  \
        --workers 4
"""

import argparse
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfileobj

from dateutil.parser import parse as parse_date

try:
    from pyicloud import PyiCloudService
except ImportError:
    print("ERROR: pyicloud not installed. Run: pip install pyicloud --break-system-packages")
    sys.exit(1)


def get_file_date(node) -> datetime | None:
    """Extract the best available date from an iCloud Drive node."""
    for attr in ("date_modified", "date_changed", "date_last_open", "date_created"):
        val = getattr(node, attr, None)
        if val and isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            return val
    return None


def apply_filters(file_items: list, args) -> list:
    """Filter file list by date range, limit, and order."""
    # --- Date range filter ---
    if args.after or args.before:
        after_dt = parse_date(args.after).replace(tzinfo=timezone.utc) if args.after else None
        before_dt = parse_date(args.before).replace(tzinfo=timezone.utc) if args.before else None
        filtered = []
        skipped = 0
        for path, node in file_items:
            fdate = get_file_date(node)
            if fdate is None:
                # No date metadata — include by default (conservative)
                filtered.append((path, node))
                continue
            if after_dt and fdate < after_dt:
                skipped += 1
                continue
            if before_dt and fdate > before_dt:
                skipped += 1
                continue
            filtered.append((path, node))
        if skipped:
            print(f"📅 Date filter: kept {len(filtered)}, skipped {skipped}")
        file_items = filtered

    # --- Sort by date for deterministic first/last behavior ---
    def sort_key(item):
        path, node = item
        d = get_file_date(node)
        return d or datetime.min.replace(tzinfo=timezone.utc)

    file_items.sort(key=sort_key)

    # --- Limit: first N or last N ---
    if args.first:
        file_items = file_items[: args.first]
        print(f"🔢 Taking first (oldest) {args.first} files")
    elif args.last:
        file_items = file_items[-args.last :]
        print(f"🔢 Taking last (newest) {args.last} files")
    elif args.limit:
        file_items = file_items[: args.limit]
        print(f"🔢 Limiting to {args.limit} files")

    return file_items


def authenticate(apple_id: str) -> PyiCloudService:
    """Authenticate to iCloud, handling 2FA/2SA interactively."""
    api = PyiCloudService(apple_id)

    if api.requires_2fa:
        print("\n🔐 Two-factor authentication required.")
        code = input("Enter the 2FA code from your trusted device: ").strip()
        if not api.validate_2fa_code(code):
            print("❌ 2FA validation failed.")
            sys.exit(1)
        print("✅ 2FA accepted.")
        if not api.is_trusted_session:
            api.trust_session()
            print("✅ Session trusted (won't need 2FA for ~2 months).")

    elif api.requires_2sa:
        print("\n🔐 Two-step authentication required.")
        devices = api.trusted_devices
        for i, device in enumerate(devices):
            name = device.get("deviceName", device.get("phoneNumber", f"Device {i}"))
            print(f"  [{i}] {name}")
        idx = int(input("Choose device index: ").strip())
        device = devices[idx]
        if not api.send_verification_code(device):
            print("❌ Failed to send verification code.")
            sys.exit(1)
        code = input("Enter verification code: ").strip()
        if not api.validate_verification_code(device, code):
            print("❌ Verification failed.")
            sys.exit(1)
        print("✅ Verification accepted.")

    return api


def walk_drive(node, current_path: str = "") -> list:
    """Recursively walk an iCloud Drive node, yielding (remote_path, node) tuples."""
    items = []
    try:
        children = node.dir()
    except Exception:
        # Leaf node (file)
        return [(current_path, node)]

    for child_name in children:
        try:
            child = node[child_name]
            child_path = f"{current_path}/{child_name}" if current_path else child_name
            if child.type == "folder":
                items.extend(walk_drive(child, child_path))
            else:
                items.append((child_path, child))
        except Exception as e:
            items.append((f"{current_path}/{child_name}", {"error": str(e)}))
    return items


def download_file(remote_path: str, node, dest_root: Path) -> dict:
    """Download a single file and return its manifest record."""
    local_path = dest_root / remote_path
    local_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "filename": node.name if hasattr(node, "name") else os.path.basename(remote_path),
        "relative_path": remote_path,
        "absolute_path": str(local_path),
        "icloud_folder": str(Path(remote_path).parent) if "/" in remote_path else "",
        "extension": "",
        "mime_type": "application/octet-stream",
        "size_bytes": 0,
        "created": None,
        "modified": None,
        "category": None,
        "tags": [],
        "notes": "",
    }

    try:
        # Get metadata
        if hasattr(node, "name"):
            record["filename"] = node.name
        if hasattr(node, "date_modified") and node.date_modified:
            record["modified"] = node.date_modified.isoformat()
        if hasattr(node, "date_created") and node.date_created:
            record["created"] = node.date_created.isoformat()
        if hasattr(node, "size") and node.size:
            record["size_bytes"] = node.size

        ext = Path(record["filename"]).suffix.lower()
        record["extension"] = ext
        mime, _ = mimetypes.guess_type(record["filename"])
        if mime:
            record["mime_type"] = mime

        # Download
        with node.open(stream=True) as response:
            with open(local_path, "wb") as f:
                copyfileobj(response.raw, f)

        # Update size from actual file
        actual_size = local_path.stat().st_size
        record["size_bytes"] = actual_size

        return {"status": "ok", "record": record}

    except Exception as e:
        return {
            "status": "error",
            "record": record,
            "error": f"{remote_path}: {str(e)}",
        }


def main():
    parser = argparse.ArgumentParser(description="Bulk export iCloud Drive files")
    parser.add_argument("--apple-id", required=True, help="Apple ID email")
    parser.add_argument("--dest", default=os.path.expanduser("~/icloud-drive-export"),
                        help="Local destination directory")
    parser.add_argument("--scope", default="/", help="iCloud Drive path to export (/ for all)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel download threads")
    # Filtering options
    parser.add_argument("--after", default=None,
                        help="Only include files modified after this date (e.g. 2025-01-01)")
    parser.add_argument("--before", default=None,
                        help="Only include files modified before this date (e.g. 2025-12-31)")
    parser.add_argument("--first", type=int, default=None,
                        help="Download only the first (oldest) N files — useful for testing")
    parser.add_argument("--last", type=int, default=None,
                        help="Download only the last (newest) N files — useful for testing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of files to download (after date filtering)")
    args = parser.parse_args()

    # Validate mutually exclusive limit options
    limit_flags = [x for x in [args.first, args.last, args.limit] if x is not None]
    if len(limit_flags) > 1:
        parser.error("Use only one of --first, --last, --limit")

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    print(f"📱 Authenticating as {args.apple_id}...")
    api = authenticate(args.apple_id)

    print(f"📂 Scanning iCloud Drive (scope: {args.scope})...")

    # Navigate to the requested scope
    drive_root = api.drive
    if args.scope and args.scope != "/":
        for part in args.scope.strip("/").split("/"):
            drive_root = drive_root[part]

    items = walk_drive(drive_root)
    file_items = [(path, node) for path, node in items if not isinstance(node, dict)]
    error_items = [(path, node) for path, node in items if isinstance(node, dict)]

    print(f"📊 Found {len(file_items)} files total")

    # Apply date range and limit filters
    file_items = apply_filters(file_items, args)

    print(f"📊 {len(file_items)} files after filtering")
    if error_items:
        print(f"⚠️  {len(error_items)} items could not be listed")

    # Download files
    manifest_files = []
    errors = [f"{path}: {node['error']}" for path, node in error_items]
    completed = 0
    start_time = time.time()

    print(f"⬇️  Downloading with {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_file, path, node, dest): path
            for path, node in file_items
        }
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result["status"] == "ok":
                manifest_files.append(result["record"])
            else:
                manifest_files.append(result["record"])
                errors.append(result["error"])

            if completed % 50 == 0 or completed == len(file_items):
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                print(f"  [{completed}/{len(file_items)}] {rate:.1f} files/sec")

    # Assign sequential IDs
    for i, rec in enumerate(manifest_files, 1):
        rec["id"] = f"file_{i:04d}"

    # Sort by relative path
    manifest_files.sort(key=lambda r: r["relative_path"])

    # Build manifest
    total_size = sum(r["size_bytes"] for r in manifest_files)
    manifest = {
        "export_metadata": {
            "source": "icloud_drive",
            "apple_id": args.apple_id,
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_files": len(manifest_files),
            "total_size_bytes": total_size,
            "backend_used": "pyicloud",
            "scope": args.scope,
            "filters": {
                "after": args.after,
                "before": args.before,
                "first": args.first,
                "last": args.last,
                "limit": args.limit,
            },
            "errors": errors,
        },
        "files": manifest_files,
    }

    manifest_path = dest / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    elapsed = time.time() - start_time
    print(f"\n✅ Export complete!")
    print(f"   Files: {len(manifest_files)} ({len(errors)} errors)")
    print(f"   Size:  {total_size / (1024*1024):.1f} MB")
    print(f"   Time:  {elapsed:.0f}s")
    print(f"   Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
