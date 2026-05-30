from __future__ import annotations

import re
from dataclasses import dataclass

from dbaide.models import ColumnInfo, TableInfo


ALIASES: dict[str, list[str]] = {
    "用户": ["user", "users", "account", "member", "customer", "client"],
    "客户": ["customer", "client", "user", "member"],
    "订单": ["order", "orders", "purchase", "sale"],
    "商品": ["product", "sku", "item", "goods"],
    "支付": ["payment", "pay", "transaction", "paid"],
    "金额": ["amount", "price", "total", "fee", "cost", "money"],
    "时间": ["time", "date", "created_at", "updated_at", "created", "day"],
    "状态": ["status", "state", "type"],
    "数量": ["count", "num", "qty", "quantity"],
    "日志": ["log", "event", "events", "trace"],
    "文章": ["post", "article", "blog"],
    "评论": ["comment", "review", "message"],
    "邮箱": ["email", "mail", "e_mail"],
    "电话": ["phone", "mobile", "tel", "telephone"],
    "手机": ["phone", "mobile", "cellphone"],
    "名称": ["name", "title", "label"],
    "地址": ["address", "addr", "location"],
}


@dataclass(slots=True)
class ScoredTable:
    table: TableInfo
    score: float
    reasons: list[str]


@dataclass(slots=True)
class ScoredColumn:
    table: str
    column: ColumnInfo
    score: float
    reasons: list[str]


class CatalogMatcher:
    def score_tables(self, query: str, tables: list[TableInfo], *, limit: int = 8) -> list[ScoredTable]:
        tokens = _query_tokens(query)
        expanded = _expand_tokens(tokens)
        scored: list[ScoredTable] = []
        for table in tables:
            haystack = " ".join([table.name, table.comment or ""]).lower()
            score, reasons = _score_text(haystack, tokens, expanded)
            if score <= 0:
                score = 0.1
                reasons = ["fallback candidate"]
            scored.append(ScoredTable(table=table, score=score, reasons=reasons))
        scored.sort(key=lambda x: (-x.score, x.table.name))
        return scored[:limit]

    def score_columns(self, query: str, table: str, columns: list[ColumnInfo], *, limit: int = 12) -> list[ScoredColumn]:
        tokens = _query_tokens(query)
        expanded = _expand_tokens(tokens)
        scored: list[ScoredColumn] = []
        for column in columns:
            haystack = " ".join([column.name, column.data_type, column.comment or ""]).lower()
            score, reasons = _score_text(haystack, tokens, expanded)
            if column.primary_key:
                score += 0.15
            if column.indexed:
                score += 0.1
            if score > 0:
                scored.append(ScoredColumn(table=table, column=column, score=score, reasons=reasons))
        scored.sort(key=lambda x: (-x.score, x.column.name))
        return scored[:limit]


def _query_tokens(text: str) -> list[str]:
    ascii_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text.lower())
    zh_tokens = [key for key in ALIASES if key in text]
    return list(dict.fromkeys(ascii_tokens + zh_tokens))


def _expand_tokens(tokens: list[str]) -> set[str]:
    expanded: set[str] = set(tokens)
    for token in tokens:
        if token in ALIASES:
            expanded.update(ALIASES[token])
        for zh, words in ALIASES.items():
            if token in words:
                expanded.add(zh)
                expanded.update(words)
    return expanded


def _score_text(haystack: str, tokens: list[str], expanded: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for token in tokens:
        if token and token.lower() in haystack:
            score += 2.0
            reasons.append(f"name/comment match: {token}")
    for token in expanded:
        if token and token.lower() in haystack:
            score += 1.0
            if len(reasons) < 4:
                reasons.append(f"alias match: {token}")
    return score, reasons
