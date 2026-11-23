#!/usr/bin/env python3
"""Repair files in data/legal_docs/to_ingest whose extension was truncated by
the crawler (`.pd`, `.p`, trailing dot, garbage suffix or none at all).

Every file with an unsupported suffix is checked for the `%PDF-` magic; PDFs
are renamed to `<stem>.pdf`. Collisions are resolved by content hash:
identical files are deleted as duplicates, different files get a `_2`, `_3`…
suffix. Renaming is safe because only files with an unsupported suffix are
touched, and the ingestion pipeline never globbed those — they have no entry
in the version registry (document_id is a hash of the file name).

Usage:
    python scripts/repair_truncated_extensions.py            # dry-run
    python scripts/repair_truncated_extensions.py --apply    # execute
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "legal_docs" / "to_ingest"

SUPPORTED = {".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".markdown", ".csv"}
PDF_MAGIC = b"%PDF-"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def target_name(path: Path) -> str:
    stem = path.stem if path.suffix else path.name
    return f"{stem.rstrip('.')}.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the renames (default: dry-run)")
    args = parser.parse_args()

    if not TARGET.is_dir():
        print(f"Not found: {TARGET}", file=sys.stderr)
        return 1

    renamed = deleted_dupes = skipped_not_pdf = 0
    for path in sorted(TARGET.iterdir()):
        if not path.is_file() or path.suffix.lower() in SUPPORTED:
            continue
        with path.open("rb") as fh:
            if fh.read(5) != PDF_MAGIC:
                skipped_not_pdf += 1
                print(f"SKIP (not a PDF): {path.name}")
                continue
        target = path.with_name(target_name(path))
        if target == path:
            continue
        if target.exists():
            base = target.stem
            n = 2
            while target.exists() and sha256_of(target) != sha256_of(path):
                target = path.with_name(f"{base}_{n}.pdf")
                n += 1
            if target.exists():
                deleted_dupes += 1
                print(f"DUPLICATE of {target.name}: {path.name}")
                if args.apply:
                    path.unlink()
                continue
        renamed += 1
        print(f"RENAME: {path.name} -> {target.name}")
        if args.apply:
            path.rename(target)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] renamed={renamed} deleted_dupes={deleted_dupes} skipped_not_pdf={skipped_not_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
