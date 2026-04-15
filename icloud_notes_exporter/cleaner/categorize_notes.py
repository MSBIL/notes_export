"""
categorize_notes.py
───────────────────
Reads notes_export.json, calls OpenAI to:
  • clean the raw text
  • assign item_kind  (task | code | paper | reference | link | project | archive)
  • assign category   (e.g. ai, productivity, finance, health, personal, …)
  • assign priority   (high | medium | low)
  • extract next_action  (one-line action if item_kind == task)
  • write clean_summary  (≤ 3 sentences)

Writes enriched JSON to output/notes_enriched.json.

Usage
-----
  export OPENAI_API_KEY="sk-..."
  python categorize_notes.py
  python categorize_notes.py --in ../output/notes_export.json --out ../output/notes_enriched.json
  python categorize_notes.py --batch-size 5   # notes per API call (default 5)
  python categorize_notes.py --model gpt-4o   # override model (default gpt-4o-mini)
  python categorize_notes.py --resume         # skip notes that already have item_kind

Environment
-----------
  OPENAI_API_KEY  required
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from textwrap import dedent

try:
    from openai import OpenAI
except ImportError:
    print("❌  openai package not found.  Run:  pip install openai")
    sys.exit(1)


# ─── prompt ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = dedent("""\
    You are a personal knowledge-management assistant.
    You receive a list of raw Apple Notes exports (JSON).
    For each note return a JSON object with EXACTLY these keys:

      id            – unchanged from input
      item_kind     – one of: task | code | paper | reference | link | project | archive
      category      – a lowercase single word or short phrase (e.g. ai, finance, health,
                      productivity, personal, travel, dev, reading, misc)
      priority      – high | medium | low
      next_action   – if item_kind is "task", one short imperative sentence; else ""
      clean_summary – 1-3 sentence polished summary of the note; preserve key facts/links
      tags          – list of 2-5 relevant lowercase tags

    Rules:
    • Do not invent facts; only use what is in raw_text and title.
    • If the note is mostly a URL, set item_kind = "link".
    • If the note contains runnable code snippets, set item_kind = "code".
    • If the note references an academic paper or article to read, set item_kind = "paper".
    • If the note is a list of to-dos or action items, set item_kind = "task".
    • Return ONLY a valid JSON array. No markdown fences, no commentary.
""")


def make_user_message(batch: list[dict]) -> str:
    slim = [
        {"id": n["id"], "title": n.get("title", ""), "raw_text": n.get("raw_text", "")}
        for n in batch
    ]
    return json.dumps(slim, ensure_ascii=False)


# ─── core ────────────────────────────────────────────────────────────────────

def enrich_batch(client: OpenAI, batch: list[dict], model: str) -> list[dict]:
    """Send one batch to OpenAI and return enriched list."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": make_user_message(batch)},
        ],
        temperature=0.2,
        response_format={"type": "json_object"} if "gpt-4" in model else None,
    )
    raw = response.choices[0].message.content.strip()

    # strip markdown fences if model wrapped anyway
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        parsed = json.loads(raw)
        # model might return {"notes": [...]} or just [...]
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
        return parsed
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error: {e}")
        print(f"  Raw response (first 500 chars):\n{raw[:500]}")
        return []


def merge_enrichment(original: dict, enriched: dict) -> dict:
    """Overlay enriched fields onto the original note dict."""
    merged = dict(original)
    for key in ("item_kind", "category", "priority", "next_action", "clean_summary", "tags"):
        if key in enriched:
            merged[key] = enriched[key]
    return merged


# ─── main ────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Enrich iCloud Notes export with OpenAI")
    p.add_argument("--in",         dest="input",  default="../output/notes_export.json")
    p.add_argument("--out",        dest="output", default="../output/notes_enriched.json")
    p.add_argument("--model",      default="gpt-4o-mini",
                   help="OpenAI model (default gpt-4o-mini; use gpt-4o for better quality)")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Notes per API call (default 5)")
    p.add_argument("--resume",     action="store_true",
                   help="Skip notes that already have item_kind in the output file")
    p.add_argument("--delay",      type=float, default=0.5,
                   help="Seconds between API calls (default 0.5)")
    return p.parse_args()


def main():
    args = parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("❌  OPENAI_API_KEY environment variable is not set.")
        print("    Set it with:  export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    in_path  = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        print(f"❌  Input file not found: {in_path.resolve()}")
        sys.exit(1)

    with open(in_path, encoding="utf-8") as f:
        notes: list[dict] = json.load(f)

    print(f"📥  Loaded {len(notes)} notes from {in_path.resolve()}")

    # build enriched index if --resume
    enriched_index: dict[str, dict] = {}
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        enriched_index = {n["id"]: n for n in existing if n.get("item_kind")}
        print(f"🔄  Resume mode: {len(enriched_index)} already enriched")

    # filter notes to process
    to_process = [n for n in notes if n["id"] not in enriched_index]
    print(f"⚙️   Processing {len(to_process)} notes with model «{args.model}»")
    print(f"    Batch size: {args.batch_size}  ·  Estimated API calls: "
          f"{(len(to_process) + args.batch_size - 1) // args.batch_size}\n")

    results: dict[str, dict] = dict(enriched_index)  # start with already-done

    for batch_start in range(0, len(to_process), args.batch_size):
        batch = to_process[batch_start: batch_start + args.batch_size]
        batch_ids = [n["id"] for n in batch]
        print(f"  🔄  Batch {batch_start // args.batch_size + 1}: "
              f"notes {batch_ids[0]}–{batch_ids[-1]}")

        enriched_list = enrich_batch(client, batch, args.model)

        # build lookup by id
        enriched_by_id = {e["id"]: e for e in enriched_list if "id" in e}

        for orig in batch:
            nid = orig["id"]
            if nid in enriched_by_id:
                results[nid] = merge_enrichment(orig, enriched_by_id[nid])
                kind = results[nid].get("item_kind", "?")
                title = orig.get("title", "")[:60]
                print(f"    ✔  {nid}  [{kind:<10}]  {title}")
            else:
                print(f"    ⚠️  {nid} not returned by model — keeping original")
                results[nid] = orig

        # save incrementally after each batch
        ordered = [results[n["id"]] for n in notes if n["id"] in results]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(ordered, f, ensure_ascii=False, indent=2)

        if args.delay and batch_start + args.batch_size < len(to_process):
            time.sleep(args.delay)

    # final save with all notes (including any not-yet-processed ones)
    all_results = []
    for n in notes:
        if n["id"] in results:
            all_results.append(results[n["id"]])
        else:
            all_results.append(n)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n💾  Enriched export → {out_path.resolve()}")
    print(f"    Total: {len(all_results)}  |  Enriched: {len(results)}")

    # quick summary by kind
    from collections import Counter
    kinds = Counter(n.get("item_kind", "unknown") for n in all_results)
    print("\n📊  Breakdown by item_kind:")
    for k, v in kinds.most_common():
        print(f"    {k:<15} {v}")


if __name__ == "__main__":
    main()
