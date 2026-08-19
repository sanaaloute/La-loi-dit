#!/usr/bin/env python3
"""Update data/legal_sources.json document_titles and backfill ingestion_results.json.

Resolution order for a filename:
1. Assemblée nationale metadata title (if available).
2. Existing document_titles entry in legal_sources.json.
3. Filename-derived heuristic title.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SOURCES_PATH = Path("data/legal_sources.json")
META_PATH = Path("data/legal_docs/new_docs/_assemblee_metadata.json")
RESULTS_PATH = Path("data/ingestion_results.json")
LEGAL_DOCS_DIR = Path("data/legal_docs")


def clean_title(title: str) -> str:
    return " ".join(title.replace("\r", " ").replace("\n", " ").split())


def normalize_filename(name: str) -> str:
    # Strip extension and replace underscores/hyphens with spaces
    base = Path(name).stem
    return base


def title_from_filename(filename: str) -> str | None:
    """Best-effort readable title from a burkina-faso_... or assemblee_... filename."""
    base = Path(filename).stem

    # Assemblée nationale files we may have renamed
    if base.startswith("assemblee_"):
        # Try to parse: assemblee_Loi_n042-2024ALT_du_23_décembre_2024_...
        m = re.search(r"assemblee_(Loi(?:_constitutionnelle)?)_n?(\d{2,3}-\d{4})(?:[A-Z]+)?(?:_du_(\d+)_(\w+)_\d{4})?_(.+)", base, re.IGNORECASE)
        if m:
            kind, num, day, month, rest = m.groups()
            rest_clean = rest.replace("_", " ")
            date_part = f" du {day} {month} {m.group(0)[-4:]}" if day else ""
            return clean_title(f"{kind} n°{num}{date_part} — {rest_clean}")
        return clean_title(base.replace("_", " ").replace("assemblee ", ""))

    # burkina-faso_TYPE_DOMAIN_YEAR_NUMBER_SHORTTITLE or similar variations
    parts = base.split("_")
    if len(parts) >= 4 and parts[0] == "burkina-faso":
        # Find the law number token (e.g. 003-2020-an, 016-2024-alt, 028-2008-an)
        law_num_idx = None
        for i, p in enumerate(parts):
            if re.fullmatch(r"\d{2,3}-\d{4}-[a-zA-Z]+", p):
                law_num_idx = i
                break

        if law_num_idx is not None:
            number = parts[law_num_idx]
            # Year is usually the 4-digit token just before the law number
            year = ""
            if law_num_idx > 2:
                candidate = parts[law_num_idx - 1]
                if re.fullmatch(r"\d{4}", candidate):
                    year = candidate

            type_label = "Loi"
            if "code" in base.lower():
                type_label = "Code"
            elif "decret" in base.lower():
                type_label = "Décret"
            elif "ordonnance" in base.lower():
                type_label = "Ordonnance"
            elif "arrete" in base.lower():
                type_label = "Arrêté"

            short = " ".join(parts[law_num_idx + 1 :])
            short_clean = short.replace("-", " ").strip()
            parts_out = [f"{type_label} n°{number}"]
            if short_clean:
                parts_out.append(f"— {short_clean}")
            if year:
                parts_out.append(f"({year})")
            return clean_title(" ".join(parts_out))

        # Fallback: just humanize the filename
        return clean_title(base.replace("burkina-faso ", "").replace("_", " ").replace("-", " "))

    return None


def main() -> None:
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    # Rebuild titles: keep only real titles from the existing map, not raw filenames
    titles: dict[str, str] = {}
    for fn, title in (sources.get("document_titles") or {}).items():
        if title and title != fn and not title.endswith(".pdf") and "_" not in title:
            titles[fn] = clean_title(title)

    # 1. Assemblée nationale metadata titles
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        for item in meta.get("selected", []):
            filename = item.get("local_filename")
            title = item.get("title")
            if filename and title:
                titles[filename] = clean_title(title)

    # 2. Existing ingestion_results.json names, but only if they already look
    #    like a real title (not a raw filename).
    if RESULTS_PATH.exists():
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        for record in results.values():
            path = record.get("path")
            name = record.get("document_name")
            if path and name:
                filename = Path(path).name
                if filename not in titles and not name.endswith(".pdf") and "_" not in name:
                    titles[filename] = clean_title(name)

    # 3. Heuristic titles for any PDF under legal_docs not yet mapped
    for pdf in sorted(LEGAL_DOCS_DIR.rglob("*.pdf")):
        if pdf.name in titles:
            continue
        generated = title_from_filename(pdf.name)
        if generated:
            titles[pdf.name] = generated

    # Update sources
    sources["document_titles"] = dict(sorted(titles.items()))
    SOURCES_PATH.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated {SOURCES_PATH} with {len(titles)} document titles")

    # Backfill ingestion_results.json with display titles
    if RESULTS_PATH.exists():
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        updated = 0
        for record in results.values():
            path = record.get("path")
            if not path:
                continue
            filename = Path(path).name
            display = titles.get(filename)
            if display and record.get("document_name") != display:
                record["document_name"] = display
                updated += 1
        if updated:
            RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"Updated {updated} ingestion_results entries with display titles")
        else:
            print("No ingestion_results entries needed updating")


if __name__ == "__main__":
    main()
