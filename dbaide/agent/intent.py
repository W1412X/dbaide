"""Intent decomposition for the Ask flow.

Ask is not only Text-to-SQL: a user may ask about the schema, where a field lives,
how tables relate, a data-profile, or to diagnose/rewrite a SQL — and a single
message can bundle several of these ("show the orders schema and count paid
orders this month"). Rather than forcing one answer, we decompose the question
into INDEPENDENT, typed sub-intents, run each on its own, and aggregate — so every
sub-intent has a visible, self-contained result.

Kept lightweight on purpose (Codex-style): one LLM call, a single-intent fast
path, no dependency DAG. Sub-intents are independent and run sequentially.
"""

from __future__ import annotations

from dataclasses import dataclass

from dbaide.i18n import detect_user_language, normalize
from dbaide.llm import LLMClient, LLMMessage, NullLLMClient

# The kinds of thing Ask can answer. Used to label sub-intents and (lightly) hint
# execution. Values are display-friendly and map to how the agent already routes.
INTENT_TYPES = (
    "data_query",      # answer needs SQL over the data
    "schema_explore",  # about tables/columns/where things live
    "relations",       # how tables relate / join paths
    "data_profile",    # column stats / data-quality overview
    "sql_diagnose",    # explain / why-slow / optimize a given SQL
    "sql_rewrite",     # rewrite a given SQL
    "other",
)

_LABELS = {
    "data_query": "Data query",
    "schema_explore": "Schema",
    "relations": "Relations",
    "data_profile": "Data profile",
    "sql_diagnose": "SQL diagnose",
    "sql_rewrite": "SQL rewrite",
    "other": "Question",
}

_LABELS_ZH = {
    "data_query": "数据查询",
    "schema_explore": "结构",
    "relations": "关系",
    "data_profile": "数据画像",
    "sql_diagnose": "SQL 诊断",
    "sql_rewrite": "SQL 改写",
    "other": "问题",
}


@dataclass(slots=True)
class SubIntent:
    id: str
    type: str
    text: str
    language: str = "en"

    @property
    def label(self) -> str:
        return _LABELS.get(self.type, "Question")

    def label_for(self, language: str | None = None) -> str:
        lang = normalize(language or self.language)
        labels = _LABELS_ZH if lang == "zh" else _LABELS
        return labels.get(self.type, labels["other"])


class IntentDecomposer:
    """Split a question into independent typed sub-intents (single-intent fast path)."""

    MAX_INTENTS = 4

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or NullLLMClient()

    def decompose(self, question: str) -> list[SubIntent]:
        question = (question or "").strip()
        fallback_language = detect_user_language(question)
        if not question or isinstance(self.llm, NullLLMClient):
            return [SubIntent(id="i1", type="other", text=question, language=fallback_language)]
        try:
            payload = self.llm.complete_json(
                [
                    LLMMessage("system", _SYSTEM),
                    LLMMessage("user", f"Question:\n{question}\n\n{_INSTRUCT}"),
                ],
                schema_hint='{"intents":[{"type":"data_query","text":"...","language":"en|zh"}]}',
            )
        except Exception:
            return [SubIntent(id="i1", type="other", text=question, language=fallback_language)]

        raw = payload.get("intents") if isinstance(payload, dict) else None
        top_language = normalize(payload.get("language")) if isinstance(payload, dict) and payload.get("language") else fallback_language
        intents: list[SubIntent] = []
        for item in (raw or [])[: self.MAX_INTENTS]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            itype = str(item.get("type") or "other").strip().lower()
            if itype not in INTENT_TYPES:
                itype = "other"
            language = normalize(item.get("language")) if item.get("language") else top_language
            intents.append(SubIntent(id=f"i{len(intents) + 1}", type=itype, text=text, language=language))
        if not intents:
            return [SubIntent(id="i1", type="other", text=question, language=fallback_language)]
        return intents


_SYSTEM = (
    "You decompose a database-assistant question into INDEPENDENT sub-intents. Most "
    "questions are a SINGLE intent — return exactly one unless the user clearly asks "
    "for multiple distinct things (e.g. a schema lookup AND a data count). Never split "
    "one coherent SQL request into pieces. Each sub-intent must be self-contained and "
    "of one type. Also identify the user's question language as language ∈ {en, zh}. "
    "Return JSON only."
)

_INSTRUCT = (
    'Return {"intents":[{"type":"...","text":"a self-contained sub-question","language":"en|zh"}]}. '
    "type ∈ {data_query, schema_explore, relations, data_profile, sql_diagnose, "
    "sql_rewrite, other}. Prefer one intent. Preserve the original question language "
    "for each sub-intent unless the user explicitly mixes languages."
)
