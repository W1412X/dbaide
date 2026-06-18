import sqlite3

import pytest

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


def test_filter_indices_maps_second_batch_global_positions(tmp_path):
    """The LLM is shown each object's global position and returns those positions;
    _filter_indices must map them correctly across batch boundaries (BATCH_SIZE=18).
    A regression guard: the old code treated returned indices as batch-relative and
    dropped every selection in the 2nd+ batch."""
    import re
    from dbaide.agent.progressive_schema import ProgressiveSchemaAgent, BATCH_SIZE
    from dbaide.llm import LLMClient

    class _EchoTargetLLM(LLMClient):
        """Echoes back the bracketed labels of objects named 'target'."""
        def complete_json(self, messages, *, schema_hint=""):
            user = messages[-1].content
            objects = user.split("Objects:\n")[-1]
            picked = [int(m.group(1)) for m in re.finditer(r"\[(\d+)\]\s+([^\n(—]+)", objects)
                      if m.group(2).strip() == "target"]
            return {"relevant_indices": picked, "reason": "echo"}

    agent = ProgressiveSchemaAgent(_EchoTargetLLM(), AssetStore(tmp_path / "a"), "t")
    # 25 objects; the only 'target' sits at global position 20 → inside batch 2.
    items = [{"index": i, "name": ("target" if i == 20 else f"obj{i}"), "summary": ""}
             for i in range(25)]
    assert len(items) > BATCH_SIZE  # forces multiple batches
    kept = agent._filter_indices("find target", level="table", items=items, context="x")
    assert kept == [20]
