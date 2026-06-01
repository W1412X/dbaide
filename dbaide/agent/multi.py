from __future__ import annotations

from dataclasses import dataclass

from dbaide.adapters import build_adapter
from dbaide.agent.assistant import DataAssistant
from dbaide.db.policy import resolve_policy
from dbaide.llm import LLMClient
from dbaide.models import AssistantResponse, ConnectionConfig
from dbaide.session import Session


@dataclass(slots=True)
class InstanceTarget:
    config: ConnectionConfig
    database: str = ""


class MultiInstanceAssistant:
    """
    Fan-out coordinator for multiple database instances.

    This intentionally keeps per-instance execution isolated: every instance has its
    own adapter and assistant, while the final response merges answers and disclosure
    events. Cross-instance joins are not attempted.
    """

    def __init__(self, targets: list[InstanceTarget], llm: LLMClient | None = None, *, default_limit: int = 100, timeout_seconds: int = 10) -> None:
        if not targets:
            raise ValueError("MultiInstanceAssistant requires at least one target")
        self.targets = targets
        self.llm = llm
        self.default_limit = default_limit
        self.timeout_seconds = timeout_seconds

    def ask(self, question: str, *, execute: bool = True) -> AssistantResponse:
        answers: list[str] = []
        disclosures: list[str] = []
        warnings: list[str] = []
        first_sql = ""
        first_result = None
        for target in self.targets:
            policy = resolve_policy(
                load_profile=getattr(target.config, "load_profile", "production"),
                instance=target.config.name,
            )
            adapter = build_adapter(target.config, policy=policy, caller="agent")
            session = Session(
                connection=target.config,
                default_limit=self.default_limit,
                timeout_seconds=self.timeout_seconds,
            )
            assistant = DataAssistant(adapter, session, self.llm)
            try:
                response = assistant.ask(question, database=target.database, execute=execute)
            except Exception as exc:
                answers.append(f"## {target.config.name}\nFailed: {exc}")
                continue
            answers.append(f"## {target.config.name}{('.' + target.database) if target.database else ''}\n{response.answer}")
            disclosures.extend(response.disclosures)
            warnings.extend(response.warnings)
            if not first_sql and response.sql:
                first_sql = response.sql
            if first_result is None and response.result is not None:
                first_result = response.result
        return AssistantResponse(
            answer="\n\n".join(answers),
            sql=first_sql,
            result=first_result,
            disclosures=disclosures,
            warnings=list(dict.fromkeys(warnings)),
        )

