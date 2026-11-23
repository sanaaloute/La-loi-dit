#!/usr/bin/env python3
"""Merge downloaded legal documents by LEGAL IDENTITY, not just bytes.

The same instrument (e.g. Loi n°013-2026/ALP) can exist as:
  - a curated corpus file  (data/legal_docs/bf/…)
  - a LégiBurkina PDF      (downloads/legiburkina/…, structured reference)
  - an Assemblée scan      (downloads/assemblee/loi/…)
with different byte content (different scans/wrappers). Byte-level sha256
dedupe misses these; this script groups records by their legal reference.

Key extraction:
  - laws / ordonnances / codes : "NNN-YYYY" (number-first, e.g. 013-2026)
  - decrees / arrêtés          : "YYYY-NNNN" (year-first, e.g. 2026-1165)
  - the document type (manifest type or filename prefix) decides direction.

Precedence within a legal key (law text only):
    legiburkina  >  assemblee  >  corpus
Assemblée annexes (exposé, compte rendu, PV, rapports) are unique content:
always kept, never treated as duplicates of the law.

Rebuilds data/legal_docs/to_ingest/ with the merged set and writes
data/legal_docs/downloads/merge_report.json.

Usage:
    python scripts/merge_legal_docs.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DOWNLOADS = DATA_DIR / "legal_docs" / "downloads"
CORPUS_DIRS = ["bf", "cima", "ohada", "uemoa"]
STAGING_DIR = DATA_DIR / "legal_docs" / "to_ingest"
REPORT_PATH = DOWNLOADS / "merge_report.json"

# "013-2026", "11-95", "n° 020-2026", "001/97/ADP"
_NUM_YEAR_RE = re.compile(r"(\d{1,4})\s*[-–/]\s*(\d{2,4})(?!\d)")
# Legislature marker right after the number ("001/97/ADP", "013-2026_ALP").
# Transition years reuse the same law number under different legislatures
# (e.g. 001/97/AN vs 001/97/ADP are DIFFERENT laws) — the suffix is part
# of the legal identity.
_SUFFIX_RE = re.compile(r"[-–/_](AN|ADP|ALT|ALP|CNR|CT|PRES|MPSR)(?![A-Za-z])", re.IGNORECASE)
# "2026-1165", "1997-084"
_YEAR_NUM_RE = re.compile(r"(\d{4})\s*[-–/]\s*0*(\d{1,4})(?!\d)")

LAW_TYPES = {"LOI", "ORDONNANCE", "ZATU", "CONSTITUTION", "CHARTE"}
YEAR_FIRST_TYPES = {"DECRET", "ARRETE", "DECISION", "CIRCULAIRE", "KITI", "RAABO"}
ANNEX_ROLES = {"expose_motifs", "compte_rendu", "pv", "rapport_caeds", "rapport_cagidh",
               "rapport_comfib", "rapport_cdd", "rapport_cgsah", "libelrapportautres"}

PRECEDENCE = {"legiburkina": 0, "assemblee": 1, "corpus": 2}


def norm_year(y: str) -> str:
    if len(y) == 2:
        n = int(y)
        return f"19{y}" if n >= 60 else f"20{y}"
    return y


def legal_key(text: str, type_hint: str | None) -> str | None:
    """Normalized legal identity key, or None when nothing parses."""
    t = (type_hint or "").upper()
    suffix = _SUFFIX_RE.search(text)
    sfx = f":{suffix.group(1).lower()}" if suffix else ""
    if t in LAW_TYPES or t == "loi":
        m = _NUM_YEAR_RE.search(text)
        if m:
            return f"law:{int(m.group(1))}-{norm_year(m.group(2))}{sfx}"
    if t in YEAR_FIRST_TYPES:
        m = _YEAR_NUM_RE.search(text)
        if m:
            return f"reg:{m.group(1)}-{int(m.group(2))}"
    # No/unknown type: try both directions, prefer number-first (laws).
    m = _NUM_YEAR_RE.search(text)
    if m:
        return f"law:{int(m.group(1))}-{norm_year(m.group(2))}{sfx}"
    m = _YEAR_NUM_RE.search(text)
    if m:
        return f"reg:{m.group(1)}-{int(m.group(2))}"
    return None


def corpus_type_hint(filename: str) -> str | None:
    name = filename.lower()
    for hint in ("loi-organique", "loi", "ordonnance", "decret", "arrete",
                 "constitution", "code", "decision"):
        if hint in name:
            return {"loi-organique": "LOI", "code": "LOI"}.get(hint, hint.upper())
    return None


def collect_records() -> list[dict]:
    records: list[dict] = []

    # 1. curated corpus
    for sub in CORPUS_DIRS:
        d = DATA_DIR / "legal_docs" / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.pdf")):
            records.append({
                "source": "corpus",
                "corpus_dir": sub,
                "kind": corpus_type_hint(p.name) or "?",
                "title": p.stem,
                "ref": None,
                "path": p,
            })

    # 2. legiburkina manifest
    legi = json.loads((DOWNLOADS / "legiburkina" / "_legiburkina_manifest.json").read_text(encoding="utf-8"))
    for e in legi.get("documents", {}).values():
        if e.get("status") != "ok" or not e.get("local_path"):
            continue
        p = Path(e["local_path"])
        records.append({
            "source": "legiburkina",
            "kind": e.get("type") or "?",
            "title": e.get("description"),
            "ref": e.get("reference"),
            "date": e.get("date"),
            "path": (REPO_ROOT / p) if not p.is_absolute() else p,
        })

    # 3. assemblee manifest
    ass = json.loads((DOWNLOADS / "assemblee" / "_assemblee_manifest.json").read_text(encoding="utf-8"))
    for det in ass.get("details", {}).values():
        for role, f in (det.get("files") or {}).items():
            if f.get("status") != "ok" or not f.get("local_path"):
                continue
            p = Path(f["local_path"])
            records.append({
                "source": "assemblee",
                "kind": role,
                "title": det.get("title"),
                "ref": det.get("id"),
                "date": det.get("date"),
                "path": (REPO_ROOT / p) if not p.is_absolute() else p,
            })
    return records


def main() -> int:
    records = collect_records()
    print(f"[collect] {len(records)} records "
          f"(corpus={sum(1 for r in records if r['source']=='corpus')}, "
          f"legiburkina={sum(1 for r in records if r['source']=='legiburkina')}, "
          f"assemblee={sum(1 for r in records if r['source']=='assemblee')})")

    # Group by legal identity; annexes get their own unique key.
    groups: dict[str, list[dict]] = {}
    for r in records:
        if r["source"] == "assemblee" and r["kind"] in ANNEX_ROLES:
            key = f"annex:{r['ref']}:{r['kind']}"
        else:
            key = legal_key(f"{r.get('ref') or ''} {r.get('title') or ''}", r["kind"])
            if key is None:
                key = f"raw:{r['source']}:{r['path'].name}"
        r["key"] = key
        groups.setdefault(key, []).append(r)

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True)

    report = {"multi_source_keys": [], "stats": {}}
    n_keep = n_dropped = n_multi = 0
    for key, members in sorted(groups.items()):
        members.sort(key=lambda r: PRECEDENCE[r["source"]])
        keeper = members[0]
        dropped = members[1:]
        sources = {m["source"] for m in members}
        if len(members) > 1:
            n_dropped += len(dropped)
            if len(sources) > 1:
                n_multi += 1
                report["multi_source_keys"].append({
                    "key": key,
                    "keeper": {"source": keeper["source"], "title": keeper["title"],
                               "path": str(keeper["path"])},
                    "dropped": [{"source": m["source"], "title": m["title"],
                                 "path": str(m["path"])} for m in dropped],
                })
        safe = re.sub(r"[^\w.\-()]+", "_", keeper["path"].name)[:110]
        dest = STAGING_DIR / f"{keeper['source']}_{safe}"
        counter = 2
        while dest.exists():
            dest = STAGING_DIR / f"{dest.stem[:100]}_{counter}.pdf"
            counter += 1
        try:
            dest.hardlink_to(keeper["path"])
            n_keep += 1
        except OSError as exc:
            print(f"[warn] hardlink failed for {keeper['path']}: {exc}")

    report["stats"] = {
        "records": len(records),
        "legal_keys": len(groups),
        "staged": n_keep,
        "dropped_as_duplicates": n_dropped,
        "keys_with_multiple_sources": n_multi,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"[done] {len(records)} records -> {len(groups)} legal identities")
    print(f"  staged for ingestion         : {n_keep}")
    print(f"  dropped as duplicates        : {n_dropped}")
    print(f"  keys spanning >1 source      : {n_multi}")
    print(f"  report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
