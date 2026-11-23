#!/usr/bin/env python3
"""Backfill `document_metadata` in data/legal_sources.json for the bulk
downloads in data/legal_docs/to_ingest (Assemblée nationale + LégiBurkina).

Filenames carry the type, reference and often the date of the text:

    assemblee_assemblee_1001_loi_Loi_n6095ADP_portant_modification_...pdf
    assemblee_assemblee_100_pv_Loi_n021-2024ALT_du_02_septembre_2024_...pdf
    legiburkina_decret_1976_313_pres_aet_..._ratification_traite_...pdf

For every file without an existing entry the script derives:
    document_name     human-readable title ("Loi n°60/95/ADP portant …")
    document_type     law | decree | ordinance | decision | parliamentary_record | other
    authority         law | decree | order | constitution | ""
    law_number        when a reference is unambiguous (Assemblée laws)
    publication_date  when the name embeds a full "du <day> <month> <year>" date
    government_body   Assemblée files only

Existing entries are never touched (idempotent). Dry-run by default.

Usage:
    python scripts/enrich_manifest_metadata.py            # dry-run
    python scripts/enrich_manifest_metadata.py --apply    # write manifest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "legal_docs" / "to_ingest"
MANIFEST = ROOT / "data" / "legal_sources.json"

MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8, "aot": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "décembre": 12, "dcembre": 12,
}
DATE_RE = re.compile(
    r"_du_(\d{1,2})_(" + "|".join(MONTHS) + r")_(\d{4})", re.IGNORECASE
)

# n6095ADP -> n°60/95/ADP ; n015-2004AN -> n°015-2004/AN ; n994ADP -> n°994/ADP
REF_SUFFIXES = "ADP|AN|ALT|CNR|PRES|FP"
REF4_RE = re.compile(rf"\bn(\d{{2}})(\d{{2}})({REF_SUFFIXES})\b")
REFDASH_RE = re.compile(rf"\bn(\d+)-(\d+)({REF_SUFFIXES})\b")
REFPLAIN_RE = re.compile(rf"\bn(\d+)({REF_SUFFIXES})\b")
REFCAPS_RE = re.compile(r"\bn(?=[A-Z]{2,}\b)")  # nAN -> n°AN

ASSEMBLEE_KINDS = {
    "loi": ("", "law", "law"),
    "pv": ("Procès-verbal", "parliamentary_record", ""),
    "compte": ("Compte rendu", "parliamentary_record", ""),
    "expose": ("Exposé des motifs", "parliamentary_record", ""),
    "rapport": ("Rapport", "parliamentary_record", ""),
    "libelrapportautres": ("Rapport", "parliamentary_record", ""),
}
# Title tokens to drop right after the kind (already conveyed by the prefix).
KIND_LEAD_TOKENS = {"compte": "rendu", "expose": "motifs"}

TRAILING_STOP = {"portant", "de", "la", "du", "des", "le", "les", "et", "d", "l"}

ASSEMBLEE_BODY = "Assemblée nationale du Burkina Faso"


def normalize_refs(text: str) -> str:
    text = REF4_RE.sub(r"n°\1/\2/\3", text)
    text = REFDASH_RE.sub(r"n°\1-\2/\3", text)
    text = REFPLAIN_RE.sub(r"n°\1/\2", text)
    return REFCAPS_RE.sub("n°", text)


def humanize(slug: str) -> str:
    text = normalize_refs(slug.replace("_", " "))
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    words = text.split()
    while words and words[-1].lower().rstrip(".") in TRAILING_STOP:
        words.pop()
    text = " ".join(words)
    return text[:1].upper() + text[1:] if text else text


def extract_date(slug: str) -> str:
    m = DATE_RE.search(slug)
    if not m:
        return ""
    day, month, year = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return ""


def parse_assemblee(stem: str) -> dict | None:
    parts = stem.split("_", 4)
    if len(parts) < 5:
        return None
    _, _, _id, kind, title = parts
    prefix, doc_type, authority = ASSEMBLEE_KINDS.get(kind, ("", "other", ""))
    if kind in KIND_LEAD_TOKENS and title.startswith(KIND_LEAD_TOKENS[kind] + "_"):
        title = title[len(KIND_LEAD_TOKENS[kind]) + 1:]
    if kind == "rapport":
        for sub in ("caeds", "cdd"):
            if title.startswith(sub + "_"):
                prefix = f"Rapport {sub.upper()}"
                title = title[len(sub) + 1:]
                break
    name = humanize(title)
    if prefix:
        name = f"{prefix} — {name}"
    meta = {
        "document_name": name,
        "document_type": doc_type,
        "government_body": ASSEMBLEE_BODY,
    }
    if authority:
        meta["authority"] = authority
    spaced = title.replace("_", " ")
    m = REF4_RE.search(spaced) or REFDASH_RE.search(spaced) or REFPLAIN_RE.search(spaced)
    if m and kind == "loi":
        meta["law_number"] = normalize_refs(m.group(0))[2:]
    date = extract_date(title)
    if date:
        meta["publication_date"] = date
    return meta


def legiburkina_type(norm: str) -> tuple[str, str]:
    if norm.startswith(("decision", "décision")):
        return "decision", ""
    if norm.startswith("d"):
        return "decree", "decree"
    if norm in ("loi", "lois", "kiti", "zatu"):
        return "law", "law"
    if norm.startswith("ordonnance"):
        return "ordinance", ""
    if norm.startswith("constitution"):
        return "", "constitution"
    if norm.startswith(("arrete", "arrêt")):
        return "", "order"
    return "other", ""


def parse_legiburkina(stem: str) -> dict:
    title = stem[len("legiburkina_"):]
    first = title.split("_", 1)[0]
    norm = re.sub(r"[0-9.\-]", "", first.lower())
    doc_type, authority = legiburkina_type(norm)
    meta = {"document_name": humanize(title), "document_type": doc_type}
    if authority:
        meta["authority"] = authority
    date = extract_date(title)
    if date:
        meta["publication_date"] = date
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the manifest (default: dry-run)")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doc_meta = manifest.setdefault("document_metadata", {})

    added = skipped = unparsed = 0
    for path in sorted(TARGET.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name in doc_meta:
            skipped += 1
            continue
        stem = path.stem
        if stem.startswith("assemblee_assemblee_"):
            meta = parse_assemblee(stem)
            if meta is None:
                unparsed += 1
                print(f"UNPARSED: {name}")
                continue
        elif stem.startswith("legiburkina_"):
            meta = parse_legiburkina(stem)
        else:
            unparsed += 1
            print(f"UNPARSED: {name}")
            continue
        added += 1
        if added <= 5:
            print(f"SAMPLE {name}\n  -> {json.dumps(meta, ensure_ascii=False)}")
        if args.apply:
            doc_meta[name] = meta

    if args.apply:
        MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] added={added} already_present={skipped} unparsed={unparsed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
