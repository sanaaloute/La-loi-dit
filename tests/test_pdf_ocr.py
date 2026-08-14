"""PDF OCR path: PyMuPDF text extraction + PaddleOCR recovery of scanned pages.

PaddleOCR itself is always mocked (the batch runner in
``backend.ingestion.ocr``, never the paddle packages — paddle runs in a child
process in production, see ``backend.ingestion.ocr_worker``); PyMuPDF builds
the fixtures, so the extraction path is exercised for real.
"""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF required for PDF fixtures")

from backend.ingestion.loaders import load_pdf
from backend.ingestion.pipeline import IngestionPipeline

_TEXT = "Article 1: Le présent code régit les relations de travail au Burkina Faso."
_SCANNED_TEXT = "DECRET N 2024-001 portant nomination des membres du gouvernement"


def _mock_ocr_batch(monkeypatch, text=_SCANNED_TEXT):
    """Mock the subprocess batch runner: every page image "recognized"."""
    monkeypatch.setattr(
        "backend.ingestion.ocr.ocr_images",
        lambda paths, timeout=None: {str(p): text for p in paths},
    )


def _text_pdf(path):
    """A normal PDF with a real text layer."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), _TEXT)
    doc.save(str(path))
    doc.close()
    return path


def _scanned_pdf(path, pages: int = 1):
    """A scanned-style PDF: each page holds only a rendered image of text."""
    doc = pymupdf.open()
    for _ in range(pages):
        src = pymupdf.open()
        src_page = src.new_page()
        src_page.insert_text((36, 72), _SCANNED_TEXT, fontsize=18)
        pixmap = src_page.get_pixmap(dpi=150)
        page = doc.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        src.close()
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def ocr_unavailable(monkeypatch):
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: False)


def test_text_pdf_extracts_without_ocr(tmp_path, monkeypatch):
    """Text PDFs go through PyMuPDF only: OCR is never consulted."""
    ocr_calls = []
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    monkeypatch.setattr(
        "backend.ingestion.ocr.ocr_images",
        lambda paths, timeout=None: ocr_calls.append(list(paths)) or {},
    )

    doc = load_pdf(_text_pdf(tmp_path / "text.pdf"))

    assert _TEXT in doc.text
    assert doc.metadata["page_count"] == 1
    assert "ocr_needed_pages" not in doc.metadata
    assert ocr_calls == []


def test_scanned_pdf_recovered_via_ocr(tmp_path, monkeypatch):
    """A textless page is rendered and OCR'd; metadata records the recovery."""
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    _mock_ocr_batch(monkeypatch)

    doc = load_pdf(_scanned_pdf(tmp_path / "scanned.pdf"))

    assert doc.pages == [_SCANNED_TEXT]
    assert doc.metadata["ocr_needed_pages"] == [1]
    assert doc.metadata["ocr_available"] is True
    assert doc.metadata["ocr_recovered_pages"] == [1]


async def test_scanned_pdf_ingests_via_ocr(ctx, tmp_path, monkeypatch):
    """End to end: a scanned PDF becomes ingestible once OCR recovers text."""
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    _mock_ocr_batch(monkeypatch)

    result = await IngestionPipeline(ctx).ingest_path(_scanned_pdf(tmp_path / "scanned.pdf"))

    assert result.status == "indexed"
    assert result.chunks_created > 0


async def test_scanned_pdf_without_ocr_fails_clearly(ctx, tmp_path, ocr_unavailable):
    """Without OCR a scanned PDF keeps the historical clear failure."""
    result = await IngestionPipeline(ctx).ingest_path(_scanned_pdf(tmp_path / "scanned.pdf"))

    assert result.status == "failed"
    assert "No extractable text" in result.detail


def test_scanned_pdf_without_ocr_keeps_empty_pages(tmp_path, ocr_unavailable):
    """The loader itself stays non-fatal and reports OCR as unavailable."""
    doc = load_pdf(_scanned_pdf(tmp_path / "scanned.pdf"))

    assert doc.text == ""
    assert doc.metadata["ocr_needed_pages"] == [1]
    assert doc.metadata["ocr_available"] is False
    assert "ocr_recovered_pages" not in doc.metadata


def test_ocr_page_cap(tmp_path, monkeypatch):
    """Pages beyond ocr_max_pages are skipped and noted in metadata."""
    from backend.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    _mock_ocr_batch(monkeypatch)

    doc = load_pdf(_scanned_pdf(tmp_path / "scanned.pdf", pages=3))

    assert doc.metadata["ocr_needed_pages"] == [1, 2, 3]
    assert doc.metadata["ocr_recovered_pages"] == [1]
    assert doc.metadata["ocr_skipped_pages"] == [2, 3]
    assert doc.pages[0] == _SCANNED_TEXT
    assert doc.pages[1:] == ["", ""]


