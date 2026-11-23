#!/usr/bin/env python3
"""Build the bundled French character-trigram model for text-quality scoring.

The model is bootstrapped from our own corpus so it reflects legal French as
actually written in Burkina Faso official texts:

* born-digital (clean) text layers of reference documents;
* tesseract OCR of a sample of pages from the scanned legacy corpus.

Output: ``backend/ingestion/french_trigrams.json`` — the sorted list of
character trigrams seen at least ``--min-count`` times. A page's quality
score is the fraction of its trigrams present in that list; broken OCR text
layers ("Lel dilpolitionl", "réglementationbancairelont") score low.

Usage:
    .venv/bin/python scripts/build_french_trigrams.py [--min-count 2]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "ingestion" / "french_trigrams.json"

# Clean born-digital reference documents (verified by inspection), given as
# glob patterns under data/legal_docs/to_ingest (crawler-truncated names
# vary). Covers laws, reports, budget tables and minutes so the model is not
# biased toward one genre.
CLEAN_DOC_GLOBS = [
    "*_1775_loi_*.pdf",        # loi 061-2015/CNT (born-digital)
    "*_44_rapport_cdd_*.pdf",  # rapport 2023 (born-digital)
    "*_1793_loi_*.pdf",        # code général des impôts 2017 (born-digital)
    "*_1023_loi_*.pdf",        # loi 045/98/AN (clean scan text layer)
    "*_17_pv_*.pdf",           # procès-verbal 2024 (born-digital)
]
# Legacy scanned corpus: OCR a few pages of each to broaden coverage.
SCANNED_SAMPLE_DIR = "data/legal_docs/bf"
SCANNED_SAMPLE_DOCS = 8
SCANNED_SAMPLE_PAGES = 3


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _add_text(counter: Counter[str], text: str) -> None:
    t = _norm(text)
    counter.update(t[i : i + 3] for i in range(len(t) - 2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-count", type=int, default=2)
    args = parser.parse_args()

    import pymupdf  # local import: only needed when (re)building

    counter: Counter[str] = Counter()

    for pattern in CLEAN_DOC_GLOBS:
        matches = sorted((ROOT / "data/legal_docs/to_ingest").glob(pattern))
        if not matches:
            print(f"skip (no match): {pattern}", file=sys.stderr)
            continue
        path = matches[0]
        doc = pymupdf.open(path)
        _add_text(counter, " ".join(page.get_text() for page in doc))
        print(f"clean text: {path.name[:60]}")

    scanned = sorted((ROOT / SCANNED_SAMPLE_DIR).glob("*.pdf"))[:SCANNED_SAMPLE_DOCS]
    with tempfile.TemporaryDirectory(prefix="trigram_ocr_") as tmp:
        tmp_dir = Path(tmp)
        for path in scanned:
            doc = pymupdf.open(path)
            for i in range(1, min(1 + SCANNED_SAMPLE_PAGES, len(doc))):
                img = tmp_dir / f"{path.stem[:24]}_{i}.png"
                doc[i].get_pixmap(dpi=200).save(img)
                result = subprocess.run(
                    ["tesseract", str(img), "stdout", "-l", "fra"],
                    capture_output=True,
                    text=True,
                )
                _add_text(counter, result.stdout)
            print(f"ocr sample: {path.name[:60]}")

    grams = sorted(g for g, count in counter.items() if count >= args.min_count)
    OUT.write_text(json.dumps(grams, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(grams)} trigrams -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
