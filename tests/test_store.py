import pytest
from dbaide.assets.store import AssetStore, safe_name
from pathlib import Path
import tempfile


class TestSafeName:
    def test_normal_name(self):
        assert safe_name("test") == "test"

    def test_empty_name(self):
        assert safe_name("") == "default"

    def test_none_name(self):
        assert safe_name(None) == "default"

    def test_dot_only(self):
        assert safe_name(".") == "default"

    def test_dotdot(self):
        assert safe_name("..") == "default"

    def test_path_traversal(self):
        assert safe_name("../../../etc") == "etc"

    def test_special_chars(self):
        assert safe_name("test@#$%") == "test"

    def test_underscores_preserved(self):
        assert safe_name("test_name") == "test_name"

    def test_hyphens_preserved(self):
        assert safe_name("test-name") == "test-name"

    def test_spaces_replaced(self):
        assert safe_name("test name") == "test_name"

    def test_leading_trailing_stripped(self):
        assert safe_name("_.test._") == "test"


class TestAssetStore:
    def test_instance_dir(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        path = store.instance_dir("test")
        assert path == tmp_path / "instances" / "test"

    def test_database_dir(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        path = store.database_dir("test", "main")
        assert path == tmp_path / "instances" / "test" / "databases" / "main"

    def test_write_read_json(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        path = tmp_path / "test.json"
        store.write_json(path, {"key": "value"})
        assert store.read_json(path) == {"key": "value"}

    def test_write_json_atomic(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        path = tmp_path / "test.json"
        store.write_json(path, {"a": 1})
        store.write_json(path, {"b": 2})
        assert store.read_json(path) == {"b": 2}

    def test_read_json_missing(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        assert store._read_optional(tmp_path / "nonexistent.json") is None

    def test_read_json_corrupted(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert store._read_optional(path) is None

    def test_instance_doc_missing(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        assert store.instance_doc("nonexistent") is None

    def test_database_docs_missing(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        assert store.database_docs("nonexistent") == []

    def test_table_docs_missing(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        assert store.table_docs("nonexistent", "main") == []

    def test_column_docs_missing(self, tmp_path):
        store = AssetStore(base_dir=tmp_path)
        assert store.column_docs("nonexistent", "main", "users") == []
