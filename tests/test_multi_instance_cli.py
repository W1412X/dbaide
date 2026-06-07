"""Regression: the multi-instance CLI `ask` must NOT also run a single-instance
WorkflowEngine. Previously it re-executed the query against targets[0] and
returned that instance's trace/JSON, inconsistent with the merged answer."""

import argparse

from dbaide import cli
from dbaide.agent.multi import InstanceTarget
from dbaide.core.result import WorkflowStatus
from dbaide.models import AssistantResponse, ConnectionConfig


class _StubAssistant:
    def __init__(self):
        self.calls = 0

    def ask(self, question, *, database="", execute=True):
        self.calls += 1
        return AssistantResponse(answer="## a\nok\n\n## b\nok", sql="SELECT 1", warnings=["w"])


def test_multi_instance_cli_does_not_double_execute(monkeypatch):
    targets = [
        InstanceTarget(config=ConnectionConfig(name="a", type="sqlite", path=":memory:")),
        InstanceTarget(config=ConnectionConfig(name="b", type="sqlite", path=":memory:")),
    ]
    monkeypatch.setattr(cli, "resolve_targets", lambda *a, **k: targets)
    stub = _StubAssistant()
    monkeypatch.setattr(cli, "build_any_assistant", lambda *a, **k: stub)

    def _boom(*a, **k):
        raise AssertionError("WorkflowEngine must not run on the multi-instance path")

    monkeypatch.setattr(cli, "WorkflowEngine", _boom)

    args = argparse.Namespace(
        question="q", conn="a,b", database="", limit=100, timeout=10,
    )
    result = cli.run_workflow_cli(cli.ConfigManager.__new__(cli.ConfigManager), args)

    assert stub.calls == 1  # the assistant ran exactly once; engine never instantiated
    assert result.status == WorkflowStatus.COMPLETED
    assert result.connection_name == "a, b"  # reflects the fan-out, not one instance
    assert "## a" in result.answer_markdown and "## b" in result.answer_markdown
    assert result.selected_sql == "SELECT 1"
    assert result.trace == []  # no misleading single-instance trace
