#!/usr/bin/env python3
"""Dedupe freshly downloaded legal documents before ingestion.

Sources:
  - data/legal_docs/downloads/legiburkina/_legiburkina_manifest.json
  - data/legal_docs/downloads/assemblee/_assemblee_manifest.json

Both manifests already carry a sha256 per file. This script:
  1. hashes the existing curated corpus (data/legal_docs/{bf,cima,ohada,uemoa}),
  2. groups downloaded files by sha256,
  3. classifies each group: unique | duplicate_corpus | duplicate_internal,
  4. hardlinks one representative per unique hash into data/legal_docs/to_ingest/,
  5. writes data/legal_docs/downloads/dedupe_report.json.

Usage:
    python scripts/dedupe_downloads.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DOWNLOADS = DATA_DIR / "legal_docs" / "downloads"
CORPUS_DIRS = ["bf", "cima", "ohada", "uemoa"]
STAGING_DIR = DATA_DIR / "legal_docs" / "to_ingest"
REPORT_PATH = DOWNLOADS / "dedupe_report.json"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_downloads() -> list[dict]:
    """Flatten both manifests into per-file records."""
    records: list[dict] = []

    legi = json.loads((DOWNLOADS / "legiburkina" / "_legiburkina_manifest.json").read_text(encoding="utf-8"))
    for e in legi.get("documents", {}).values():
        if e.get("status") != "ok" or not e.get("local_path"):
            continue
        records.append({
            "source": "legiburkina",
            "ref": e.get("reference"),
            "title": e.get("description"),
            "date": e.get("date"),
            "kind": e.get("type"),
            "sha256": e.get("sha256"),
            "local_path": e["local_path"],
            "source_url": e.get("source_url"),
        })

    ass = json.loads((DOWNLOADS / "assemblee" / "_assemblee_manifest.json").read_text(encoding="utf-8"))
    for det in ass.get("details", {}).values():
        for role, f in (det.get("files") or {}).items():
            if f.get("status") != "ok" or not f.get("local_path"):
                continue
            records.append({
                "source": "assemblee",
                "ref": det.get("id"),
                "title": det.get("title"),
                "date": det.get("date"),
                "kind": role,
                "sha256": f.get("sha256"),
                "local_path": f["local_path"],
                "source_url": f.get("pdf_url"),
            })
    return records


def known_corpus_hashes() -> dict[str, str]:
    """sha256 -> path for every file already in the curated corpus."""
    known: dict[str, str] = {}
    for sub in CORPUS_DIRS:
        d = DATA_DIR / "legal_docs" / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*.pdf"):
            try:
                known.setdefault(sha256_of(p), str(p))
            except OSError as exc:
                print(f"[warn] cannot hash {p}: {exc}")
    return known


def main() -> int:
    records = collect_downloads()
    print(f"[collect] {len(records)} downloaded files with ok status")

    corpus = known_corpus_hashes()
    print(f"[corpus] hashed {len(corpus)} existing curated files")

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict]] = {}
    for r in records:
        if r["sha256"]:
            groups.setdefault(r["sha256"], []).append(r)

    report = {"groups": [], "stats": {}}
    n_unique = n_dup_corpus = n_dup_internal = n_staged = 0

    for digest, members in sorted(groups.items()):
        if digest in corpus:
            n_dup_corpus += len(members)
            report["groups"].append({
                "sha256": digest, "verdict": "duplicate_corpus",
                "corpus_path": corpus[digest], "members": members,
            })
            continue

        keeper = members[0]
        verdict = "unique" if len(members) == 1 else "duplicate_internal"
        if len(members) > 1:
            n_dup_internal += len(members) - 1
            n_unique += 1
        else:
            n_unique += 1

        # Stage the keeper as a hardlink with a collision-free name.
        src = Path(keeper["local_path"])
        if not src.is_absolute():
            src = REPO_ROOT / src
        safe = re.sub(r"[^\w.\-()]+", "_", src.name)[:110]
        link_name = f"{keeper['source']}_{keeper['kind']}_{safe}"
        dest = STAGING_DIR / link_name
        counter = 2
        while dest.exists():
            dest = STAGING_DIR / f"{dest.stem[:100]}_{counter}.pdf"
            counter += 1
        try:
            dest.hardlink_to(src)
            n_staged += 1
        except OSError as exc:
            print(f"[warn] hardlink failed for {src}: {exc}")

        entry = {"sha256": digest, "verdict": verdict, "keeper": keeper,
                 "staged_as": dest.name, "duplicates": members[1:]}
        report["groups"].append(entry)

    report["stats"] = {
        "files_total": len(records),
        "unique_hashes": len(groups),
        "staged": n_staged,
        "duplicates_vs_corpus": n_dup_corpus,
        "duplicates_internal": n_dup_internal,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"[done] {len(records)} files -> {len(groups)} unique contents")
    print(f"  staged for ingestion : {n_staged}")
    print(f"  duplicates of corpus : {n_dup_corpus}")
    print(f"  internal duplicates  : {n_dup_internal}")
    print(f"  report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
