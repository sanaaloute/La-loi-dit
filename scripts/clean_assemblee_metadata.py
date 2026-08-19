#!/usr/bin/env python3
"""Normalize the mixed _assemblee_metadata.json file.

* Keeps only the rich "selected" list from the original scraper.
* Removes annual budget/finance execution laws.
* Checks which downloaded files actually exist.
* Re-saves clean metadata.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

META_PATH = Path("data/legal_docs/new_docs/_assemblee_metadata.json")
NEW_DOCS = Path("data/legal_docs/new_docs")


def is_budget_law(title: str) -> bool:
    low = title.lower()
    return (
        ("loi de finances" in low and "exécution du budget" in low)
        or "loi de règlement" in low
        or "règlement au titre du budget" in low
        or "budget de l'etat" in low
        or "budget de l'état" in low
    )


# Normalize common duplicate subjects so we can keep only the latest version.
DUPLICATE_SUBJECTS = {
    "code electoral": ["code electoral", "code électoral", "code électorale"],
    "code minier": ["code minier"],
    "constitution": ["constitution", "revision de la constitution", "révision de la constitution"],
    "code penal": ["code penal", "code pénal"],
    "code procedure penale": ["code de procedure penale", "code de procédure pénale"],
    "code travail": ["code du travail"],
    "code personnes famille": ["code des personnes", "code de la famille"],
}


def duplicate_subject(title: str) -> str | None:
    low = title.lower()
    for key, patterns in DUPLICATE_SUBJECTS.items():
        if any(p in low for p in patterns):
            return key
    return None


def main() -> None:
    raw = json.loads(META_PATH.read_text(encoding="utf-8"))

    selected = raw.get("selected", [])
    if not selected:
        raise SystemExit("No 'selected' list found in metadata")

    # Pass 1: remove budget laws and mark file existence
    cleaned = []
    removed_budget = 0
    for item in selected:
        title = item.get("title", "")
        if is_budget_law(title):
            removed_budget += 1
            continue

        local_path = item.get("local_path")
        if local_path:
            full = Path(local_path)
            if not full.is_absolute():
                full = NEW_DOCS / full.name
            exists = full.exists()
        else:
            exists = False
        item["file_exists"] = exists
        cleaned.append(item)

    # Pass 2: for known duplicate subjects, keep only the latest year/number
    # (e.g. several versions of the electoral code).
    def _sort_key(item: dict) -> tuple:
        year = str(item.get("year", ""))
        num = re.search(r"n°\s*(\d+[-/]\d+)", item.get("title", ""), re.IGNORECASE)
        num_str = num.group(1) if num else ""
        # Prefer existing files over missing ones when years tie
        return (year, num_str, item.get("file_exists", False))

    kept_by_subject: dict[str, dict] = {}
    for item in cleaned:
        subj = duplicate_subject(item.get("title", ""))
        if subj is None:
            continue
        current = kept_by_subject.get(subj)
        if current is None or _sort_key(item) > _sort_key(current):
            kept_by_subject[subj] = item

    deduped_ids = {id(item) for item in kept_by_subject.values()}
    final = []
    removed_duplicates = 0
    for item in cleaned:
        subj = duplicate_subject(item.get("title", ""))
        if subj is not None and id(item) not in deduped_ids:
            removed_duplicates += 1
            continue
        final.append(item)

    missing_files = sum(1 for x in final if not x.get("file_exists"))

    # Sort by year desc, then importance desc
    final.sort(key=lambda x: (x.get("year", ""), x.get("importance_score", 0)), reverse=True)

    new_meta = {
        "total_found": raw.get("total_found", len(selected) + raw.get("no_pdf_count", 0)),
        "selected_count": len(final),
        "downloaded_count": sum(1 for x in final if x["file_exists"]),
        "missing_count": missing_files,
        "removed_budget_laws": removed_budget,
        "removed_duplicate_laws": removed_duplicates,
        "selected": final,
    }

    META_PATH.write_text(json.dumps(new_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Cleaned metadata: selected={new_meta['selected_count']} "
        f"existing={new_meta['downloaded_count']} missing={missing_files} "
        f"removed_budget={removed_budget} removed_duplicates={removed_duplicates}"
    )


if __name__ == "__main__":
    main()
