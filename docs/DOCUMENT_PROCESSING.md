# Document Processing

The ingestion stack as implemented: **extract → clean → chunk → embed →
dedupe → version → upsert**, orchestrated by `IngestionPipeline`
(`backend/ingestion/pipeline.py`). CLI:

```bash
python -m backend.ingestion.pipeline <file-or-dir> [--name X] [--url Y] [--no-gc] [--full-reindex]
```

## Loaders

`backend/ingestion/loaders.py` dispatches on extension (`load_any`); all
third-party imports are lazy so the module imports cleanly without optional
dependencies. Loaders raise `IngestionError` on unreadable files and return an
`ExtractedDocument` (name, text, pages, metadata).

| Format | Extensions | Loader | Notes |
|---|---|---|---|
| PDF | `.pdf` | `pypdf` | Per-page text; textless pages recorded in `metadata["ocr_needed_pages"]`; best-effort OCR over embedded page images when pytesseract+Pillow are importable (never fatal). |
| DOCX | `.docx` | `python-docx` | Paragraphs + table cells (`a | b | c` lines). |
| HTML | `.html`, `.htm`, or `http(s)://` URL | BeautifulSoup + httpx | script/style/nav/header/footer stripped; page title kept in metadata. |
| Text | `.txt` | stdlib | Encoding-tolerant (UTF-8, UTF-8-BOM, cp1252, latin-1). |
| Markdown | `.md`, `.markdown` | stdlib | Lightweight markup stripping (headings, links, emphasis, fences). |
| CSV | `.csv` | stdlib `csv` | Each row rendered as `header=value | header=value` lines so cells stay self-describing once chunked out of order. |

## Text cleaning

`backend/ingestion/text_cleaning.py`: Unicode NFKC normalization (accents
preserved), whitespace cleanup, repeated header/footer removal
(`text_cleaning_min_pages_for_header` / `text_cleaning_header_min_frequency`),
and PDF extraction-artifact repair — line-break hyphenation ("consente - ment"
→ "consentement") and intra-word spaces ("lie u" → "lieu"), carefully scoped so
genuine hyphens (033-2012), one-letter words and elisions survive.
`repair_extraction_artifacts` is also applied at answer time so chunks ingested
before the cleaning fix still display clean text.

## Structure-aware legal chunking

`backend/ingestion/chunking.py` provides three strategies; the pipeline picks
with `chunk_strategy` (`auto` default → `legal_parent_child` when
`looks_like_legal` detects ≥ 2 legal headings, else `parent_child`):

- **`legal_parent_child`** — parents follow legal boundaries (`Article N`,
  `Art. N`, `Section`, `Chapitre`, `Titre`, `Partie`, `Livre`, `Annexe`), so a
  parent is a whole article or section (kept intact even beyond
  `chunk_parent_size`); each parent is split into child chunks
  (`chunk_child_size` / `chunk_overlap`) for dense retrieval. Children carry
  `parent_chunk_id`; retrieval searches children, the parent-expansion node
  restores full context.
- **`parent_child`** — size-based parents/children for unstructured text.
- **`semantic`** — flat split on the same legal boundaries with size fallback
  for oversized articles.

Boundary detection details:

- **Article numbers** include `1`, `1.2`, `123-4`, ordinal forms `1er`/`1ère`
  and the spelled-out `premier`/`première`; all normalize to a canonical key
  ("1er"/"premier" → "1").
- **Hierarchy tracking**: every chunk carries a `hierarchy` map
  (`{"livre": "I", "titre": "II", "chapitre": "III", "section": "3"}`) with the
  ordered heading path in force; a deeper heading resets the levels below it,
  and an `annexe` replaces the whole path. `section` keeps the deepest heading
  string for backward compatibility.
- Text before the first heading is kept as a preamble segment.
- Every chunk is stamped with full provenance (document name/id, article,
  section, page via cumulative page offsets, publication/effective dates,
  government body, URL, version, legal domains) plus `metadata["role"]`
  (`"parent"` / `"child"`).

## Classification and enrichment

`backend/ingestion/classification.py` fills gaps only — explicit
caller-provided metadata is never overwritten:

- `infer_authority` — ordered keyword rules over the document name
  (constitution → OHADA treaty/uniform acts → code/loi → décret → arrêté →
  circulaire → journal officiel → jurisprudence → communiqué), else `unknown`.
- `infer_legal_domains` — keyword map over name + first 2000 chars of content.
- `infer_document_type` — conservative instrument-type rules (treaty → code →
  ordonnance → décret → arrêté/décision → jurisprudence → loi); `None` when
  nothing matches.
