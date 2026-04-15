# iCloud Notes Exporter — End-to-End Pipeline

Export every note from iCloud Notes on the web, clean and categorize them with OpenAI, then produce CSV / Markdown / task-list outputs.

---

## Folder structure

```
icloud_notes_exporter/
├── run_pipeline.py          ← single entry-point (runs all steps)
├── requirements.txt
├── scraper/
│   └── scrape_notes.py      ← Step 1: Playwright browser automation
├── cleaner/
│   └── categorize_notes.py  ← Step 2: OpenAI enrichment
├── utils/
│   ├── merge_exports.py     ← merge per-folder JSON files
│   └── convert_output.py    ← JSON → CSV / Markdown / tasks
└── output/                  ← all generated files land here
    ├── notes_export.json
    ├── notes_enriched.json
    ├── notes_export.csv
    ├── notes_archive.md
    └── tasks.md
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Set your OpenAI key

```bash
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."

# macOS / Linux
export OPENAI_API_KEY="sk-..."
```

### 3. Run the full pipeline

```bash
python run_pipeline.py
```

A Chromium window will open.
Log in to iCloud (including 2FA), wait for Notes to fully load, then press **Enter** in the terminal.
The rest is automated.

---

## Step-by-step options

### Scrape only (no OpenAI)

```bash
python run_pipeline.py --steps scrape
```

### Only one folder

```bash
python run_pipeline.py --folder "Read"
```

### Test run (first 10 notes)

```bash
python run_pipeline.py --limit 10
```

### Enrich an existing export without re-scraping

```bash
python run_pipeline.py --steps enrich convert
```

### Use a better model for higher quality

```bash
python run_pipeline.py --steps enrich --model gpt-4o
```

### Resume an interrupted enrichment run

```bash
python run_pipeline.py --steps enrich --resume
```

---

## Per-script usage

### Scraper

```bash
cd scraper
python scrape_notes.py --folder "Read" --limit 50 --pause 1.5 --out ../output/notes_export.json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--folder` | (all) | Only scrape this folder |
| `--limit` | 0 (all) | Stop after N notes |
| `--pause` | 1.2 | Seconds between note clicks |
| `--wait-login` | 0 | Extra wait after you press Enter |
| `--out` | `../output/notes_export.json` | Output path |

**Tip:** Run `playwright codegen https://www.icloud.com/notes` first to capture live selectors if the page structure has changed.

---

### Categorizer / Cleaner

```bash
cd cleaner
python categorize_notes.py --in ../output/notes_export.json --model gpt-4o-mini --batch-size 5
```

Each note gets these new fields:

| Field | Values |
|-------|--------|
| `item_kind` | `task` · `code` · `paper` · `reference` · `link` · `project` · `archive` |
| `category` | lowercase word, e.g. `ai`, `finance`, `health`, `productivity` |
| `priority` | `high` · `medium` · `low` |
| `next_action` | one-line action (tasks only) |
| `clean_summary` | 1–3 sentence polished summary |
| `tags` | list of 2–5 lowercase tags |

---

### Merge per-folder exports

If you exported folder-by-folder, merge before enriching:

```bash
cd utils
python merge_exports.py --dir ../output/ --out ../output/notes_export.json
```

---

### Convert to CSV / Markdown

```bash
cd utils
python convert_output.py --in ../output/notes_enriched.json --out-dir ../output/
```

Produces:
- `notes_export.csv` — flat spreadsheet, great for reviewing in Excel
- `notes_archive.md` — one section per note, collapsible raw text
- `tasks.md` — checkbox task list grouped by priority

---

## Recommended workflow for large note libraries

1. **Test first** — run with `--limit 10` to verify selectors work
2. **Export by folder** — use `--folder` for each folder, saving to separate files
3. **Merge** — `python utils/merge_exports.py`
4. **Enrich in batches** — use `--resume` if you need to restart
5. **Convert** — produces CSV, archive, and task list

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No notes found | Run `playwright codegen` to inspect live selectors; update `NOTE_SELECTORS` in `scrape_notes.py` |
| Login loop / 2FA keeps triggering | Increase `--wait-login 30` to give yourself more time |
| Notes load slowly | Increase `--pause 2.5` |
| OpenAI rate limit | Increase `--delay 2.0` or reduce `--batch-size 2` |
| JSON parse error from model | Add `--model gpt-4o` (more reliable JSON output) |

---

## Privacy note

Your iCloud credentials never leave your machine. The Playwright script runs in a local browser window that you log into manually. Only the exported text (not your credentials) is sent to OpenAI for enrichment.
