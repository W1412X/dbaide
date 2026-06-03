"""Result export formatters: JSON and INSERT (CSV/Markdown already exist)."""
from dbaide.rendering.table import export_insert, export_json


def test_export_json_array_of_objects():
    import json
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    out = json.loads(export_json(rows, ["id", "name"]))
    assert out == rows


def test_export_json_empty():
    assert export_json([]) == "[]"


def test_export_insert_escapes_and_types():
    rows = [{"id": 1, "name": "O'Brien", "amt": 3.5, "flag": None}]
    sql = export_insert(rows, ["id", "name", "amt", "flag"], table="users")
    assert sql == "INSERT INTO users (id, name, amt, flag) VALUES (1, 'O''Brien', 3.5, NULL);"


def test_export_insert_empty():
    assert export_insert([]) == ""
