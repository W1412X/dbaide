"""SemanticClarifier: turns an LLM ambiguity analysis into a ClarificationPlan;
no-LLM / empty schema → no clarification; renders a user-facing prompt."""

from dbaide.agent.clarify import ClarificationPlan, SemanticClarifier
from dbaide.llm import LLMClient, NullLLMClient
from dbaide.models import ColumnInfo


class _Mock(LLMClient):
    def __init__(self, payload):
        self._payload = payload
        self.seen = {}

    def complete_json(self, messages, *, schema_hint=""):
        self.seen["system"] = messages[0].content
        self.seen["user"] = messages[-1].content
        return self._payload

    def complete_text(self, messages):
        return "OK"


def _disclosed():
    return [("analysis", "orders", [
        ColumnInfo(name="id", data_type="int", primary_key=True),
        ColumnInfo(name="created_at", data_type="timestamp"),
        ColumnInfo(name="status", data_type="varchar"),
        ColumnInfo(name="refunded_at", data_type="timestamp", nullable=True),
    ])]


def test_no_llm_means_no_clarification():
    plan = SemanticClarifier(NullLLMClient()).analyze("refund rate last month", _disclosed())
    assert plan.is_empty()


def test_empty_schema_means_no_clarification():
    plan = SemanticClarifier(_Mock({"questions": [{"ask": "x", "options": ["a"]}]})).analyze("q", [])
    assert plan.is_empty()


def test_parses_questions_and_assumptions():
    mock = _Mock({
        "questions": [
            {"dimension": "time", "ask": "created_at is UTC — which timezone for 'last month'?",
             "options": ["UTC", "America/New_York"], "default": "UTC"},
            {"dimension": "metric", "ask": "Refunds: any refund or only post-delivery?",
             "options": ["Any refund", "Post-delivery only"], "default": "Any refund"},
        ],
        "assumptions": ["Excluding soft-deleted rows (del_flag='0')"],
    })
    plan = SemanticClarifier(mock).analyze("refund rate last month in NY time", _disclosed())
    assert len(plan.questions) == 2
    assert plan.first_options() == ["UTC", "America/New_York"]
    assert plan.assumptions == ["Excluding soft-deleted rows (del_flag='0')"]
    # the schema digest reached the model (so it can reason about created_at being a timestamp)
    assert "created_at" in mock.seen["user"] and "timestamp" in mock.seen["user"]


def test_render_question_lists_each_ambiguity():
    plan = ClarificationPlan(questions=[
        {"ask": "Which timezone?", "options": ["UTC", "NY"]},
        {"ask": "Which refunds count?", "options": ["all", "post-delivery"]},
    ])
    text = plan.render_question()
    assert "Which timezone?" in text and "Which refunds count?" in text
    assert "`UTC`" in text and "(default)" not in text  # options shown, no presumed default


def test_open_question_without_options():
    plan = ClarificationPlan(questions=[{"ask": "Which value of delivery_status means 妥投?", "options": []}])
    text = plan.render_question()
    assert "妥投" in text and "Options:" not in text  # open question, no invented candidates


def test_observed_values_reach_the_model():
    mock = _Mock({"questions": [], "assumptions": []})
    SemanticClarifier(mock).analyze(
        "yesterday's delivered count", _disclosed(),
        observed_values={"orders.status": ["delivered", "in_transit", "returned"]},
    )
    assert "delivered" in mock.seen["user"] and "in_transit" in mock.seen["user"]


def test_already_confirmed_is_passed_so_it_is_not_reasked():
    mock = _Mock({"questions": [], "assumptions": []})
    SemanticClarifier(mock).analyze(
        "q", _disclosed(), already_confirmed=["Timezone: America/New_York"],
    )
    assert "do NOT ask these again" in mock.seen["user"]
    assert "America/New_York" in mock.seen["user"]


def test_dropped_when_no_ask_text():
    plan = SemanticClarifier(_Mock({"questions": [{"options": ["a"]}, {"ask": "real?", "options": []}]})).analyze(
        "q", _disclosed()
    )
    assert [q["ask"] for q in plan.questions] == ["real?"]


def test_malformed_payload_is_safe():
    assert SemanticClarifier(_Mock(None)).analyze("q", _disclosed()).is_empty()
    assert SemanticClarifier(_Mock("nope")).analyze("q", _disclosed()).is_empty()
