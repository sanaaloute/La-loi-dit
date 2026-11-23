#!/usr/bin/env python3
"""Audit data/legal_docs/to_ingest for PDFs whose embedded text layer is
broken OCR garbage (the "Lel dilpolitionl" problem).

Uses the same rule as the ingestion loader: a page is flagged when its
embedded text fails the French-trigram quality gate AND the page carries an
image (so a real OCR pass can replace it). Flagged documents do NOT need any
registry purge: on the next ingestion run their extracted text changes, the
content hash differs, and the pipeline re-ingests them automatically,
deleting the old garbage chunks. This script only reports.

Writes the full list to data/bad_layer_report.json.

Usage:
    python scripts/reocr_bad_layers.py            # scan + report
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "legal_docs" / "to_ingest"
REPORT = ROOT / "data" / "bad_layer_report.json"
VERSIONS = ROOT / "data" / "versions.json"

_OCR_TEXT_THRESHOLD = 20


def scan_pdf(raw: str) -> tuple[str, int, int]:
    """Return (filename, flagged_pages, total_pages) for one PDF."""
    import pymupdf

    from backend.ingestion import text_quality
    from backend.core.config import get_settings

    min_score = get_settings().ocr_bad_layer_min_score
    path = Path(raw)
    flagged = 0
    try:
        with pymupdf.open(path) as pdf:
            total = len(pdf)
            for i in range(total):
                text = pdf[i].get_text()
                if len(text) < _OCR_TEXT_THRESHOLD:
                    continue  # scanned-page bucket: handled by the plain OCR path
                if not text_quality.page_needs_ocr(text, min_score):
                    continue
                if pdf[i].get_images():
                    flagged += 1
    except Exception as exc:  # unreadable PDF: report, don't crash the audit
        return (path.name, -1, 0)
    return (path.name, flagged, total)


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    files = sorted(str(p) for p in TARGET.iterdir() if p.suffix.lower() == ".pdf")
    print(f"Scanning {len(files)} PDFs with {mp.cpu_count()} workers…")

    results: list[tuple[str, int, int]] = []
    with mp.Pool() as pool:
        for i, res in enumerate(pool.imap_unordered(scan_pdf, files, chunksize=16), 1):
            results.append(res)
            if i % 1000 == 0:
                print(f"  {i}/{len(files)}")

    bad = [(n, f, t) for n, f, t in results if f > 0]
    unreadable = [n for n, f, _ in results if f < 0]
    versions = json.loads(VERSIONS.read_text(encoding="utf-8"))
    indexed_bad = [
        n for n, _, _ in bad if hashlib.sha256(n.encode()).hexdigest()[:16] in versions
    ]

    REPORT.write_text(
        json.dumps(
            {
                "scanned": len(results),
                "bad_documents": [
                    {"file": n, "flagged_pages": f, "total_pages": t}
                    for n, f, t in sorted(bad)
                ],
                "unreadable": sorted(unreadable),
                "already_indexed": sorted(indexed_bad),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nScanned: {len(results)}")
    print(f"Bad embedded layer (will be re-OCR'd on next ingestion run): {len(bad)}")
    print(f"  of which already indexed (auto re-ingest via content-hash change): {len(indexed_bad)}")
    print(f"Unreadable PDFs: {len(unreadable)}")
    print(f"Report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
