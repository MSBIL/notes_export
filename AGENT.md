# Agent Brief

## Project Goal

This repository is a personal export toolkit for turning private knowledge
sources into durable local files, with Markdown as the preferred long-term
archive format.

Primary sources:

- Apple Notes
- iCloud Drive / Apple Files
- O'Reilly Learning playlists, expert lists, learning paths, and related book
  metadata

The intended end state is a repeatable pipeline that exports source data,
normalizes metadata, and produces Markdown files plus JSON manifests that can be
indexed, searched, reviewed, or imported into another knowledge system.

## Current Capabilities

- `apple_notes_export.py` exports Apple Notes from `NoteStore.sqlite` into
  per-note Markdown files with YAML frontmatter and a `notes_manifest.json`.
- `export_icloud_drive.py` is the top-level Apple export wrapper. It can export
  iCloud Drive files through iFetch or pyicloud, export Apple Notes, and generate
  manifests.
- `icloud_notes_exporter/` contains a Playwright web pipeline for iCloud Notes:
  scrape notes, optionally enrich them with OpenAI, and convert JSON into CSV,
  Markdown archive, and task Markdown.
- `oreilly_exporter/` exports O'Reilly playlists, expert lists, and learning
  paths through Playwright. It currently produces raw JSON, CSV files, and Excel
  workbooks.
- `mirror/oreilly-epub-downloader/` is a reviewed reference implementation for
  cookie-authenticated O'Reilly book EPUB downloads.
- `mirror/safaribooks/` is a reviewed legacy reference implementation for
  cookie-authenticated Safari/O'Reilly EPUB downloads.

## Important Gap

O'Reilly list export is not yet a Markdown-first pipeline. The current local
O'Reilly exporter writes JSON, CSV, and XLSX. A future converter should turn
`oreilly_raw.json` or `oreilly_export.csv` into Markdown files such as:

- one Markdown file per collection
- one Markdown file per content item
- an index Markdown file linking collections, items, O'Reilly URLs, companion
  links, estimated duration, table of contents, and tags

iCloud Drive exports preserve files and generate manifests. Arbitrary Apple
Files are not automatically converted to Markdown yet. Any conversion layer
should be explicit by file type and should keep originals intact.

## Repository Map

- `apple_notes_export.py`: macOS/local database Apple Notes to Markdown exporter.
- `export_icloud_drive.py`: unified Apple files and notes command-line entry
  point.
- `scrape_icloud_notes.py`: standalone iCloud Notes web scraper.
- `icloud_notes_exporter/`: structured iCloud Notes scrape/enrich/convert
  pipeline.
- `oreilly_exporter/`: O'Reilly collection scraper and spreadsheet/reporting
  tools.
- `skill_payload/icloud-drive-exporter/`: packaged skill payload containing
  related scripts.
- `mirror/`: local clones of external O'Reilly downloader projects used as
  reference material. Treat these as upstream mirrors, not product code, unless
  explicitly vendored or converted to submodules.

## Reference Repo Findings

`mirror/oreilly-epub-downloader` is a modern, smaller Python 3.11 project. It
uses cookie JSON, `httpx`, BeautifulSoup, `ebooklib`, Click, and Rich. It has a
cleaner separation between CLI, API client, cookie auth, models, and EPUB
creation. It is useful reference code for modern O'Reilly authentication and
content retrieval.

`mirror/safaribooks` is older and largely monolithic. Its README says direct
login no longer works and cookie-based auth is required. It still contains useful
ideas for EPUB layout, URL handling, and legacy API behavior, but it should not
be used as the main style model for new code.

## Development Guidance

- Prefer new integration work in this repository's own scripts instead of
  modifying mirrored upstream repos.
- Preserve original exports. Add Markdown conversion as a downstream step rather
  than replacing JSON, CSV, XLSX, or original downloaded files.
- Keep credentials, cookies, auth state, exported private data, and local runtime
  logs out of Git.
- For Playwright workflows, keep manual login and 2FA in the browser; do not
  collect credentials in code.
- For O'Reilly workflows, respect subscription and terms-of-service boundaries.
  Treat downloaded content as personal-use archival material.
- Keep converters deterministic: the same input manifest should produce the same
  Markdown paths and frontmatter.
- Use manifests as the integration contract between source-specific exporters
  and Markdown-specific output tools.

## Suggested Next Work

1. Add an `oreilly_exporter/utils/export_markdown.py` converter.
2. Add a root-level orchestrator that can run Apple Notes, iCloud Drive, and
   O'Reilly exports into a shared `output/` directory.
3. Define a common frontmatter schema across exported notes, files, and O'Reilly
   items.
4. Add tests for filename sanitization, frontmatter escaping, manifest parsing,
   and Markdown output shape.
5. Document private output directories in `.gitignore` before running real
   exports.
