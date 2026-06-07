"""Data assistant — thin facade over AskOrchestrator."""

from __future__ import annotations

from typing import Callable

from dbaide.adapters.base import DatabaseAdapter
from dbaide.agent.orchestrator import AgentContext, AgentStep, AskOrchestrator, format_inspect
from dbaide.assets import AssetStore
from dbaide.joins import JoinCatalogStore
from dbaide.llm import LLMClient
from dbaide.models import AssistantResponse
from dbaide.session import Session

__all__ = [
    "AgentContext",
    "AgentStep",
    "DataAssistant",
    "format_inspect",
]


class DataAssistant:
    """Ask-only database assistant. Delegates to AskOrchestrator."""

    def __init__(
        self,
        adapter: DatabaseAdapter,
        session: Session,
        llm: LLMClient | None = None,
        *,
        asset_store: AssetStore | None = None,
        join_catalog: JoinCatalogStore | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self._orchestrator = AskOrchestrator(
            adapter,
            session,
            llm,
            asset_store=asset_store,
            join_catalog=join_catalog,
            progress=progress,
        )
        self.adapter = adapter
        self.session = session
        self.instance = session.connection.name
        self.asset_store = asset_store or AssetStore()

    def ask(
        self,
        question: str,
        *,
        database: str = "",
        execute: bool = True,
        resume_state: dict | None = None,
        user_reply: str = "",
    ) -> AssistantResponse:
        return self._orchestrator.run(
            question,
            database=database,
            execute=execute,
            resume_state=resume_state,
            user_reply=user_reply,
        )
