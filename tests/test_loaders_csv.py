"""CSV loader tests (offline, tmp_path only)."""

from __future__ import annotations

import pytest


def test_load_csv_renders_header_and_rows(tmp_path):
    from backend.ingestion.loaders import load_csv

    path = tmp_path / "lois.csv"
    path.write_text("numero,titre\n028-2008/AN,Code du travail\n010-2015/AN,Charte de la Transition\n", encoding="utf-8")
    doc = load_csv(path)
    lines = doc.text.splitlines()
    assert lines[0] == "numero | titre"
    assert lines[1] == "numero=028-2008/AN | titre=Code du travail"
    assert lines[2] == "numero=010-2015/AN | titre=Charte de la Transition"
    assert doc.metadata["loader"] == "csv"
    assert doc.metadata["format"] == "csv"
    assert doc.metadata["row_count"] == 2


def test_load_csv_skips_blank_rows(tmp_path):
    from backend.ingestion.loaders import load_csv

    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n,\n3,4\n", encoding="utf-8")
    doc = load_csv(path)
    assert doc.text.splitlines() == ["a | b", "a=1 | b=2", "a=3 | b=4"]


def test_load_csv_tolerates_cp1252_encoding(tmp_path):
    from backend.ingestion.loaders import load_csv

    path = tmp_path / "latin.csv"
    path.write_bytes("col\nRépublique\n".encode("cp1252"))
    doc = load_csv(path)
    assert "République" in doc.text


def test_load_csv_missing_file(tmp_path):
    from backend.core.exceptions import IngestionError
    from backend.ingestion.loaders import load_csv

    with pytest.raises(IngestionError):
        load_csv(tmp_path / "nope.csv")


def test_load_csv_empty_file(tmp_path):
    from backend.core.exceptions import IngestionError
    from backend.ingestion.loaders import load_csv

    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(IngestionError):
        load_csv(path)


async def test_load_any_dispatches_csv(tmp_path):
    from backend.ingestion.loaders import SUPPORTED_EXTENSIONS, load_any

    assert SUPPORTED_EXTENSIONS[".csv"] == "csv"
    path = tmp_path / "table.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    doc = await load_any(path)
    assert doc.metadata["format"] == "csv"
    assert doc.text.splitlines()[0] == "x | y"
