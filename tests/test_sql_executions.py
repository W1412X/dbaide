from dbaide.agent.run_state import RunState
from dbaide.agent.sql_executions import (
    MAX_SQL_PURPOSE_LEN,
    normalize_sql_purpose,
    record_sql_execution,
    response_sql_exports,
)


def test_normalize_sql_purpose_truncates():
    long = "验证订单表是否存在有效销量字段名称"
    out = normalize_sql_purpose(long)
    assert len(out) <= MAX_SQL_PURPOSE_LEN
    assert out


def test_record_sql_execution_accumulates_in_order():
    state = RunState()
    record_sql_execution(
        state,
        sql="SELECT 1",
        purpose="销量统计",
        database="main",
        tool="execute_readonly_sql",
        row_count=10,
        elapsed_ms=12.4,
        artifact_id="sql:1",
        columns=["a"],
    )
    record_sql_execution(
        state,
        sql="SELECT 2",
        purpose="广告投入",
        database="main",
        tool="execute_sql",
        row_count=3,
        elapsed_ms=8.0,
        artifact_id="sql:2",
    )
    selected, executed = response_sql_exports(state)
    assert selected == "SELECT 2"
    assert len(executed) == 2
    assert executed[0]["purpose"] == "销量统计"
    assert executed[1]["tool"] == "execute_sql"


def test_response_sql_exports_falls_back_to_legacy_sql():
    state = RunState(sql="SELECT legacy")
    selected, executed = response_sql_exports(state)
    assert selected == "SELECT legacy"
    assert executed == []
