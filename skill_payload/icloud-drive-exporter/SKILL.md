---
name: icloud-drive-exporter
description: >
  Bulk export iCloud Drive files (Apple Files app) AND/OR Apple Notes to local directories,
  preserving folder structure and producing structured JSON manifests for piping into
  categorization, tagging, or search tools. Supports both sources via --source files|notes|both.
  Use this skill whenever the user wants to: download all files from iCloud Drive, bulk export
  iPhone Files app contents, export or back up Apple Notes in bulk, create a file/note
  inventory/manifest, or prepare iCloud data for categorization or indexing. Also trigger on
  phrases like "export my iCloud files", "download my Apple Notes", "back up my Notes",
  "get all my iCloud documents", "dump iCloud Drive and Notes", "inventory my notes",
  "categorize my Apple Notes", or "export my iPhone files". This skill tries multiple backends
  per source — iFetch/pyicloud for files, SQLite direct/apple_cloud_notes_parser for notes —
  so it works even if one fails. Always use this skill instead of ad-hoc scripting.
---

# iCloud Drive & Apple Notes Exporter

Bulk-export iCloud Drive files and/or Apple Notes to local directories with
structured manifests for downstream processing (categorization, tagging, RAG
indexing, migration).

**Two sources, one tool:**
- `--source files` — iCloud Drive (the Files app)
- `--source notes` — Apple Notes
- `--source both` — export everything

---

## Prerequisites

Install dependencies before running:

```bash
pip install pyicloud keyring keyrings.alt python-dateutil --break-system-packages -q
pip install ifetch 2>/dev/null || true  # iFetch may need to be cloned; see below
```

If `ifetch` is not on PyPI, clone it:

```bash
git clone https://github.com/roshanlam/iFetch.git /tmp/ifetch 2>/dev/null || true
```

For Apple Notes via parser backend (cross-platform):

```bash
docker pull ghcr.io/threeplanetssoftware/apple_cloud_notes_parser
# OR clone and use Ruby:
git clone https://github.com/threeplanetssoftware/apple_cloud_notes_parser.git /tmp/apple_cloud_notes_parser
```

---

## Inputs

- **Source**: `--source files` (iCloud Drive), `--source notes` (Apple Notes), or `--source both`
- **Apple ID email**: Required for iCloud Drive files (not needed for notes-only on macOS)
- **Target directory**: Where to save exports (default: auto based on source)
- **Scope** (optional, files only): Specific folder(s) to export, e.g. `Documents/Work`. Default: everything.
- **Notes DB** (optional, notes only): `--notes-db /path/to/NoteStore.sqlite` — auto-detected on macOS if omitted
- **Max workers** (optional, files only): Parallel download threads. Default: 4.
- **Date range** (optional): Filter by modification date with `--after` and/or `--before` (e.g. `--after 2025-01-01 --before 2025-06-30`)
- **Limit** (optional): Subselect a number of items for testing:
  - `--first N` — take the N oldest items (by modification date)
  - `--last N` — take the N newest items (by modification date)
  - `--limit N` — take the first N items after any date filtering
  - These are mutually exclusive — use only one.

### Quick-test examples

```bash
# --- iCloud Drive Files ---
# Test with just the 10 most recent files
python scripts/export_icloud_drive.py --apple-id you@icloud.com --source files --last 10

# Export only files from Q1 2025
python scripts/export_icloud_drive.py --apple-id you@icloud.com --source files \
  --after 2025-01-01 --before 2025-03-31

# --- Apple Notes ---
# Test with the 10 newest notes
python scripts/export_icloud_drive.py --source notes --last 10

# Export notes from a backup copy of NoteStore.sqlite
python scripts/export_icloud_drive.py --source notes \
  --notes-db /path/to/NoteStore.sqlite --last 10

# Export notes modified in 2025
python scripts/export_icloud_drive.py --source notes \
  --after 2025-01-01 --before 2025-12-31

# --- Both ---
# Export everything, test with 10 newest items from each
python scripts/export_icloud_drive.py --apple-id you@icloud.com --source both --last 10

# Or run the Notes script directly
python scripts/apple_notes_export.py --dest ~/notes-export --last 10
```

---

## Workflow

### Step 1: Authenticate

Both backends use pyicloud for authentication. Handle 2FA interactively:

```python
from pyicloud import PyiCloudService

api = PyiCloudService(apple_id)

if api.requires_2fa:
    code = input("Enter 2FA code from your Apple device: ")
    result = api.validate_2fa_code(code)
    if not result:
        raise SystemExit("2FA validation failed")
    api.trust_session()
elif api.requires_2sa:
    devices = api.trusted_devices
    for i, d in enumerate(devices):
        print(f"  [{i}] {d.get('deviceName', 'SMS')}")
    idx = int(input("Choose device: "))
    device = devices[idx]
    api.send_verification_code(device)
    code = input("Enter verification code: ")
    api.validate_verification_code(device, code)
```

