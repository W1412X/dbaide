"""Breadth stress-test of the Ask agent loop.

Drives the REAL orchestrator/loop/toolkit/schema-link/validator/executor over a
matrix of schemas x questions x execution policies (50+ full flows) plus the
clarify→pause→resume path, asserting cross-cutting invariants. The point is to
exercise the agent's core control flow broadly and catch regressions that a
single happy-path test would miss — not to test the LLM (a deterministic,
state-driven mock stands in for it).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest

from dbaide.adapters import build_adapter
from dbaide.agent.loop import AskAgentLoop
from dbaide.agent.orchestrator import AskOrchestrator
from dbaide.assets import AssetBuilder, AssetStore
from dbaide.core.result import ExecutionPolicy
from dbaide.llm import LLMClient, LLMMessage
from dbaide.models import ConnectionConfig
from dbaide.session import Session


class _StressMock(LLMClient):
    """Deterministic stand-in for the LLM. Loop decisions read TRUE orchestrator
    state (resolved schema / sql / result) the way a real agent reasons over what
    it has, so the matrix exercises real control flow rather than a scripted path."""

    def __init__(self, orch_ref: dict, ambiguous: bool = False) -> None:
        self._orch_ref = orch_ref   # {"orch": <set after construction>}
        self.ambiguous = ambiguous
        self._validated = False

    @property
    def orch(self):
        return self._orch_ref["orch"]

    def supports_streaming(self) -> bool:
        return False

    def complete_text(self, messages: list[LLMMessage]) -> str:
        return "Synthesized schema answer."

    def complete_json(self, messages: list[LLMMessage], *, schema_hint: str = "") -> dict[str, Any]:
        system = messages[0].content if messages else ""
        user = messages[-1].content if messages else ""
        if "operating in a tool loop" in system:
            return self._loop(system, user)
        if "Classify a database assistant" in system:
            return self._route(user)
        if "schema linker for Text-to-SQL" in system:
            return self._link(user)
        if "generate safe read-only SQL" in system:
            return self._sql(user)
        if "relevant_indices" in system:
            objects = user.split("Objects:\n")[-1] if "Objects:" in user else user
            return {"relevant_indices": [int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", objects)]}
        return {}

    def _route(self, user: str) -> dict[str, Any]:
        if any(k in user for k in ("有哪些表", "字段", "在哪里", "结构")):
            return {"task": "schema_explore"}
        return {"task": "data_query"}

    def _link(self, user: str) -> dict[str, Any]:
        section = user.split("Candidate tables:\n", 1)[-1]
        question = user.split("Question:", 1)[-1].strip().split("\n", 1)[0] if "Question:" in user else ""
        tables = []
        for m in re.finditer(r'^-\s+([^\s.]+)\.("[^"]+"|[^\s\[]+)\s*\[([^\]]*)\]', section, re.MULTILINE):
            db, tbl, cols = m.group(1), m.group(2).strip('"'), m.group(3)
            # A column may carry an inline note annotation ("total(📝note)") — strip it.
            col_names = [c.split("(")[0].split()[0] for c in cols.split(",") if c.strip()]
            tables.append({"database": db, "table": tbl, "columns": col_names, "reason": "stress"})
        if self.ambiguous and len(tables) >= 2:
            return {"ask": {"question": "Which table?", "options": [t["table"] for t in tables[:3]]}}
        # Join intent → keep two tables so the multi-table generate_sql + join-inference
        # path runs (single-table questions keep just one).
        n = 2 if (any(k in question for k in ("每个", "关联", "join")) and len(tables) >= 2) else 1
        return {"tables": tables[:n], "sufficient": bool(tables), "missing": "", "ask": None}

    def _sql(self, user: str) -> dict[str, Any]:
        m = re.search(r'^Table:\s*("[^"]+"|\S+)', user, re.MULTILINE) or \
            re.search(r'^-\s+(?:\w+\.)?("[^"]+"|\w+)', user, re.MULTILINE)
        if not m:
            return {"sql": "SELECT 1 AS n", "rationale": "fallback", "confidence": 0.3}
        table = m.group(1)
        bare = table.strip('"')
        if not re.fullmatch(r"\w+", bare) or bare.lower() in {"select", "order", "table"}:
            table = f'"{bare}"'
        return {"sql": f"SELECT COUNT(*) AS n FROM {table}", "rationale": "count", "confidence": 0.9}

    def _loop(self, system: str, user: str) -> dict[str, Any]:
        if "User reply:" in user:
            self.ambiguous = False
        q = (user.split("User question:", 1)[1].split("Database scope:", 1)[0].strip()
             if "User question:" in user else user)
        prior = user.count("Tool `")
        execute_allowed = "execute_sql is allowed" in system
        if any(k in q for k in ("有哪些表", "字段", "在哪里", "结构")):
            if prior == 0:
                return {"action": "call_tool", "tool": "discover_schema", "args": {"question": q}}
            if prior == 1:
                return {"action": "call_tool", "tool": "synthesize_schema_answer", "args": {"question": q}}
            return {"action": "finish", "answer": "Schema answer."}
        rs = self.orch.run_state
        if rs.resolved_schema is None or rs.resolved_schema.is_empty():
            return {"action": "call_tool", "tool": "resolve_schema", "args": {"question": q}}
        if not rs.sql:
            return {"action": "call_tool", "tool": "generate_sql", "args": {"question": q}}
        if not self._validated:
            self._validated = True
            return {"action": "call_tool", "tool": "validate_sql", "args": {}}
        if execute_allowed and rs.query_result is None:
            return {"action": "call_tool", "tool": "execute_sql", "args": {}}
        return {"action": "finish", "answer": ""}


_SCHEMAS = {
    "shop": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT);"
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total REAL,"
            " FOREIGN KEY(user_id) REFERENCES users(id));"
            "INSERT INTO orders VALUES (1,1,9.9);",
    "industrial": "CREATE TABLE production_lines (line_id INTEGER PRIMARY KEY, line_name TEXT);"
                  "CREATE TABLE assets (asset_id INTEGER PRIMARY KEY, line_id INTEGER, status TEXT);"
                  "INSERT INTO assets VALUES (1,1,'on');",
    "empty": "CREATE TABLE logs (id INTEGER PRIMARY KEY, level TEXT, msg TEXT);",
    "weird": 'CREATE TABLE "order details" (id INTEGER PRIMARY KEY, "unit price" REAL);'
             'CREATE TABLE "select" (id INTEGER PRIMARY KEY, val TEXT);'
             'INSERT INTO "order details" VALUES (1, 3.5);',
    "single": "CREATE TABLE people (id INTEGER PRIMARY KEY, full_name TEXT, age INTEGER);"
              "INSERT INTO people VALUES (1,'Jo',30);",
}
_QUESTIONS = [
    ("schema", "这个库有哪些表？"),
    ("schema", "email 字段在哪里？"),
    ("data", "统计订单数量"),
    ("data", "一共有多少行"),
    ("data", "查询最近的数据"),
    ("data", "统计每个用户的订单数"),   # join intent → multi-table generate + join inference
]
_POLICIES = [ExecutionPolicy.SAFE_AUTO, ExecutionPolicy.SQL_ONLY]
_NO_EXEC = {ExecutionPolicy.SQL_ONLY, ExecutionPolicy.INSPECT_ONLY}


@pytest.fixture(scope="module")
def _connections(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stress")
    # Point the asset store at a scratch dir for the whole module, and RESTORE the
    # previous value on teardown so this module's env doesn't leak into other tests.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DBAIDE_ASSETS", str(tmp / "assets"))
        conns = {}
        for name, ddl in _SCHEMAS.items():
            path = tmp / f"{name}.db"
            c = sqlite3.connect(path); c.executescript(ddl); c.commit(); c.close()
            cfg = ConnectionConfig(name=name, type="sqlite", path=str(path))
            AssetBuilder(connection=cfg, adapter=build_adapter(cfg), store=AssetStore(),
                         llm=None).build(sample=False, profile_mode="none")
            conns[name] = cfg
        yield conns


@pytest.mark.parametrize("sname", list(_SCHEMAS))
@pytest.mark.parametrize("qkind,question", _QUESTIONS)
@pytest.mark.parametrize("policy", _POLICIES)
def test_agent_flow_matrix(_connections, sname, qkind, question, policy):
    """50 full agent flows (5 schemas x 5 questions x 2 policies); each must obey the
    cross-cutting invariants below."""
    cfg = _connections[sname]
    ref: dict = {}
    mock = _StressMock(ref)
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), mock,
                           execution_policy=policy)
    ref["orch"] = orch
    resp = AskAgentLoop(orch).run(question, execute=True)

    assert resp is not None, "loop returned None"
    waiting = getattr(resp, "status", "") == "wait_user"
    if not waiting:
        assert (resp.answer or "").strip(), "completed run has empty answer"
    # Non-executing policy must never run the query.
    if policy in _NO_EXEC:
        assert resp.result is None, "non-exec policy produced an execution result"
    # Schema questions never fabricate an execution result.
    if qkind == "schema":
        assert resp.result is None
    # A real result must never be mislabelled partial.
    if resp.result is not None:
        assert "partial" not in " ".join(resp.warnings or []).lower()
    # Executing data queries should actually return rows.
    if qkind == "data" and policy not in _NO_EXEC and not waiting:
        assert resp.result is not None, "executing data query produced no result"


def test_join_question_exercises_multi_table_path(_connections):
    """A join-intent question resolves TWO tables and runs join inference (the
    declared FK), so the multi-table generate_sql branch is actually exercised."""
    cfg = _connections["shop"]
    ref: dict = {}
    mock = _StressMock(ref)
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), mock,
                           execution_policy=ExecutionPolicy.SAFE_AUTO)
    ref["orch"] = orch
    resp = AskAgentLoop(orch).run("统计每个用户的订单数", execute=True)
    assert orch.run_state.resolved_schema is not None
    assert len(orch.run_state.resolved_schema.tables) == 2     # two tables resolved
    assert len(orch.run_state.relations or []) >= 1            # FK inferred between them
    assert resp.result is not None


def test_user_notes_reach_the_sql_writer(_connections, tmp_path):
    """User notes are authoritative and must reach the SQL writer — the most critical
    injection point. A table note AND a column note both appear in the generate_sql
    prompt (table note in the authoritative block, column note on its column line)."""
    from dbaide.annotations import AnnotationStore

    cfg = _connections["shop"]
    ann = AnnotationStore(base_dir=tmp_path / "ann")
    ann.add("shop", scope="table", note="DEPRECATED use orders_v2", database="main", table="orders")
    ann.add("shop", scope="column", note="total is in CENTS not dollars",
            database="main", table="orders", column="total")

    ref: dict = {}
    mock = _StressMock(ref)
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), mock,
                           execution_policy=ExecutionPolicy.SAFE_AUTO, annotations=ann)
    ref["orch"] = orch

    sql_prompts: list[str] = []
    inner = mock.complete_json

    def capture(messages, **kw):
        if messages and "generate safe read-only SQL" in messages[0].content:
            sql_prompts.append("\n".join(m.content for m in messages))
        return inner(messages, **kw)

    mock.complete_json = capture
    AskAgentLoop(orch).run("统计订单总额", execute=True)

    assert sql_prompts, "generate_sql never ran"
    prompt = sql_prompts[-1]
    assert "DEPRECATED use orders_v2" in prompt          # table note reached the SQL writer
    assert "total is in CENTS not dollars" in prompt     # column note rode on its column


def test_clarify_pause_then_resume_completes(_connections):
    """An ambiguous data query pauses for clarification, and the user's reply resumes
    the SAME workflow through to an executed result (no stall, no stale 'partial')."""
    cfg = _connections["shop"]
    ref: dict = {}
    mock = _StressMock(ref, ambiguous=True)
    orch = AskOrchestrator(build_adapter(cfg), Session(connection=cfg), mock,
                           execution_policy=ExecutionPolicy.SAFE_AUTO)
    ref["orch"] = orch
    first = AskAgentLoop(orch).run("统计订单数量", execute=True)
    assert first.status == "wait_user" and first.pending_question
    assert first.resume_state

    resumed = AskAgentLoop(orch).run("统计订单数量", resume_state=first.resume_state,
                                     user_reply="orders")
    assert resumed.status != "wait_user"
    assert resumed.result is not None and resumed.result.row_count == 1
    assert "partial" not in " ".join(resumed.warnings or []).lower()