- `extract_law_number` — structured numbers like `028-2008/AN` from
  loi/décret/ordonnance/arrêté/décision titles.

The pipeline additionally maps known corpus filenames to official display
titles (`_DOCUMENT_TITLE_MAP`), defaults `issuing_authority` from
`government_body`, `valid_from` from `effective_date`, and sets
`status="future"` only when the effective date lies ahead — repeal/expiry is
never claimed without explicit data. Resolved document-level fields
(`document_type`, `law_number`, `issuing_authority`, `jurisdiction`, `status`,
`valid_from`, `valid_until`) are propagated onto every chunk.

## Versioning and change detection

`backend/ingestion/versioning.py` — `VersionStore` persists
`document_id -> {"hash", "version", "articles"}` in
`data/versions.json` (atomic temp-file + `os.replace` writes).

- Whole-document SHA256 decides *new / unchanged / changed*; unchanged content
  is `skipped_duplicate`, changed content bumps the version and the old chunks
  are deleted before re-indexing.
- `check_version` / `commit_version` are split so a **failed** ingest never
  marks content as seen.
- **Article-level change detection (spec §26)**: `_article_hashes` buckets
  chunk text per article key (falling back to `section:` keys, then
  `__no_article__`); `diff_articles` returns an `ArticleDiff`
  (added/modified/deleted) which is logged and embedded in the ingest result's
  `detail` JSON. Legacy records without an article map are treated as empty —
  the first diff after upgrade reports every article as added.
- `reindex_directory` ingests every supported file under a path and
  garbage-collects stale documents (present in the registry, gone from disk).

## Ingestion result persistence

Alongside the version store, every ingest persists its outcome via
`record_ingestion_result` into `data/ingestion_results.json` — the latest
record per document id (`document_id`, `document_name`, `status`, `version`,
`chunks_created`, `timestamp`, optional `path`, and `error` for failed
ingests). Writes merge into the existing file and rewrite it atomically (temp
file + `os.replace`, same pattern as `VersionStore`), and persistence is
best-effort — it never fails the ingest. `load_ingestion_results` reads it
back (`{}` when absent/corrupt); consumers are the admin ingestion-status
endpoint (real `failed_documents` list) and `GET /api/v1/documents/{id}`.

## Legal knowledge graph persistence

After a successful upsert, `_persist_legal_graph` (spec §19/§34) upserts the
document, its articles and regex-extracted relationships into the relational
legal knowledge graph (`backend/knowledge/` — `documents`, `legal_articles`,
`legal_relationships` tables). Additive and fully best-effort: a graph failure
is logged and swallowed so it can never fail ingestion, and the hook is a no-op
when `legal_graph_enabled` is off. See
[LEGAL_RETRIEVAL.md](LEGAL_RETRIEVAL.md#legal-knowledge-graph-spec-19).

## Crawler and freshness monitor

- **Crawler** (`backend/ingestion/crawler.py`): polite depth-limited BFS
  restricted to an allowed-domain list (BF government / OHADA by default),
  per-origin robots.txt cache (permissive when unreachable), configurable
  delay/UA. Offline-safe: every fetch fails soft and the crawl returns what it
  gathered (often `[]`).
- **Freshness monitor** (`backend/ingestion/freshness.py`): polls RSS feeds
  (feedparser) and plain pages (HTTP `ETag`/`Last-Modified` HEAD), persists
  seen-state under `data_dir`, emits `ChangeEvent`s; an injectable `on_change`
  callback can trigger incremental re-indexing. Default registry: OHADA
  actualités feed, LégiBurkina, Assemblée Nationale, Portail du Gouvernement.
  Fully offline-safe.

## Known limitations (documented future work)

- **OCR path is dormant**: `pytesseract` and `Pillow` are not in
  `requirements.txt`; PDF pages with no extractable text are recorded
  (`ocr_needed_pages`) but not OCR'd unless both packages are installed.
- **`.doc` unsupported** (only `.docx`); no image loaders.
- **No layout-aware parser** (no Docling/Unstructured): two-column layouts,
  scanned gazettes and complex tables extract as flat text.
- **The crawler skips PDFs** (and Office/media files) by design — the Journal
  Officiel and most BF gazettes are PDFs, so the most authoritative artifacts
  must be ingested from files, not crawled.
- Old document versions are deleted, not archived. The relational legal
  knowledge graph (above) adds `documents` / `legal_articles` /
  `legal_relationships` tables, but it is an additive, best-effort index — the
  corpus truth for retrieval remains `versions.json` + the vector store.