Session cookies persist in `~/.pyicloud/` so subsequent runs skip 2FA (sessions last ~2 months).

### Step 2: Try Backend A — iFetch (preferred)

iFetch handles parallel downloads, retries, chunked transfers, and resume. Use it when available.

```bash
# If installed via pip:
python -m ifetch.cli "<icloud_path>" "<local_dest>" \
  --email="<apple_id>" \
  --max-workers=<workers> \
  --max-retries=5 \
  --log-file=export.log

# If cloned:
python /tmp/ifetch/ifetch/cli.py "<icloud_path>" "<local_dest>" \
  --email="<apple_id>" \
  --max-workers=<workers>
```

- For full drive: use `"/"` or `""` as icloud_path
- For a subfolder: use `"Documents/Projects"` etc.

If iFetch succeeds, skip to Step 4 (manifest generation).

### Step 3: Fallback — pyicloud Direct Download

If iFetch is unavailable or fails, use the pyicloud Drive API directly.
Run the bundled script:

```bash
python scripts/pyicloud_export.py \
  --apple-id "<apple_id>" \
  --dest "<local_dest>" \
  --scope "<folder_path_or_root>" \
  --workers <num_workers>
```

This script (included in `scripts/pyicloud_export.py`):
1. Authenticates via pyicloud (reuses cached session)
2. Recursively walks the iCloud Drive tree
3. Downloads each file with streaming, preserving folder structure
4. Handles errors per-file (logs failures, continues with the rest)
5. Writes a manifest on completion

### Step 4: Generate the Manifest

After download completes (via either backend), run the manifest generator:

```bash
python scripts/generate_manifest.py \
  --root "<local_dest>" \
  --output "<local_dest>/manifest.json"
```

This scans the downloaded tree and produces the structured output described below.

### Step 5: Apple Notes — SQLite Direct (macOS, preferred)

When `--source notes` or `--source both` is used, the notes exporter runs:

```bash
python scripts/apple_notes_export.py --dest ~/apple-notes-export --last 10
```

The SQLite backend reads `NoteStore.sqlite` directly from:
`~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`

It queries the Core Data tables, decompresses gzipped protobuf note bodies,
extracts plaintext, and writes each note as a markdown file with YAML frontmatter.

Requires macOS and Full Disk Access (or a copy of the database).

### Step 6: Apple Notes — Parser Fallback (cross-platform)

If SQLite direct fails, falls back to `apple_cloud_notes_parser` (Ruby/Docker):

```bash
# Via Docker (easiest cross-platform path):
docker run --rm \
  -v /path/to/NoteStore.sqlite:/data/NoteStore.sqlite:ro \
  -v ./output:/app/output \
  ghcr.io/threeplanetssoftware/apple_cloud_notes_parser \
  -f /data/NoteStore.sqlite --one-output-folder
```

This handles the complex protobuf parsing including embedded images and tables.
To get the `NoteStore.sqlite` without a Mac, extract it from an iTunes/Finder
iPhone backup.

---

## Output Format

The skill produces two things:

### 1. Downloaded Files

A local directory tree mirroring the iCloud Drive structure:

```
~/icloud-drive-export/
├── Documents/
│   ├── Work/
│   │   ├── report.pdf
│   │   └── budget.xlsx
│   └── Personal/
│       └── lease.pdf
├── Downloads/
│   └── receipt.pdf
└── manifest.json
```

### 2. Structured Manifest (manifest.json)

This is the key output for piping to downstream tools. Each file becomes a record:

```json
{
  "export_metadata": {
    "source": "icloud_drive",
    "apple_id": "user@example.com",
    "export_timestamp": "2026-04-11T14:30:00Z",
    "total_files": 1247,
    "total_size_bytes": 3489201344,
    "backend_used": "ifetch|pyicloud",
    "errors": []
  },
  "files": [
    {
      "id": "file_0001",
      "filename": "report.pdf",
      "relative_path": "Documents/Work/report.pdf",
      "absolute_path": "/home/user/icloud-drive-export/Documents/Work/report.pdf",
      "icloud_folder": "Documents/Work",
      "extension": ".pdf",
      "mime_type": "application/pdf",
      "size_bytes": 245120,
      "created": "2025-06-15T10:22:00Z",
      "modified": "2025-11-03T08:45:00Z",
      "category": null,
      "tags": [],
      "notes": ""
    }
  ]
}
```

**Field descriptions:**
- `id`: Unique sequential ID for downstream reference
- `relative_path`: Path from export root — preserves iCloud folder hierarchy
- `icloud_folder`: The parent folder path in iCloud Drive
- `extension`: File extension, lowercase, with dot
- `mime_type`: Inferred from extension via Python's `mimetypes` module
- `size_bytes`: Actual file size on disk after download
- `created` / `modified`: From iCloud metadata when available, filesystem times as fallback
- `category`: `null` — this is the field your categorization tool should populate
- `tags`: Empty list — for your categorization tool to populate
- `notes`: Empty string — for your categorization tool to add context

