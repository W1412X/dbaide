"""Tests for phase labels / step numbers in the progress event model."""

from __future__ import annotations

from dbaide.agent.progress_events import (
    agent_label,
    phase_for,
    progress_event,
)


def test_phase_for_known_stages():
    assert phase_for("execute_sql") == "Running query"
    assert phase_for("generate_sql") == "Writing SQL"
    assert phase_for("discover_schema") == "Exploring schema"
    assert phase_for("describe_table") == "Reading tables"
    assert phase_for("validate_joins") == "Mapping relations"
    assert phase_for("unknown_stage") == ""


def test_agent_label_friendly_names():
    assert agent_label("sql_writer") == "SQL writer"
    assert agent_label("join_validate") == "Join validation"
    # Unknown agents get a title-cased fallback.
    assert agent_label("custom_thing") == "Custom Thing"
    assert agent_label("") == ""


def test_progress_event_injects_phase_and_step():
    ev = progress_event(stage="execute_sql", title="Calling execute_sql", status="running", kind="tool", step=3)
    assert ev["phase"] == "Running query"
    assert ev["step"] == 3


def test_progress_event_explicit_phase_wins():
    ev = progress_event(stage="loop", title="x", phase="Custom phase")
    assert ev["phase"] == "Custom phase"


def test_progress_event_no_step_when_zero():
    ev = progress_event(stage="loop", title="x")
    assert "step" not in ev
