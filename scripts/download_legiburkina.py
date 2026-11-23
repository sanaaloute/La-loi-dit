#!/usr/bin/env python3
"""Bulk downloader for legiburkina.gov.bf public documents.

The site is a Laravel + Inertia app: every page embeds its props as JSON in a
`data-page` attribute, so plain HTTP + JSON parsing is enough — no browser.

Listing:  GET /documents-publics?page=N   (10 docs/page, last_page in props)
Download: GET /documents/{id}/visualiser  (streams the PDF, no login)

Resumable: a JSON manifest is written incrementally; ids marked "ok" whose
file is still on disk are skipped on re-run.

Usage:
    python scripts/download_legiburkina.py [--start-page 1] [--end-page N]
                                           [--sleep 2.0] [--limit K]
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_URL = "https://legiburkina.gov.bf"
LIST_URL = f"{BASE_URL}/documents-publics?page={{page}}"
VIEW_URL = f"{BASE_URL}/documents/{{doc_id}}/visualiser"
OUT_DIR = Path("data/legal_docs/downloads/legiburkina")
MANIFEST_PATH = OUT_DIR / "_legiburkina_manifest.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_DATA_PAGE_RE = re.compile(r'data-page="([^"]+)"')


def curl_get(url: str) -> bytes:
    """Fallback fetch for hosts whose TLS stack rejects Python's handshake."""
    result = subprocess.run(
        [
            "curl", "-sS", "-f", "-L", "-m", "120",
            "--tlsv1.2", "--http1.1",
            "-A", USER_AGENT,
            url,
        ],
        capture_output=True,
        timeout=150,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed ({result.returncode}): {result.stderr.decode(errors='ignore')[:200]}")
    return result.stdout


def fetch(url: str, retries: int = 3, binary: bool = False) -> bytes:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last_exc = e
        except ssl.SSLError:
            try:
                return curl_get(url)
            except Exception as e:
                last_exc = e
        except Exception as e:
            last_exc = e
        time.sleep(2 ** (attempt + 1))
    raise last_exc or RuntimeError(f"failed to fetch {url}")


def fetch_page_props(url: str, retries: int = 3) -> dict:
    raw = fetch(url, retries=retries)
    text = raw.decode("utf-8", errors="ignore")
    m = _DATA_PAGE_RE.search(text)
    if not m:
        raise RuntimeError(f"no data-page JSON at {url}")
    return json.loads(html_mod.unescape(m.group(1)))


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
        return {"documents": {}}


def save_manifest(manifest: dict) -> None:
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=None, help="stop after K downloads (smoke test)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    docs_db: dict = manifest.setdefault("documents", {})
    done_ids = {
        doc_id for doc_id, e in docs_db.items()
        if e.get("status") == "ok" and e.get("local_path") and Path(e["local_path"]).exists()
    }
    print(f"[init] manifest has {len(docs_db)} entries, {len(done_ids)} already downloaded")

    # Page 1 gives us last_page so the run sizes itself.
    props = fetch_page_props(LIST_URL.format(page=args.start_page))
    documents = props["props"]["documents"]
    last_page = documents["last_page"]
    end_page = min(args.end_page or last_page, last_page)
    print(f"[list] total={documents['total']} last_page={last_page} -> pages {args.start_page}..{end_page}")

    downloaded = 0
    page_props = props

    for page in range(args.start_page, end_page + 1):
        if page != args.start_page:
            time.sleep(args.sleep)
            try:
                page_props = fetch_page_props(LIST_URL.format(page=page))
            except Exception as exc:
                print(f"[error] listing page {page}: {exc}")
                continue
        items = page_props["props"]["documents"]["data"]
        print(f"[list] page {page}/{end_page}: {len(items)} documents")

        for item in items:
            doc_id = item["id"]
            if doc_id in done_ids:
                continue
            type_info = item.get("type") or {}
            type_dir = re.sub(r"[^A-Za-z0-9_-]+", "_", type_info.get("libelle") or "AUTRE")
            target_dir = OUT_DIR / type_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            file_name = item.get("file_name") or f"{doc_id}.pdf"
            if not file_name.lower().endswith(".pdf"):
                file_name += ".pdf"
            out_path = unique_path(target_dir, file_name)

            url = VIEW_URL.format(doc_id=doc_id)
            entry = {
                "id": doc_id,
                "reference": item.get("reference"),
                "description": item.get("description"),
                "date": item.get("date"),
                "type": type_info.get("libelle"),
                "categorie": type_info.get("categorie"),
                "file_name": file_name,
                "file_size": item.get("file_size"),
                "source_url": url,
                "listing_page": page,
            }
            time.sleep(args.sleep)
            try:
                blob = fetch(url)
                out_path.write_bytes(blob)
            except Exception as exc:
                print(f"  [error] {file_name[:60]}: {exc}")
                entry.update(status="error", error=str(exc)[:300])
                docs_db[doc_id] = entry
                save_manifest(manifest)
                continue

            status = "ok"
            if not blob.startswith(b"%PDF"):
                status = "invalid"
            entry.update(
                status=status,
                local_path=str(out_path),
                local_filename=out_path.name,
                sha256=sha256_of(out_path),
                file_size_downloaded=len(blob),
                downloaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            docs_db[doc_id] = entry
            save_manifest(manifest)
            done_ids.add(doc_id)
            downloaded += 1
            print(f"  [ok] {type_dir}/{out_path.name} ({len(blob)} bytes)"
                  + (" WARNING not-a-pdf" if status == "invalid" else ""))

            if args.limit and downloaded >= args.limit:
                print(f"[done] limit reached: downloaded={downloaded}")
                return 0

    print(f"[done] downloaded={downloaded} manifest_total={len(docs_db)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