The `category` and `tags` fields are intentionally left empty so a downstream
tool (LLM-based categorizer, rule engine, etc.) can fill them in. The manifest
is designed to be the **input** to such a tool.

### 3. Notes Manifest (notes_manifest.json) — when `--source notes` or `both`

```json
{
  "export_metadata": {
    "source": "apple_notes",
    "export_timestamp": "2026-04-11T14:30:00Z",
    "total_notes": 1000,
    "total_size_bytes": 524288,
    "backend_used": "sqlite_direct|apple_cloud_notes_parser",
    "filters": { "after": null, "before": null, "first": null, "last": 10, "limit": null },
    "errors": []
  },
  "summary": {
    "folder_distribution": { "Work": 342, "Personal": 218, "Recipes": 45 },
    "total_body_chars": 1250000,
    "avg_note_length": 1250
  },
  "notes": [
    {
      "id": "note_0001",
      "title": "Q1 Planning Meeting",
      "filename": "Q1-Planning-Meeting.md",
      "relative_path": "Work/Q1-Planning-Meeting.md",
      "absolute_path": "/home/user/apple-notes-export/Work/Q1-Planning-Meeting.md",
      "icloud_folder": "Work",
      "account": "iCloud",
      "extension": ".md",
      "mime_type": "text/markdown",
      "size_bytes": 2048,
      "body_length": 1850,
      "body_preview": "Attendees: Alice, Bob, Carol. Key decisions: ...",
      "created": "2025-01-15T09:00:00Z",
      "modified": "2025-01-15T10:30:00Z",
      "category": null,
      "tags": [],
      "notes": ""
    }
  ]
}
```

**Notes-specific fields:**
- `title`: The note's title from Apple Notes
- `account`: iCloud, On My Mac, Gmail, etc.
- `body_length`: Character count of the note body (useful for filtering short vs long notes)
- `body_preview`: First 200 characters — enough for a categorizer to work with without reading the file
- Each note is also saved as a `.md` file with YAML frontmatter for easy reading

---

## Piping to a Categorizer

After export, the manifest can be consumed by any tool that reads JSON. Example pattern:

```python
import json

with open("manifest.json") as f:
    manifest = json.load(f)

for file_record in manifest["files"]:
    # Your categorization logic here, e.g.:
    # - Rule-based: assign category by extension or folder
    # - LLM-based: read file content + metadata, ask Claude to categorize
    # - Hybrid: rules first, LLM for ambiguous cases
    file_record["category"] = assign_category(file_record)
    file_record["tags"] = assign_tags(file_record)

with open("manifest_categorized.json", "w") as f:
    json.dump(manifest, f, indent=2)
```

---

## Edge Cases

### iCloud Drive (Files)
- **2FA required every time**: Apple expires sessions. If the user has Advanced Data Protection enabled, sessions may be shorter. Suggest generating an app-specific password at appleid.apple.com for automation use.
- **Very large drives (10k+ files)**: iFetch handles this well with parallel workers. For pyicloud fallback, the script uses streaming downloads and processes files one at a time to avoid memory issues.
- **Files that fail to download**: Both backends log failures. The manifest includes an `errors` array in `export_metadata` listing files that couldn't be downloaded, with the error reason.
- **Encrypted/protected files**: Some iCloud files may be protected by Advanced Data Protection. These will fail to download via the API. The manifest notes them as errors.
- **Name collisions**: The export preserves exact iCloud paths, so collisions only occur if iCloud itself has them (unlikely).
- **No Mac required**: Both backends work on any platform (Windows, Linux, macOS) — they talk to iCloud's web API, not local filesystem.

### Apple Notes
- **Database locked**: If Notes.app is open, the SQLite database may be locked. Close Notes.app first, or use a copy of the database.
- **Protobuf parsing limitations**: The direct SQLite reader does best-effort plaintext extraction from Apple's gzipped protobuf format. Complex formatting (tables, checklists, drawings) may not survive perfectly. For highest fidelity, use `--backend parser` with apple_cloud_notes_parser.
- **Encrypted notes**: Notes locked with a password in Apple Notes cannot be decrypted by either backend. They appear in the manifest with empty body content.
- **Notes with images/attachments**: The SQLite backend extracts text only. The parser backend can extract embedded images to separate files.
- **Getting NoteStore.sqlite without a Mac**: Extract from an iTunes/Finder iPhone backup. The file is at path `4f98687d8ab0d6d1a371110e6b7300f6e465bef2` in the backup manifest.
- **1000+ notes**: Both backends handle large note libraries. The SQLite backend is faster since it reads locally. Apply `--last 10` for initial testing.
