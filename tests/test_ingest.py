from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dbaide.ingest import ImportManifest, import_workbooks


def _q(table: str) -> str:
    return '"' + table.replace('"', '""') + '"'


def test_csv_infers_types_and_preserves_leading_zero_codes(tmp_path):
    csv = tmp_path / "sales.csv"
    csv.write_text(
        "code,amount,qty,city\n0012,1234.5,3,BJ\n0034,99,10,SH\n0056,,2,GZ\n",
        encoding="utf-8",
    )
    res = import_workbooks([csv], dest_dir=tmp_path / "imports")
    sheet = res.manifest.workbooks[0].sheets[0]
    types = {c.name: c.type for c in sheet.columns}
    assert types == {"code": "TEXT", "amount": "REAL", "qty": "INTEGER", "city": "TEXT"}

    con = sqlite3.connect(res.db_path)
    try:
        assert [r[0] for r in con.execute(f"SELECT code FROM {_q(sheet.table)}")] == ["0012", "0034", "0056"]
        assert con.execute(f"SELECT SUM(amount) FROM {_q(sheet.table)}").fetchone()[0] == 1333.5
        # blank cell became NULL, not "" or 0
        assert con.execute(f"SELECT amount FROM {_q(sheet.table)} WHERE code='0056'").fetchone()[0] is None
    finally:
        con.close()


def test_csv_sanitizes_and_dedups_columns(tmp_path):
    csv = tmp_path / "weird.csv"
    # punctuation + CJK + duplicate header + an empty header
    csv.write_text("销售额(元),销售额(元),,name\n1,2,3,x\n", encoding="utf-8")
    res = import_workbooks([csv], dest_dir=tmp_path / "imports")
    names = [c.name for c in res.manifest.workbooks[0].sheets[0].columns]
    assert names[0] == "销售额_元"        # punctuation -> _, CJK kept
    assert names[1] == "销售额_元_2"      # duplicate disambiguated
    assert names[2] == "col_3"           # empty header -> positional name
    assert names[3] == "name"


def test_unsupported_type_and_empty_file_raise(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        import_workbooks([bad], dest_dir=tmp_path / "i1")

    empty = tmp_path / "empty.csv"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_workbooks([empty], dest_dir=tmp_path / "i2")


def test_manifest_round_trips_and_has_provenance(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    res = import_workbooks([csv], dest_dir=tmp_path / "imports")
    loaded = ImportManifest.load(res.db_path.parent / "manifest.json")
    wb = loaded.workbooks[0]
    assert wb.source_filename == "t.csv"
    assert wb.file_hash and wb.imported_at
    assert wb.sheets[0].row_count == 1


def test_failed_import_leaves_no_partial_db(tmp_path, monkeypatch):
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    import dbaide.ingest.importer as imp

    monkeypatch.setattr(imp, "_write_table", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        import_workbooks([csv], dest_dir=tmp_path / "imports")
    assert not (tmp_path / "imports" / "data.db").exists()


def test_xlsx_multi_sheet_skips_hidden_and_handles_dates(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook
    import datetime

    wb = Workbook()
    s1 = wb.active
    s1.title = "data"
    s1.append(["product", "price", "qty", "day"])
    s1.append(["A", 12.5, 3, datetime.date(2026, 1, 5)])
    s2 = wb.create_sheet("store")
    s2.append(["name"]); s2.append(["flagship"])
    hidden = wb.create_sheet("secret"); hidden.sheet_state = "hidden"
    hidden.append(["x"]); hidden.append([1])
    path = tmp_path / "book.xlsx"
    wb.save(path)

    res = import_workbooks([path], dest_dir=tmp_path / "imports")
    tables = [s.sheet_name for w in res.manifest.workbooks for s in w.sheets]
    assert tables == ["data", "store"]   # hidden sheet skipped
    cols = {c.name: c.type for c in res.manifest.workbooks[0].sheets[0].columns}
    assert cols == {"product": "TEXT", "price": "REAL", "qty": "INTEGER", "day": "TEXT"}
    con = sqlite3.connect(res.db_path)
    try:
        assert con.execute('SELECT day FROM "data"').fetchone()[0].startswith("2026-01-05")
    finally:
        con.close()


def test_multi_file_prefixes_table_names_to_avoid_collision(tmp_path):
    a = tmp_path / "a.csv"; a.write_text("x\n1\n", encoding="utf-8")
    b = tmp_path / "b.csv"; b.write_text("y\n2\n", encoding="utf-8")
    res = import_workbooks([a, b], dest_dir=tmp_path / "imports")
    tables = sorted(s.table for w in res.manifest.workbooks for s in w.sheets)
    assert tables == ["a__a", "b__b"]


def test_cli_ingest_registers_sqlite_connection(tmp_path):
    from dbaide.cli import build_parser, dispatch_ingest
    from dbaide.config import ConfigManager

    csv = tmp_path / "people.csv"
    csv.write_text("name,age\nAda,30\n", encoding="utf-8")
    cfg = ConfigManager(path=tmp_path / "config.toml")

    args = build_parser().parse_args(["ingest", str(csv), "--conn", "people", "--default"])
    assert dispatch_ingest(args, cfg) == 0

    conns = cfg.connections()
    assert "people" in conns
    assert conns["people"].type == "sqlite"
    assert Path(conns["people"].path).exists()

    # re-ingesting the same name without --replace is refused
    args2 = build_parser().parse_args(["ingest", str(csv), "--conn", "people"])
    assert dispatch_ingest(args2, cfg) == 1
