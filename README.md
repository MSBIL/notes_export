# Notes Export Toolkit

Export Apple Notes, iCloud Drive files, and O'Reilly Learning lists into a local
knowledge archive. The target archive format is Markdown plus machine-readable
JSON manifests.

## Status

This repository is an active toolkit, not a polished package.

- Apple Notes to Markdown is supported through `apple_notes_export.py`.
- iCloud Notes web scraping can produce Markdown archives through
  `icloud_notes_exporter/`.
- iCloud Drive file export can preserve files and generate manifests through
  `export_icloud_drive.py`.
- O'Reilly list export currently produces JSON, CSV, and XLSX. Markdown export
  for O'Reilly lists is the next implementation gap.

## Repository Layout

```text
.
|-- apple_notes_export.py
|-- export_icloud_drive.py
|-- scrape_icloud_notes.py
|-- icloud_notes_exporter/
|   |-- run_pipeline.py
|   |-- scraper/
|   |-- cleaner/
|   `-- utils/
|-- oreilly_exporter/
|   |-- run_pipeline.py
|   |-- build_review_list.py
|   |-- enrich_sheet.py
|   |-- scraper/
|   `-- utils/
|-- skill_payload/
`-- mirror/
    |-- oreilly-epub-downloader/
    `-- safaribooks/
```

`mirror/` contains upstream reference clones for O'Reilly EPUB download behavior.
They are useful for research, but the main project should evolve in the root
scripts and first-party exporter folders.

## Requirements

- Python 3.11 recommended for new work
- Git
- Playwright Chromium for browser-based iCloud and O'Reilly workflows
- An Apple ID for iCloud Drive exports
- Local Apple Notes database access on macOS, or a copied `NoteStore.sqlite`
- An O'Reilly Learning account for O'Reilly list exports
- Optional: OpenAI API key for note enrichment
- Optional: Docker or Ruby for `apple_cloud_notes_parser` fallback support

## Setup

Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install dependencies for the workflow you need:

```powershell
pip install -r icloud_notes_exporter\requirements.txt
pip install -r oreilly_exporter\requirements.txt
pip install python-dateutil
python -m playwright install chromium
```

For OpenAI enrichment:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

## Apple Notes Export

Use the local database exporter when you have access to `NoteStore.sqlite`:

```powershell
python apple_notes_export.py --dest output\apple-notes
```

Export a copied database:

```powershell
python apple_notes_export.py `
  --db C:\path\to\NoteStore.sqlite `
  --dest output\apple-notes
```

Test with a small sample:

```powershell
python apple_notes_export.py --dest output\apple-notes --last 10
```

Expected output:

- one `.md` file per note, grouped by source folder
- `notes_manifest.json`

## iCloud Notes Web Pipeline

Use this path when you want to scrape iCloud Notes through the browser and
optionally enrich or convert the result:

```powershell
cd icloud_notes_exporter
python run_pipeline.py --limit 10
```

The browser opens for manual iCloud login and 2FA. After scraping, the pipeline
can enrich notes with OpenAI and convert the output into:

- `notes_export.csv`
- `notes_archive.md`
- `tasks.md`

Run only conversion when JSON already exists:

```powershell
python run_pipeline.py --steps convert
```

## iCloud Drive / Apple Files Export

Use the unified Apple wrapper for iCloud Drive files:

```powershell
python export_icloud_drive.py `
  --apple-id you@example.com `
  --source files `
  --dest output\icloud-drive
```

Export both iCloud Drive files and Apple Notes:

```powershell
python export_icloud_drive.py `
  --apple-id you@example.com `
  --source both `
  --dest output\apple-export
```

Expected output for files:

- downloaded files preserved in their source structure
- `manifest.json`

Apple Files are not automatically transformed into Markdown yet. Future
conversion should be file-type aware and should keep originals alongside any
generated `.md` files.

## O'Reilly Lists Export

Use the first-party O'Reilly exporter to collect personal playlists, expert
lists, and learning paths:

```powershell
cd oreilly_exporter
python run_pipeline.py
```

The browser opens for manual O'Reilly or company SSO login. Session state is
saved locally for future runs.

Current output:

- `output\oreilly_raw.json`
- `output\oreilly_export.csv`
- `output\oreilly_collections.csv`
- `output\oreilly_export.xlsx`

Build or enrich review workbooks:

```powershell
python build_review_list.py --resume
python enrich_sheet.py --resume
```

Planned Markdown output:

- collection index files
- one item page per book, video, course, or learning path item
- frontmatter for title, source URL, collection, content type, author,
  duration, companion links, table of contents, and tags

## Reference O'Reilly Downloaders

Two external projects are mirrored locally for source review:

- `mirror/oreilly-epub-downloader`: modern Python 3.11 O'Reilly EPUB downloader
  using cookie JSON and a separated client/EPUB architecture.
- `mirror/safaribooks`: older Safari/O'Reilly EPUB downloader. Its direct login
  flow is no longer current, but it remains useful as a legacy reference.

Do not copy large sections from these projects blindly. Use them to understand
authentication, API behavior, EPUB structure, and failure modes.

## Output Contract

Markdown exporters should use YAML frontmatter where practical:

```markdown
---
source: apple_notes
title: Example
created: 2026-01-01T12:00:00Z
modified: 2026-01-02T12:00:00Z
tags: []
---

# Example

Body text...
```

Manifests should remain the stable integration contract between source-specific
exporters and downstream Markdown converters.

## Privacy

This project handles private notes, private files, browser cookies, and account
session state. Keep exports, credentials, cookies, and auth-state files out of
Git unless they have been intentionally sanitized.

Manual browser login is preferred for Apple and O'Reilly workflows so credentials
do not need to be stored in code.

## Roadmap

- Add O'Reilly JSON/CSV to Markdown conversion.
- Add Apple Files manifest to Markdown index conversion.
- Define one shared frontmatter schema for all exported item types.
- Add tests for converters and filename sanitization.
- Add a root orchestrator that exports all sources into one local Markdown
  archive.
