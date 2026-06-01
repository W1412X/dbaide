"""Lightweight runtime internationalisation (English / 简体中文).

A single string table keyed by a stable id, plus a process-wide "current language".
``t("key")`` returns the string for the current language (falling back to English,
then the key itself). This drives both the desktop UI labels and the default
language the model answers in.

The current language is set once at startup from config (``[ui].language``) and can
be switched at runtime; callers that hold already-rendered widgets re-read ``t()``
when they retranslate.
"""

from __future__ import annotations

from typing import Callable

DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

# id → {lang: text}. Keep ids stable; English is the fallback.
_STRINGS: dict[str, dict[str, str]] = {
    # Tabs / panels
    "tab.ask": {"en": "Ask", "zh": "提问"},
    "tab.sql": {"en": "SQL", "zh": "SQL"},
    "panel.inspector": {"en": "Inspector", "zh": "检查器"},
    "panel.trace": {"en": "Trace", "zh": "执行轨迹"},
    "panel.plan": {"en": "Plan", "zh": "计划"},
    # Top bar
    "topbar.build": {"en": "Build Assets", "zh": "构建资产"},
    "topbar.settings": {"en": "Settings", "zh": "设置"},
    "topbar.refresh": {"en": "Refresh", "zh": "刷新"},
    "topbar.connection": {"en": "Connection", "zh": "连接"},
    "topbar.database": {"en": "Database", "zh": "数据库"},
    # Composer
    "composer.send": {"en": "Send", "zh": "发送"},
    "composer.stop": {"en": "Stop", "zh": "停止"},
    "composer.placeholder.ready": {
        "en": "Ask about your data, e.g. \"daily orders for the last 7 days\"",
        "zh": "用自然语言提问，例如「最近 7 天每天的订单数」",
    },
    "composer.placeholder.build": {
        "en": "Ask a question, or build assets for better accuracy",
        "zh": "直接提问，或先构建资产以提升准确度",
    },
    "composer.placeholder.no_conn": {
        "en": "Add or select a connection to start",
        "zh": "请先添加或选择一个连接",
    },
    "composer.hint": {"en": "  Enter = newline · ⌘Enter = send", "zh": "  Enter 换行 · ⌘Enter 发送"},
    "composer.placeholder.reply": {
        "en": "Reply to continue…  Enter = newline · ⌘Enter = send",
        "zh": "回复以继续…  Enter 换行 · ⌘Enter 发送",
    },
    # Settings nav / sections
    "settings.title": {"en": "Settings", "zh": "设置"},
    "settings.connections": {"en": "Connections", "zh": "连接"},
    "settings.models": {"en": "Models", "zh": "模型"},
    "settings.resources": {"en": "Resources", "zh": "资源"},
    "settings.general": {"en": "General", "zh": "通用"},
    "settings.back": {"en": "← Back", "zh": "← 返回"},
    "settings.language": {"en": "Language", "zh": "语言"},
    "settings.language.hint": {
        "en": "Interface, prompts and the model's default answer language.",
        "zh": "界面、提示以及模型默认回答所使用的语言。",
    },
    "settings.resources.title": {"en": "Resources & Safety", "zh": "资源与安全"},
    "settings.resources.subtitle": {
        "en": "Hard limits that keep database load negligible. Blank/zero uses the connection's load profile.",
        "zh": "将数据库负载控制到极低的硬性限制。留空/为 0 时使用连接的负载档位。",
    },
    # Common buttons
    "btn.save": {"en": "Save", "zh": "保存"},
    "btn.cancel": {"en": "Cancel", "zh": "取消"},
    "btn.test": {"en": "Test", "zh": "测试"},
    "btn.build": {"en": "Build", "zh": "构建"},
    "btn.delete": {"en": "Delete", "zh": "删除"},
    "btn.add": {"en": "Add", "zh": "添加"},
    "btn.reset_defaults": {"en": "Reset to defaults", "zh": "恢复默认"},
    # Status / toasts
    "status.idle": {"en": "Idle", "zh": "空闲"},
    "status.running": {"en": "Running", "zh": "运行中"},
    "status.ready": {"en": "Ready", "zh": "就绪"},
    "status.loading": {"en": "Loading…", "zh": "加载中…"},
    "status.building": {"en": "Building assets", "zh": "正在构建资产"},
    "toast.task_running": {"en": "A task is already running", "zh": "已有任务在运行"},
    "toast.cancelling": {"en": "Cancelling…", "zh": "正在取消…"},
    "toast.cancelled": {"en": "Cancelled", "zh": "已取消"},
    "toast.select_connection": {"en": "Select a connection first", "zh": "请先选择一个连接"},
    "toast.conn_saved": {"en": "Connection saved", "zh": "连接已保存"},
    "toast.conn_removed": {"en": "Connection removed", "zh": "连接已删除"},
    "toast.model_saved": {"en": "Model saved", "zh": "模型已保存"},
    "toast.model_removed": {"en": "Model removed", "zh": "模型已删除"},
    "toast.resources_saved": {"en": "Resource limits saved", "zh": "资源限制已保存"},
    "toast.language_changed": {"en": "Language updated", "zh": "语言已更新"},
}

_current = DEFAULT_LANGUAGE
_listeners: list[Callable[[str], None]] = []


def normalize(lang: str | None) -> str:
    value = str(lang or "").strip().lower()
    if value in {"zh", "zh-cn", "zh_cn", "chinese", "中文"}:
        return "zh"
    if value in {"en", "en-us", "english"}:
        return "en"
    return DEFAULT_LANGUAGE


def set_language(lang: str | None) -> None:
    global _current
    new = normalize(lang)
    if new == _current:
        return
    _current = new
    for cb in list(_listeners):
        try:
            cb(new)
        except Exception:  # a bad listener must not break language switching
            pass


def get_language() -> str:
    return _current


def t(key: str, /, **kwargs: object) -> str:
    entry = _STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key
    return text.format(**kwargs) if kwargs else text


def on_change(callback: Callable[[str], None]) -> Callable[[], None]:
    """Register a callback fired when the language changes; returns an unsubscribe."""
    _listeners.append(callback)

    def _off() -> None:
        if callback in _listeners:
            _listeners.remove(callback)

    return _off


def answer_language_directive(lang: str | None = None) -> str:
    """Instruction appended to the agent's prompt so it answers in the chosen
    language by default (the user writing in another language still wins)."""
    code = normalize(lang if lang is not None else _current)
    if code == "zh":
        return ("Respond in Simplified Chinese (简体中文) by default. "
                "If the user writes in another language, match the user's language.")
    return ("Respond in English by default. "
            "If the user writes in another language, match the user's language.")
