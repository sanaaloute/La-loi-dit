#!/usr/bin/env python3
"""Incremental crawler for newly promulgated laws (assembleenationale.bf).

Reuses the battle-tested parsing of scripts/download_assemblee.py, but instead
of a one-shot bulk download it diffs the site against the CURRENT corpus
(law numbers already indexed) and downloads only what's new.

Usage:
  python scripts/crawl_assemblee.py [--pages 2] [--ingest]

  --pages N   scan the N newest listing pages (default 2)
  --ingest    ingest each downloaded PDF right away (pipeline subprocess;
              LEGAL_AI_MILVUS_HOST is overridden to localhost for host runs)

Designed for a weekly cron/LaunchAgent on the server. Safe to re-run: known
law numbers are skipped, and ingestion itself is content-hash idempotent.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_assemblee import list_detail_ids, parse_detail  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CORPUS_DIR = DATA_DIR / "legal_docs" / "bf"
STATE_PATH = DATA_DIR / "crawl_assemblee_state.json"

_LAW_NO_RE = re.compile(r"(?:n[°o]?\s*)?0*(\d{1,4})\s*[-–/]\s*(\d{4})", re.IGNORECASE)


def known_law_numbers() -> set[str]:
    """Law numbers (NNN-YYYY, zero-stripped) already in the corpus.

    Scans titles AND filenames — mangled metadata titles ("Loi n°2024 — 027
    2024 alt …") defeat number parsing, but the journal paths and manifest
    keys carry clean filenames ("…_027-2024-alt_….pdf").
    """
    known: set[str] = set()
    texts: list[str] = []
    try:
        journal = json.loads((DATA_DIR / "ingestion_results.json").read_text(encoding="utf-8"))
        for e in journal.values():
            texts.append(e.get("document_name", ""))
            texts.append(Path(e.get("path", "")).name)
    except OSError:
        pass
    try:
        manifest = json.loads((DATA_DIR / "legal_sources.json").read_text(encoding="utf-8"))
        texts += list(manifest.get("document_metadata", {}).keys())  # filenames
        texts += [v.get("document_name", "") for v in manifest.get("document_metadata", {}).values()]
        texts += list(manifest.get("document_titles", {}).keys())  # filenames
        texts += list(manifest.get("document_titles", {}).values())
    except OSError:
        pass
    for text in texts:
        for m in _LAW_NO_RE.finditer(text):
            known.add(f"{int(m.group(1))}-{m.group(2)}")
    return known


def law_number_of(title: str) -> str | None:
    m = _LAW_NO_RE.search(title)
    return f"{int(m.group(1))}-{m.group(2)}" if m else None


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen_detail_ids": []}


def ingest_pdf(pdf: Path, title: str, source_url: str) -> bool:
    """Run the ingestion pipeline in a subprocess (host Milvus = localhost)."""
    import os

    env = dict(os.environ)
    env["LEGAL_AI_MILVUS_HOST"] = "localhost"
    env["LEGAL_AI_DATA_DIR"] = str(DATA_DIR)
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            "-m",
            "backend.ingestion.pipeline",
            str(pdf),
            "--name",
            title,
            "--url",
            source_url,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if result.returncode != 0:
        print(f"  [ingest FAILED] {pdf.name}: {result.stderr[-300:]}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--ingest", action="store_true")
    args = parser.parse_args()

    state = load_state()
    seen_ids = set(state["seen_detail_ids"])
    known = known_law_numbers()
    print(f"[init] {len(known)} law numbers in corpus, {len(seen_ids)} detail pages seen")

    detail_ids = list_detail_ids(max_page=args.pages)
    new_ids = [i for i in detail_ids if i not in seen_ids]
    print(f"[list] {len(detail_ids)} detail pages scanned, {len(new_ids)} new")

    downloaded = 0
    for lid in new_ids:
        info = parse_detail(lid)
        if not info:
            seen_ids.add(lid)
            continue
        number = law_number_of(info["title"])
        if number and number in known:
            print(f"  [skip] already in corpus: {number} — {info['title'][:60]}")
            seen_ids.add(lid)
            continue
        filename = f"assemblee_{re.sub(r'[^A-Za-z0-9_-]+', '_', info['title'])[:80] or lid}.pdf"
        out_path = CORPUS_DIR / filename
        print(f"  [download] {filename} <- {info['pdf_url']}")
        try:
            import urllib.request

            req = urllib.request.Request(info["pdf_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                out_path.write_bytes(resp.read())
        except Exception as exc:
            print(f"  [error] {info['pdf_url']}: {exc}")
            continue
        downloaded += 1
        seen_ids.add(lid)
        known.add(number or filename)
        if args.ingest:
            ok = ingest_pdf(out_path, info["title"], info["source_url"])
            print(f"  [ingest] {'ok' if ok else 'failed'}: {filename}")
        time.sleep(0.5)

    state["seen_detail_ids"] = sorted(seen_ids)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"[done] downloaded={downloaded} (ingest={'on' if args.ingest else 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