def test_ocr_no_cap_processes_all_pages_in_batches(tmp_path, monkeypatch):
    """Default (uncapped): every page is OCR'd, split across batch subprocesses."""
    from backend.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_max_pages", 0)
    monkeypatch.setattr(settings, "ocr_batch_pages", 2)
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    batches: list[int] = []
    monkeypatch.setattr(
        "backend.ingestion.ocr.ocr_images",
        lambda paths, timeout=None: (batches.append(len(paths)), {str(p): _SCANNED_TEXT for p in paths})[1],
    )

    doc = load_pdf(_scanned_pdf(tmp_path / "scanned.pdf", pages=5))

    assert batches == [2, 2, 1]  # 5 pages in batches of 2
    assert doc.metadata["ocr_recovered_pages"] == [1, 2, 3, 4, 5]
    assert "ocr_skipped_pages" not in doc.metadata
    assert all(page == _SCANNED_TEXT for page in doc.pages)



def test_ocr_worker_failure_is_non_fatal(tmp_path, monkeypatch):
    """A crashed/failed OCR child process leaves pages empty, never raises."""
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: True)
    monkeypatch.setattr(
        "backend.ingestion.ocr.ocr_images", lambda paths, timeout=None: {}
    )

    doc = load_pdf(_scanned_pdf(tmp_path / "scanned.pdf"))

    assert doc.text == ""
    assert doc.metadata["ocr_available"] is True
    assert doc.metadata["ocr_needed_pages"] == [1]


# ---------------------------------------------------------------------------
# pypdf fallback (corrupt streams PyMuPDF rejects, e.g. zlib header errors)
# ---------------------------------------------------------------------------


def test_pypdf_fallback_when_mupdf_cannot_open(tmp_path, monkeypatch):
    """A hard PyMuPDF failure falls back to pypdf for the whole document."""
    import sys
    from types import SimpleNamespace

    pdf_path = _text_pdf(tmp_path / "text.pdf")  # real file, readable by pypdf

    def _boom(path):
        raise RuntimeError("library error: zlib error: incorrect header check")

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=_boom))

    doc = load_pdf(pdf_path)

    assert doc.metadata["loader"] == "pypdf"
    assert "zlib error" in doc.metadata["mupdf_error"]
    assert _TEXT in doc.text


def test_pypdf_recovers_pages_mupdf_extracts_nothing_from(tmp_path, monkeypatch):
    """Textless pages are retried with pypdf before the OCR path runs."""
    import sys
    from types import SimpleNamespace

    pdf_path = _text_pdf(tmp_path / "text.pdf")

    class _FakePage:
        def get_text(self):
            return ""  # simulates PyMuPDF failing on a corrupt content stream

    class _FakePdf:
        def __iter__(self):
            return iter([_FakePage()])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda path: _FakePdf()))
    monkeypatch.setattr("backend.ingestion.ocr.ocr_available", lambda: False)

    doc = load_pdf(pdf_path)

    assert doc.metadata["pypdf_recovered_pages"] == [1]
    assert _TEXT in doc.text
    # pypdf recovered the page: OCR is never needed.
    assert "ocr_needed_pages" not in doc.metadata


def test_unreadable_pdf_still_fails_when_pypdf_cannot_help(tmp_path, monkeypatch):
    """Both extractors failing keeps the historical clear IngestionError."""
    import sys
    from types import SimpleNamespace

    from backend.core.exceptions import IngestionError

    bad = tmp_path / "garbage.pdf"
    bad.write_bytes(b"not a pdf at all")

    def _boom(path):
        raise RuntimeError("library error: zlib error: incorrect header check")

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=_boom))

    with pytest.raises(IngestionError, match="Failed to read PDF"):
        load_pdf(bad)


def test_ocr_child_env_caps_thread_pools(tmp_path, monkeypatch):
    """The OCR child gets OMP/MKL thread caps from ocr_cpu_threads."""
    import json
    from pathlib import Path

    import backend.ingestion.ocr as ocr
    from backend.core.config import get_settings

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        manifest = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        Path(manifest["out"]).write_text("{}", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    monkeypatch.setenv("LEGAL_AI_OCR_CPU_THREADS", "2")
    get_settings.cache_clear()
    try:
        ocr.ocr_images([tmp_path / "page.png"])
    finally:
        get_settings.cache_clear()

    assert captured["env"]["OMP_NUM_THREADS"] == "2"
    assert captured["env"]["MKL_NUM_THREADS"] == "2"
