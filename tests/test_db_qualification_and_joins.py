"""Two design-flaw fixes:
1. The SQL writer keeps the database OUT of the SQL (bare table names) unless the
   query genuinely spans >1 database — a stray `db.table` prefix is a common cause
   of a confirmed table being flagged "unknown" at validation.
2. Semantic join inference is on-demand, not eager: collect_relations honours
   infer_semantic=False so the cheap auto-load never triggers the expensive LLM.
"""

import sqlite3

from dbaide.adapters import build_adapter
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.agent.join_validation import _qualified_sample_table
from dbaide.agent.schema_context import collect_relations
from dbaide.agent.sql_writer import SQLWriter
from dbaide.assets import AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import ColumnInfo, ConnectionConfig
from dbaide.session import Session


def _col(n, t="int"):
    return ColumnInfo(name=n, data_type=t)


# ── Flaw 2: database qualification ─────────────────────────────────────────--

def test_sql_writer_uses_bare_table_for_single_database():
    w = SQLWriter(NullLLMClient(), dialect="mysql")
    p = w._user_prompt_multi("q", [("order_data", "fulfillment", [_col("id")])], {})
    assert "Table: fulfillment" in p
    assert "order_data.fulfillment" not in p          # no stray db prefix
    assert "Active database: order_data" in p


def test_sql_writer_qualifies_only_across_databases():
    w = SQLWriter(NullLLMClient(), dialect="mysql")
    p = w._user_prompt_multi("q", [("a", "t1", [_col("id")]), ("b", "t2", [_col("id")])], {})
    assert "a.t1" in p and "b.t2" in p                 # cross-db → qualified


def test_system_prompt_forbids_db_prefix():
    sys = SQLWriter(NullLLMClient())._system_prompt()
    assert "BARE name" in sys and "more than one database" in sys.lower()


def test_sql_writer_normalizes_dialect_aliases():
    writer = SQLWriter(NullLLMClient(), dialect="postgresql")
    assert writer.dialect == "postgres"
    assert "PostgreSQL" in writer._system_prompt()


def test_join_sampler_qualifies_mariadb_like_mysql():
    assert _qualified_sample_table("order_data", "orders", "mariadb") == "order_data.orders"


# ── Flaw 1: semantic inference is on-demand ────────────────────────────────--

class _SpyLLM(LLMClient):
    def __init__(self):
        self.calls = 0

    def complete_json(self, messages, *, schema_hint=""):
        self.calls += 1
        return {"joins": []}

    def complete_text(self, messages):
        return "OK"


def _orch(tmp_path, llm):
    db = tmp_path / "x.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE a(id INTEGER PRIMARY KEY, x TEXT); CREATE TABLE b(id INTEGER PRIMARY KEY, y TEXT);")
    c.commit(); c.close()
    conn = ConnectionConfig(name="x", type="sqlite", path=str(db))
    return AskOrchestrator(build_adapter(conn), Session(connection=conn), llm,
                           asset_store=AssetStore(tmp_path / "a"), join_catalog=JoinCatalogStore(base_dir=tmp_path / "j"))


def test_infer_semantic_false_skips_the_llm(tmp_path):
    spy = _SpyLLM()
    orch = _orch(tmp_path, spy)
    schemas = [("main", "a", [_col("id"), _col("x", "text")]), ("main", "b", [_col("id"), _col("y", "text")])]
    collect_relations(orch, [("main", "a"), ("main", "b")], disclosed_schemas=schemas, infer_semantic=False)
    assert spy.calls == 0  # the eager/cheap path never calls the semantic inferencer


def test_infer_semantic_true_uses_the_llm(tmp_path):
    spy = _SpyLLM()
    orch = _orch(tmp_path, spy)
    schemas = [("main", "a", [_col("id"), _col("x", "text")]), ("main", "b", [_col("id"), _col("y", "text")])]
    collect_relations(orch, [("main", "a"), ("main", "b")], disclosed_schemas=schemas, infer_semantic=True)
    assert spy.calls >= 1  # two unconnected tables → on-demand inference does run
