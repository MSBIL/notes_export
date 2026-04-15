# O'Reilly Bulk Exporter

Exports **all three content collection types** from your O'Reilly Learning account into a rich Excel workbook + CSV — fully automated after a one-time login.

---

## What you get

| Output file | Description |
|---|---|
| `oreilly_raw.json` | Raw scraped data (resume-safe checkpoint) |
| `oreilly_export.xlsx` | 5-sheet Excel with colour coding + hyperlinks |
| `oreilly_export.csv` | Flat CSV — one row per item |
| `oreilly_collections.csv` | One row per collection (name + count) |

### Excel sheets

| Sheet | Contents |
|---|---|
| 📋 All Items | Every item from every collection, flat |
| 🗂 My Playlists | Grouped by your personal playlists |
| ⭐ Expert Lists | Grouped by expert/curated playlists |
| 🎓 Learning Paths | Grouped by learning path |
| 📊 Summary | Totals, type breakdown, duplicate report |

### Colour coding (by content type)

| Colour | Type |
|---|---|
| 🟦 Light blue | Book |
| 🟪 Light purple | Video |
| 🟩 Light green | Course |
| 🟨 Light amber | Live training |
| 🩷 Light pink | Sandbox |
| 🟫 Light grey | Unknown |

---

## Setup (Windows, run once)

```powershell
# Navigate to this folder
cd C:\path\to\oreilly_exporter

# Install dependencies
conda install -c conda-forge greenlet   # if you're using Miniconda
pip install -r requirements.txt
python -m playwright install chromium
```

---

## Run

```powershell
# Set your working directory to this folder first
cd C:\path\to\oreilly_exporter

# Full pipeline (recommended first run)
python run_pipeline.py
```

A browser window will open. Complete your **company SSO login** (Okta / Azure / Google — 2FA is fine). Once you see your playlists, the script takes over automatically.

Your session is saved to `oreilly_auth_state.json` — **no login needed on future runs**.

---

## Options

### Only scrape specific content types

```powershell
python run_pipeline.py --types playlists expert_playlists
python run_pipeline.py --types learning_paths
```

### Resume an interrupted scrape

```powershell
python run_pipeline.py --resume
```
Skips any collection already in `oreilly_raw.json`.

### Re-export without re-scraping

```powershell
python run_pipeline.py --steps excel csv
```
Useful if you want to tweak the Excel format and already have the raw JSON.

### Slow down for flaky connections

```powershell
python run_pipeline.py --pause 2.5
```

### Test run (scrape only, manually limit in the browser)

```powershell
python run_pipeline.py --steps scrape --types playlists
```
Then press Ctrl+C once you've captured enough — the JSON is saved after each playlist.

---

## How sub-lists work

If any playlist item is itself a link to another playlist (a **sub-list**), the script:
1. Flags it with `is_sub_list = True`
2. Navigates into it and extracts its children
3. Marks those children with `sub_list_parent = <parent name>`

Sub-list expansion goes up to depth 2 to avoid infinite loops.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `greenlet` build error | `conda install -c conda-forge greenlet` |
| `playwright` not recognised | Use `python -m playwright install chromium` |
| Login not detected | Increase `--pause 2.5`; make sure you're fully past the SSO screen |
| Session expired (auth state invalid) | Delete `oreilly_auth_state.json` and run again |
| No playlists found | O'Reilly may have updated their UI — open `scraper/scrape_oreilly.py` and update the selector patterns in `extract_playlist_links()` |
| Too many noise items | Tighten selectors in `extract_items_from_page()` |
| Timeout on large playlists | Increase `--pause 2.0` |

---

## Privacy

Your credentials never leave your machine. The script uses a real browser session that **you** log into. Only the page content (titles, URLs) is processed locally — nothing is sent to any external service.
