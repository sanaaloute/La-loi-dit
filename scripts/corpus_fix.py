#!/usr/bin/env python3
"""Corpus maintenance: backfill publication dates, drop duplicates, mark repeals.

Runs on the HOST against the repo data dir and the Milvus container
(127.0.0.1:19530, collection legal_chunks). Everything is a dry-run report by
default; pass --apply to write changes.

  python scripts/corpus_fix.py dates    [--apply]   # extract publication_date from first page / filename
  python scripts/corpus_fix.py dedupe   [--apply]   # byte-identical PDFs indexed twice
  python scripts/corpus_fix.py repeal   [--apply]   # older versions of the same text -> status=repealed

Why: chunks never get dates from text (only from the manifest, 22/105 docs), so
conflict resolution's "latest law wins" can never fire and parallel versions
pile up as unresolvable conflicts. Duplicates are moved (not deleted) to
data/legal_docs/_duplicates/ and their document_ids are removed from Milvus,
the ingestion journal and versions.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
MILVUS_URI = "http://127.0.0.1:19530"
COLLECTION = "legal_chunks"
DUPLICATES_DIR = DATA_DIR / "legal_docs" / "_duplicates"

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

_FULL_DATE_RE = re.compile(
    r"du\s+(\d{1,2})\s+(" + "|".join(_FR_MONTHS) + r")\s+(\d{4})", re.IGNORECASE
)
# Filenames hyphenate: "…-du-17-décembre-2022-…".
_FILENAME_DATE_RE = re.compile(
    r"du[-\s](\d{1,2})[-\s](" + "|".join(_FR_MONTHS) + r")[-\s](\d{4})", re.IGNORECASE
)
_LAW_NUMBER_RE = re.compile(r"(\d{3,4})\s*[-–/]\s*(\d{4})")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def load_journal(data_dir: Path) -> dict:
    return json.loads((data_dir / "ingestion_results.json").read_text())


def save_journal(data_dir: Path, journal: dict) -> None:
    (data_dir / "ingestion_results.json").write_text(
        json.dumps(journal, ensure_ascii=False, indent=1)
    )


def host_path(entry_path: str, data_dir: Path) -> Path:
    """Journal paths are container paths (/app/data/...) — map to the host."""
    rel = entry_path.replace("/app/data/", "").lstrip("/")
    return data_dir / rel


def milvus_client(uri: str):
    from pymilvus import MilvusClient

    return MilvusClient(uri)


def doc_chunks(client, document_id: str) -> list[dict]:
    return client.query(
        collection_name=COLLECTION,
        filter=f'document_id == "{document_id}"',
        output_fields=["chunk_id", "document_id", "article", "status",
                       "document_type", "legal_domains", "vector", "chunk_json"],
        limit=10000,
    )


def repatch_chunks(client, rows: list[dict], mutate) -> int:
    """Apply `mutate(chunk_dict) -> None` to each chunk_json and re-upsert with
    the unchanged vector (full-row upsert: works on every Milvus 2.x)."""
    patched = 0
    for row in rows:
        chunk = json.loads(row["chunk_json"])
        mutate(chunk)
        row["chunk_json"] = json.dumps(chunk, ensure_ascii=False)
        row["status"] = chunk.get("status") or ""
        patched += 1
    if rows:
        client.upsert(collection_name=COLLECTION, data=rows)
    return patched


def doc_chunks_strict(client, document_id: str) -> list[dict]:
    """doc_chunks + a loud failure when the store returns nothing for a
    document the journal says is indexed (a vacuous patch is a silent no-op —
    that failure mode already happened once)."""
    rows = doc_chunks(client, document_id)
    if not rows:
        print(f"  !! {document_id}: 0 rows in the store — index/store mismatch, nothing patched")
    return rows


def _iso(day: str, month_name: str, year: str) -> str | None:
    try:
        return date(int(year), _FR_MONTHS[month_name.lower()], int(day)).isoformat()
    except (ValueError, KeyError):
        return None


def extract_publication_date(filename: str, first_text: str) -> tuple[str | None, str]:
    """Return (iso_date, granularity) — granularity full|year|none.

    Precision order (a wrong full date is worse than a year-only one):
    1. `du-<D>-<month>-<YYYY>` inside the FILENAME (curated download names).
    2. the document's own law number (n°028-2022) with `du <date>` in canonical
       adjacency ("Loi n°028-2022/ALT du 17 décembre 2022") — the gap may not
       contain another law number (a "n°" or a digit), which is how amending
       laws quote the amended law's date.
    3. year embedded in the law number (n°…-2023) → YYYY-01-01.
    4. any 20xx year in the filename → YYYY-01-01.
    Deep-text and bare title-block dates are NEVER used: preambles quote other
    laws' dates.
    """
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        iso = _iso(*m.groups())
        if iso:
            return iso, "full"

    text = first_text.lower()
    num = _LAW_NUMBER_RE.search(filename) or _LAW_NUMBER_RE.search(first_text[:400])
    if num:
        n, year = num.group(1), num.group(2)
        near = re.search(
            rf"n[°o]?\s*0*{int(n)}\s*[-–/\s]?\s*{year}[a-z/]*[^°\d]{{0,60}}?"
            rf"du\s+(\d{{1,2}})\s+({'|'.join(_FR_MONTHS)})\s+(\d{{4}})",
            text,
            re.DOTALL,
        )
        if near:
            iso = _iso(*near.groups())
            if iso:
                return iso, "full"

    if num:
        return f"{num.group(2)}-01-01", "year"
    m = _YEAR_RE.search(filename)
    if m:
        return f"{m.group(1)}-01-01", "year"
    return None, "none"


def first_pages_text(pdf_path: Path, pages: int = 2) -> str:
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        text = "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))
        doc.close()
        return text
    except Exception as exc:
        print(f"  ! cannot read {pdf_path.name}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def cmd_dates(apply: bool, data_dir: Path, uri: str) -> None:
    journal = load_journal(data_dir)
    manifest_path = data_dir / "legal_sources.json"
    manifest = json.loads(manifest_path.read_text())
    doc_meta = manifest.setdefault("document_metadata", {})

    client = milvus_client(uri) if apply else None
    full = year = missing = skipped = 0
    for document_id, entry in sorted(journal.items()):
        pdf = host_path(entry.get("path", ""), data_dir)
        name = entry.get("document_name", pdf.name)
        if not pdf.exists():
            print(f"  ? {name[:60]} — file missing ({pdf.name})")
            missing += 1
            continue
        existing = (doc_meta.get(pdf.name) or {}).get("publication_date")
        if existing:
            skipped += 1
            continue
        iso, granularity = extract_publication_date(pdf.name, first_pages_text(pdf))
        if not iso:
            print(f"  ✗ {name[:70]} — no date found")
            missing += 1
            continue
        tag = "📅" if granularity == "full" else "📆"  # year-only
        print(f"  {tag} {iso} [{granularity}] {name[:70]}")
        if granularity == "full":
            full += 1
        else:
            year += 1
        if apply:
            meta = doc_meta.setdefault(pdf.name, {})
            meta.setdefault("document_name", name)
            meta["publication_date"] = iso
            rows = doc_chunks_strict(client, document_id)
            repatch_chunks(
                client,
                rows,
                lambda c, iso=iso: c.__setitem__("publication_date", iso)
                if not c.get("publication_date")
                else None,
            )
    print(f"\nfull dates: {full}, year-only: {year}, none/missing: {missing}, already set: {skipped}")
    if apply:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        print("applied to Milvus + legal_sources.json")


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def cmd_dedupe(apply: bool, data_dir: Path, uri: str) -> None:
    journal = load_journal(data_dir)
    versions_path = data_dir / "versions.json"
    versions = json.loads(versions_path.read_text())

    # Map journal path basename -> document_id (a file may be indexed once).
    by_file: dict[str, str] = {}
    for document_id, entry in journal.items():
        by_file[Path(entry.get("path", "")).name] = document_id

    hashes: dict[str, list[Path]] = {}
    for pdf in sorted((data_dir / "legal_docs").rglob("*.pdf")):
        if DUPLICATES_DIR in pdf.parents:
            continue
        hashes.setdefault(_sha256(pdf), []).append(pdf)

    losers: list[tuple[Path, str]] = []  # (pdf, document_id)
    for digest, paths in hashes.items():
        if len(paths) < 2:
            continue
        # Keeper: prefer a filename not starting with "assemblee_", then the
        # longest journal title (usually the curated one).
        def keeper_rank(p: Path) -> tuple[int, int]:
            doc_id = by_file.get(p.name, "")
            title = (journal.get(doc_id) or {}).get("document_name", "")
            return (p.name.startswith("assemblee_"), -len(title))

        paths_sorted = sorted(paths, key=keeper_rank)
        keep, drop = paths_sorted[0], paths_sorted[1:]
        print(f"  ≡ {digest[:8]} keep {keep.name}")
        for p in drop:
            doc_id = by_file.get(p.name, "")
            print(f"    ✗ dupe {p.name} (document_id={doc_id or 'not indexed'})")
            losers.append((p, doc_id))

    if not losers:
        print("no byte-identical duplicates")
        return
    if apply:
        client = milvus_client(uri)
        DUPLICATES_DIR.mkdir(parents=True, exist_ok=True)
        for pdf, document_id in losers:
            if document_id:
                n = client.delete(collection_name=COLLECTION, filter=f'document_id == "{document_id}"')
                journal.pop(document_id, None)
                versions.pop(document_id, None)
                print(f"    removed {document_id} ({n} chunks)" if hasattr(n, "__int__") else f"    removed {document_id}")
            shutil.move(str(pdf), DUPLICATES_DIR / pdf.name)
        save_journal(data_dir, journal)
        versions_path.write_text(json.dumps(versions, ensure_ascii=False, indent=1))
        print(f"moved {len(losers)} duplicate file(s) to {DUPLICATES_DIR}")


# ---------------------------------------------------------------------------
# repeal
# ---------------------------------------------------------------------------


def _family_key(document_name: str) -> str:
    """Normalize a document title to its legal-family key: lowercase, no law
    numbers / dates / parentheticals — 'Code minier … (Loi n°016-2024/ALT du
    18 juillet 2024)' and 'Code minier 2023' collapse to one family."""
    name = re.sub(r"\([^)]*\)", " ", document_name.lower())
    name = re.sub(r"n°\s*[\d\-–/alt]+", " ", name)
    name = re.sub(r"\b(19|20)\d{2}\b", " ", name)
    name = re.sub(r"\b(loi|décret|decret|du|du\s+\d+|portant|alt|an)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


_FAMILY_STOPWORDS = {
    "loi", "lois", "décret", "decret", "arrêté", "arrete", "ordonnance",
    "du", "de", "la", "le", "les", "des", "au", "aux", "et", "en", "sur",
    "portant", "alt", "an", "n", "n°", "no", "—", "-", "à", "a",
}


def _subject_tokens(document_name: str) -> frozenset[str]:
    """Significant tokens of a title: strips numbers, dates and structure words.
    'code'/'décret' stay significant so a code and its application decree
    never merge into one family."""
    name = re.sub(r"\([^)]*\)", " ", document_name.lower())
    name = re.sub(r"n[°o]\s*[\da-z\-–/]+", " ", name)  # law numbers
    name = re.sub(r"\b\d+\b", " ", name)  # bare numbers incl. years
    tokens = {
        t
        for t in re.findall(r"[a-zàâäçéèêëîïôöùûü]{3,}", name)
        if t not in _FAMILY_STOPWORDS
    }
    return frozenset(tokens)


def cmd_repeal(apply: bool, data_dir: Path, uri: str) -> None:
    journal = load_journal(data_dir)
    manifest = json.loads((data_dir / "legal_sources.json").read_text())
    doc_meta = manifest.get("document_metadata", {})

    # doc_id -> (date_iso, chunks, name, law_no, token_key)
    docs: dict[str, tuple[str, int, str, str | None, frozenset[str]]] = {}
    for document_id, entry in journal.items():
        name = entry.get("document_name", "")
        pdf_name = Path(entry.get("path", "")).name
        iso = (doc_meta.get(pdf_name) or {}).get("publication_date", "")
        law = _LAW_NUMBER_RE.search(name) or _LAW_NUMBER_RE.search(pdf_name)
        docs[document_id] = (
            iso or "0000-00-00",
            int(entry.get("chunks_created") or 0),
            name,
            f"{int(law.group(1))}-{law.group(2)}" if law else None,
            _subject_tokens(name),
        )

    # Union-find: link docs sharing a law number (same text, different scan)
    # or the same normalized subject tokens (older/newer version).
    parent = {d: d for d in docs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    by_law: dict[str, str] = {}
    by_tokens: dict[frozenset[str], str] = {}
    for document_id, (_, _, name, law_no, tokens) in docs.items():
        if law_no:
            if law_no in by_law:
                union(document_id, by_law[law_no])
            else:
                by_law[law_no] = document_id
        if tokens:
            if tokens in by_tokens:
                union(document_id, by_tokens[tokens])
            else:
                by_tokens[tokens] = document_id

    families: dict[str, list[str]] = {}
    for document_id in docs:
        families.setdefault(find(document_id), []).append(document_id)

    def has_abrogation(newer_doc_id: str) -> bool:
        """True when the newer text explicitly abrogates (an amendment law —
        'modifiant la loi n°X' — must NOT repeal the base law)."""
        entry = journal.get(newer_doc_id) or {}
        pdf = host_path(entry.get("path", ""), data_dir)
        if not pdf.exists():
            return False
        return "abrog" in first_pages_text(pdf).lower()

    repeals: list[tuple[str, str]] = []  # (document_id, name)
    for members in families.values():
        if len(members) < 2:
            continue
        # Newest date wins; on a tie the more complete document (more chunks).
        members.sort(key=lambda d: (docs[d][0], docs[d][1]))
        current = members[-1]
        current_law = docs[current][3]
        for document_id in members[:-1]:
            iso, chunks, name, law_no, _ = docs[document_id]
            same_law = law_no is not None and law_no == current_law
            if not same_law and not has_abrogation(current):
                print(
                    f"  · keep  {name[:64]} ({iso}) — newer version does not abrogate it\n"
                    f"      (amendment-style update; both stay active: {docs[current][2][:60]})"
                )
                continue
            why = "same law, duplicate scan" if same_law else "abrogated by newer text"
            print(
                f"  ⟳ repeal {name[:64]} ({iso}, {chunks} chunks) — {why}\n"
                f"      superseded by {docs[current][2][:64]} ({docs[current][0]}, {docs[current][1]} chunks)"
            )
            repeals.append((document_id, name))
        print(f"    ✓ current: {docs[current][2][:70]} ({docs[current][0]})")

    if not repeals:
        print("no superseded versions found")
        return
    if apply:
        client = milvus_client(uri)
        for document_id, _ in repeals:
            rows = doc_chunks_strict(client, document_id)
            n = repatch_chunks(client, rows, lambda c: c.__setitem__("status", "repealed"))
            print(f"    repealed {document_id} ({n} chunks)")


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["dates", "dedupe", "repeal"])
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--milvus", default=MILVUS_URI)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    {"dates": cmd_dates, "dedupe": cmd_dedupe, "repeal": cmd_repeal}[args.command](
        args.apply, data_dir, args.milvus
    )


if __name__ == "__main__":
    sys.exit(main())
