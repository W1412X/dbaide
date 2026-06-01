"""The agent reasoning budget (step / SQL-retry / disclosed-tables) is now a
user-configurable ResourcePolicy knob that flows policy → session → runtime."""

from dbaide.agent.runtime import AgentRuntime
from dbaide.config import ConfigManager
from dbaide.db.policy import ResourcePolicy, resolve_policy
from dbaide.models import ConnectionConfig
from dbaide.session import Session


def test_resource_policy_has_agent_budget_defaults():
    p = ResourcePolicy()
    assert p.agent_max_steps == 12
    assert p.agent_sql_retries == 2
    assert p.agent_max_disclosed_tables == 4


def test_user_overrides_flow_through_resolve_policy():
    policy = resolve_policy(
        load_profile="production",
        overrides={"agent_max_steps": 30, "agent_sql_retries": 5},
        instance="",  # bypass cache
    )
    assert policy.agent_max_steps == 30
    assert policy.agent_sql_retries == 5
    # untouched knob keeps its preset value
    assert policy.agent_max_disclosed_tables == 4


def test_session_from_policy_carries_agent_budget():
    conn = ConnectionConfig(name="x", type="sqlite", path=":memory:")
    policy = ResourcePolicy(agent_max_steps=7, agent_sql_retries=1, agent_max_disclosed_tables=2)
    session = Session.from_policy(conn, policy)
    assert session.agent_max_steps == 7
    assert session.agent_sql_retries == 1
    assert session.agent_max_disclosed_tables == 2


def test_runtime_step_budget_is_overridable():
    rt = AgentRuntime(max_steps=3)
    assert rt.steps_remaining == 3
    rt.consume_step()
    rt.consume_step()
    rt.consume_step()
    assert rt.steps_remaining == 0
    # default falls back to the class constant when not supplied
    assert AgentRuntime().steps_remaining == AgentRuntime.MAX_STEPS


def test_config_resource_defaults_persist_agent_budget(tmp_path):
    cfg = ConfigManager(tmp_path / "config.toml")
    cfg.set_resource_defaults({"agent_max_steps": 20, "agent_sql_retries": 4})
    # round-trip through disk
    reloaded = ConfigManager(tmp_path / "config.toml")
    conn = ConnectionConfig(name="y", type="sqlite", path=":memory:")
    policy = reloaded.policy_for(conn)
    assert policy.agent_max_steps == 20
    assert policy.agent_sql_retries == 4
