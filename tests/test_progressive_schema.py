import sqlite3

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.progressive_schema import ProgressiveSchemaAgent
from dbaide.assets import AssetStore
from dbaide.config import ConfigManager
from dbaide.desktop.service import DesktopService
from dbaide.models import ConnectionConfig
from tests.llm_mock import AgentMockLLM


def _make_industrial_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE production_lines (line_id INTEGER PRIMARY KEY, line_name TEXT, line_code TEXT);
        CREATE TABLE assets (asset_id INTEGER PRIMARY KEY, line_id INTEGER, asset_name TEXT);
        CREATE TABLE bench_orders (id INTEGER PRIMARY KEY, total REAL);
        INSERT INTO production_lines VALUES (1, 'Line A', 'L1');
        INSERT INTO assets VALUES (1, 1, 'Pump');
        """
    )
    conn.commit()
    conn.close()


def test_progressive_schema_filters_production_line_tables(tmp_path):
    db = tmp_path / "industrial.db"
    _make_industrial_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="test", type="sqlite", path=str(db))
    cfg.upsert_connection(conn, make_default=True)
    service.build_assets({"name": "test", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})

    agent = ProgressiveSchemaAgent(AgentMockLLM(), store, "test")
    discovery = agent.discover("我想知道和产线相关的表")

    table_names = {h.name for h in discovery.hits if h.kind == "table"}
    assert "production_lines" in table_names
    assert "bench_orders" not in table_names


def test_progressive_schema_requires_llm():
    store = AssetStore()
    with pytest.raises(Exception):
        from dbaide.llm import NullLLMClient

        ProgressiveSchemaAgent(NullLLMClient(), store, "missing")


def test_single_database_skips_db_filter(tmp_path):
    """A one-database connection should not spend an LLM call filtering databases."""
    db = tmp_path / "industrial.db"
    _make_industrial_db(db)
    cfg = ConfigManager(tmp_path / "config.toml")
    store = AssetStore(tmp_path / "assets")
    service = DesktopService(cfg, store)
    conn = ConnectionConfig(name="test", type="sqlite", path=str(db))
    cfg.upsert_connection(conn, make_default=True)
    service.build_assets({"name": "test", "profile_mode": "auto", "top_k": 10, "sample_limit": 20})

    agent = ProgressiveSchemaAgent(AgentMockLLM(), store, "test")
    levels: list[str] = []
    real_filter = agent._filter_indices
    agent._filter_indices = lambda *a, **k: (levels.append(k.get("level", "")) or real_filter(*a, **k))
    discovery = agent.discover("我想知道和产线相关的表")
    assert "database" not in levels          # db filter skipped (only one db)
    assert "table" in levels                 # table filter still runs
    assert "production_lines" in {h.name for h in discovery.hits if h.kind == "table"}
