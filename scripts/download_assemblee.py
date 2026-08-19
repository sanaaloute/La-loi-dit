#!/usr/bin/env python3
"""Download "lois promulguées" from assembleenationale.bf pages 1..14.

Keeps only the actual law text PDFs (under /storage/Loi/), skips reports
and supporting documents.  If the same law number appears on multiple
pages, the script keeps the most recent date it can parse.

Usage:
    python scripts/download_assemblee.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

BASE_URL = "https://www.assembleenationale.bf"
LIST_URL = f"{BASE_URL}/loip?page={{page}}"
OUT_DIR = Path("data/legal_docs/new_docs")
META_PATH = OUT_DIR / "_assemblee_metadata.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Sectors we are most interested in for the legal assistant.
# Laws whose titles contain these keywords are flagged as priority.
PRIORITY_KEYWORDS = [
    "constitution",
    "électoral",
    "élection",
    "pénal",
    "procédure",
    "justice",
    "travail",
    "foncier",
    "mines",
    "énergie",
    "environnement",
    "santé",
    "éducation",
    "finances",
    "budget",
    "douane",
    "fiscal",
    "investissement",
    "commerce",
    "numérique",
    "communication",
    "sécurité",
    "défense",
    "police",
    "armées",
    "protection",
    "expropriation",
    "indemnisation",
    "nationalité",
    "famille",
    "personne",
    "mariage",
    "successions",
    "données",
    "cyber",
    "médiation",
    "arbitrage",
    "assurance",
    "banque",
    "système financier",
]

DATE_RE = re.compile(r"(\d{1,2})\s+([a-zéûôêâç]+)\s+(\d{4})", re.IGNORECASE)
MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}


def fetch(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last_exc = e
        except Exception as e:
            last_exc = e
        time.sleep(2 ** attempt)
    raise last_exc or RuntimeError(f"failed to fetch {url}")


def parse_french_date(text: str) -> Optional[tuple[int, int, int]]:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, month_str, year = m.groups()
    month = MONTHS.get(month_str.lower())
    if not month:
        return None
    return (int(year), month, int(day))


def safe_filename(title: str) -> str:
    keep = re.sub(r"[^\w\s\-().]", "", title)
    keep = re.sub(r"\s+", "_", keep.strip())
    return keep[:120]


def list_detail_ids(max_page: int = 14) -> list[str]:
    ids: list[str] = []
    seen = set()
    for page in range(1, max_page + 1):
        url = LIST_URL.format(page=page)
        print(f"[list] page {page}: {url}")
        html = fetch(url)
        # Detail links look like href="https://www.assembleenationale.bf/loip/137"
        for m in re.finditer(r'href="([^"]*\/loip\/(\d+))"', html):
            full, lid = m.groups()
            if lid in seen:
                continue
            seen.add(lid)
            ids.append(lid)
        # Politeness
        time.sleep(0.5)
    return ids


def parse_detail(lid: str) -> Optional[dict]:
    url = f"{BASE_URL}/loip/{lid}"
    html = fetch(url)

    # Title block
    title_match = re.search(
        r'<div[^>]*class="[^"]*rounded-lg[^"]*bg-red-600[^"]*"[^>]*>\s*Intitulé de la loi\s*:\s*(.*?)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not title_match:
        return None
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
    title = re.sub(r"\s+", " ", title)

    # Find the law PDF link (the one under the "Loi" column in the KITS table)
    law_match = re.search(
        r'href="(/storage/Loi/[A-Za-z0-9]+\.pdf)"[^>]*>\s*Télécharger la Loi',
        html,
        re.IGNORECASE,
    )
    if not law_match:
        # Fallback: any link inside /storage/Loi/
        law_match = re.search(r'href="(/storage/Loi/[A-Za-z0-9]+\.pdf)"', html)
    if not law_match:
        return None
    pdf_path = law_match.group(1)
    pdf_url = urljoin(BASE_URL, pdf_path)

    date_tuple = parse_french_date(title)

    return {
        "id": lid,
        "title": title,
        "pdf_url": pdf_url,
        "date": "-".join(str(x) for x in date_tuple) if date_tuple else None,
        "source_url": url,
    }


def is_priority(title: str) -> bool:
    low = title.lower()
    return any(kw.lower() in low for kw in PRIORITY_KEYWORDS)


def is_annual_budget(title: str) -> bool:
    low = title.lower()
    return (
        "loi de finances" in low
        and "exécution du budget" in low
    ) or "budget de l'etat" in low or "budget de l'état" in low


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing metadata if any
    if META_PATH.exists():
        metadata: dict[str, dict] = json.loads(META_PATH.read_text(encoding="utf-8"))
    else:
        metadata = {}

    detail_ids = list_detail_ids(max_page=14)
    print(f"[list] found {len(detail_ids)} detail pages")

    new_count = 0
    skipped_count = 0
    for lid in detail_ids:
        if lid in metadata:
            skipped_count += 1
            continue
        info = parse_detail(lid)
        if not info:
            print(f"[skip] detail {lid}: no law PDF found")
            continue

        title = info["title"]
        date = info.get("date")
        if is_annual_budget(title):
            print(f"[skip] {title[:80]}... (annual budget)")
            continue
        if not is_priority(title):
            print(f"[skip] {title[:80]}... (not priority)")
            continue

        base = safe_filename(title) or f"loi_{lid}"
        filename = f"assemblee_{base}.pdf"
        out_path = OUT_DIR / filename

        # Avoid overwriting by appending a counter
        counter = 1
        original_out_path = out_path
        while out_path.exists():
            stem = original_out_path.stem
            out_path = OUT_DIR / f"{stem}_{counter}.pdf"
            counter += 1

        print(f"[download] {filename} <- {info['pdf_url']}")
        try:
            urllib.request.urlretrieve(
                info["pdf_url"],
                out_path,
                reporthook=None,
            )
        except Exception as e:
            print(f"[error] failed to download {info['pdf_url']}: {e}")
            continue

        # out_path is a relative Path under OUT_DIR; store a clean relative string
        info["local_path"] = str(out_path)
        info["local_filename"] = out_path.name
        info["file_size_bytes"] = out_path.stat().st_size
        metadata[lid] = info
        new_count += 1

        # Save metadata incrementally
        META_PATH.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        time.sleep(0.5)

    print(f"[done] new={new_count} skipped_existing={skipped_count} total={len(metadata)}")


if __name__ == "__main__":
    main()
