#!/usr/bin/env python3
"""Create ingestion_results.json entries for documents known in versions.json
but missing from ingestion_results.json, using the document title map.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES_PATH = Path("data/legal_sources.json")
RESULTS_PATH = Path("data/ingestion_results.json")
VERSIONS_PATH = Path("data/versions.json")


def document_id(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    titles = json.loads(SOURCES_PATH.read_text(encoding="utf-8")).get("document_titles", {})
    if RESULTS_PATH.exists():
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    else:
        results = {}
    versions = json.loads(VERSIONS_PATH.read_text(encoding="utf-8")) if VERSIONS_PATH.exists() else {}

    # Build reverse map: document_id -> (filename, title)
    reverse_map: dict[str, tuple[str, str]] = {}
    for filename, title in titles.items():
        did = document_id(filename)
        reverse_map[did] = (filename, title)

    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for did, entry in versions.items():
        if did in results:
            continue
        if did not in reverse_map:
            continue
        filename, title = reverse_map[did]
        results[did] = {
            "document_id": did,
            "document_name": title,
            "status": "skipped_duplicate",
            "version": entry.get("version", 1),
            "chunks_created": 0,
            "timestamp": now,
            "path": str(Path("data/legal_docs/new_docs") / filename),
        }
        added += 1

    if added:
        RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Added {added} missing ingestion_results entries")
    else:
        print("No missing ingestion_results entries to add")


if __name__ == "__main__":
    main()
