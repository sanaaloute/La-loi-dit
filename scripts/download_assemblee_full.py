#!/usr/bin/env python3
"""Full, unfiltered sweep of assembleenationale.bf "lois promulguees".

Unlike scripts/download_assemblee.py (priority-keyword filtered, law PDF only),
this downloads EVERY document attached to each law detail page:

    KITS table       : loi, expose des motifs, compte rendu analytique, PV
    Autres documents : rapports de commission (CAEDS, CAGIDH, COMFIB, CDD, CGSAH, ...)

Reuses fetch/parse_french_date/safe_filename from scripts/download_assemblee.py.

Resumable: a JSON manifest is written incrementally; (detail_id, role) pairs
marked "ok" whose file is still on disk are skipped on re-run.

Usage:
    python scripts/download_assemblee_full.py [--pages N] [--sleep 2.0] [--limit K]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_assemblee import (  # noqa: E402
    BASE_URL,
    LIST_URL,
    USER_AGENT,
    fetch,
    parse_french_date,
    safe_filename,
)


def fetch_binary(url: str, retries: int = 3) -> bytes:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            time.sleep(2 ** (attempt + 1))
    raise last_exc or RuntimeError(f"failed to fetch {url}")

OUT_DIR = Path("data/legal_docs/downloads/assemblee")
MANIFEST_PATH = OUT_DIR / "_assemblee_manifest.json"

_DETAIL_RE = re.compile(r'href="([^"]*\/loip\/(\d+))"')
_PAGE_NUM_RE = re.compile(r'/loip\?page=(\d+)')
_TITLE_RE = re.compile(
    r'<div[^>]*class="[^"]*rounded-lg[^"]*bg-red-600[^"]*"[^>]*>\s*Intitulé de la loi\s*:\s*(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_ANCHOR_RE = re.compile(
    r'<a\s+[^>]*href="(/storage/[^"]+\.pdf)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)

_ROLE_RULES = [
    (r"Télécharger la Loi", "loi"),
    (r"exposé des motifs", "expose_motifs"),
    (r"Compte rendu analytique", "compte_rendu"),
    (r"\bPV\b", "pv"),
    (r"Rapport\s+CAEDS", "rapport_caeds"),
    (r"Rapport\s+CAGIDH", "rapport_cagidh"),
    (r"Rapport\s+COMFIB", "rapport_comfib"),
    (r"Rapport\s+CDD", "rapport_cdd"),
    (r"Rapport\s+CGSAH", "rapport_cgsah"),
]


def role_of(label: str, pdf_path: str) -> str:
    for pattern, role in _ROLE_RULES:
        if re.search(pattern, label, re.IGNORECASE):
            return role
    m = re.search(r"/storage/([^/]+)/", pdf_path)
    folder = m.group(1) if m else "misc"
    return re.sub(r"[^a-z0-9]+", "_", folder.lower()).strip("_") or "misc"


def detect_last_page() -> int:
    html = fetch(LIST_URL.format(page=1))
    pages = [int(m.group(1)) for m in _PAGE_NUM_RE.finditer(html)]
    return max(pages) if pages else 1


def parse_detail_full(lid: str) -> Optional[dict]:
    url = f"{BASE_URL}/loip/{lid}"
    html = fetch(url)

    title = ""
    m = _TITLE_RE.search(html)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        title = re.sub(r"\s+", " ", title)

    documents: dict[str, str] = {}
    for am in _ANCHOR_RE.finditer(html):
        pdf_path, label_html = am.groups()
        label = re.sub(r"<[^>]+>", " ", label_html)
        label = re.sub(r"\s+", " ", label).strip()
        role = role_of(label, pdf_path)
        if role in documents:
            base = role
            n = 2
            while f"{base}_{n}" in documents:
                n += 1
            role = f"{base}_{n}"
        documents[role] = urljoin(BASE_URL, pdf_path)

    date_tuple = parse_french_date(title)
    return {
        "id": lid,
        "title": title,
        "date": "-".join(str(x) for x in date_tuple) if date_tuple else None,
        "source_url": url,
        "documents": documents,
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_path(directory: Path, filename: str) -> Path:
    out = directory / filename
    if not out.exists():
        return out
    stem, suffix = out.stem, out.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"details": {}}


def save_manifest(manifest: dict) -> None:
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=None, help="listing pages to scan (default: auto-detect)")
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=None, help="stop after K downloads (smoke test)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    details_db: dict = manifest.setdefault("details", {})

    max_page = args.pages or detect_last_page()
    print(f"[list] scanning {max_page} listing pages on {BASE_URL}")

    downloaded = 0
    for page in range(1, max_page + 1):
        url = LIST_URL.format(page=page)
        try:
            html = fetch(url)
        except Exception as exc:
            print(f"[error] listing page {page}: {exc}")
            continue
        ids: list[str] = []
        for m in _DETAIL_RE.finditer(html):
            lid = m.group(2)
            if lid not in ids:
                ids.append(lid)
        print(f"[list] page {page}/{max_page}: {len(ids)} detail pages")
        time.sleep(args.sleep)

        for lid in ids:
            try:
                info = parse_detail_full(lid)
            except Exception as exc:
                print(f"  [error] detail {lid}: {exc}")
                continue
            if not info["documents"]:
                details_db.setdefault(lid, {
                    "id": lid, "title": info["title"], "source_url": info["source_url"],
                    "status": "no_documents",
                })
                save_manifest(manifest)
                continue

            entry = details_db.setdefault(lid, {
                "id": lid,
                "title": info["title"],
                "date": info["date"],
                "source_url": info["source_url"],
                "status": "ok",
                "files": {},
            })
            entry.setdefault("title", info["title"])
            entry.setdefault("date", info["date"])
            files_db: dict = entry.setdefault("files", {})

            for role, pdf_url in info["documents"].items():
                existing = files_db.get(role)
                if existing and existing.get("status") == "ok" and existing.get("local_path") \
                        and Path(existing["local_path"]).exists():
                    continue

                role_dir = OUT_DIR / role
                role_dir.mkdir(parents=True, exist_ok=True)
                base = safe_filename(info["title"])[:80] or f"loi_{lid}"
                filename = f"assemblee_{lid}_{role}_{base}.pdf"
                out_path = unique_path(role_dir, filename)

                try:
                    blob = fetch_binary(pdf_url)
                    out_path.write_bytes(blob)
                except Exception as exc:
                    print(f"  [error] {role} detail {lid}: {exc}")
                    files_db[role] = {"pdf_url": pdf_url, "status": "error", "error": str(exc)[:300]}
                    save_manifest(manifest)
                    time.sleep(args.sleep)
                    continue

                status = "ok" if blob.startswith(b"%PDF") else "invalid"
                files_db[role] = {
                    "pdf_url": pdf_url,
                    "status": status,
                    "local_path": str(out_path),
                    "local_filename": out_path.name,
                    "sha256": sha256_of(out_path),
                    "file_size": len(blob),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                save_manifest(manifest)
                downloaded += 1
                print(f"  [ok] {role}/{out_path.name} ({len(blob)} bytes)"
                      + (" WARNING not-a-pdf" if status == "invalid" else ""))

                if args.limit and downloaded >= args.limit:
                    print(f"[done] limit reached: downloaded={downloaded}")
                    return 0
                time.sleep(args.sleep)

    print(f"[done] downloaded={downloaded} details_total={len(details_db)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
