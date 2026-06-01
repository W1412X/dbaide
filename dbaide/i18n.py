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
    "panel.queries": {"en": "SQL Log", "zh": "SQL 日志"},
    "queries.empty": {"en": "Every SQL the system runs appears here.", "zh": "系统执行的每一条 SQL 都会显示在这里。"},
    "queries.cleared": {"en": "(no queries yet)", "zh": "（暂无查询）"},
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
        "en": "Hard limits that keep database load negligible. Values shown are the connection's load-profile defaults; change one to override it.",
        "zh": "将数据库负载控制到极低的硬性限制。显示的是连接负载档位的默认值，修改某项即为覆盖该默认值。",
    },
    "settings.restart_required": {
        "en": "Language will apply after you restart DBAide.",
        "zh": "语言将在重启 DBAide 后生效。",
    },
    # Resources page field labels
    "res.max_inflight_queries": {"en": "Max concurrent queries", "zh": "最大并发查询数"},
    "res.statement_timeout_seconds": {"en": "Statement timeout (s)", "zh": "语句超时（秒）"},
    "res.build_max_workers": {"en": "Build workers", "zh": "构建并发数"},
    "res.default_row_limit": {"en": "Default row limit", "zh": "默认行数上限"},
    "res.max_row_limit": {"en": "Max row limit (hard cap)", "zh": "最大行数（硬上限）"},
    "res.agent_max_steps": {"en": "Agent step budget", "zh": "Agent 步数预算"},
    "res.agent_sql_retries": {"en": "SQL retry budget", "zh": "SQL 重试预算"},
    "res.agent_max_disclosed_tables": {"en": "Max tables explored", "zh": "最大探查表数"},
    "res.big_table_rows": {"en": "Big-table threshold (rows)", "zh": "大表阈值（行）"},
    "res.explain_max_rows": {"en": "EXPLAIN cost gate (rows)", "zh": "EXPLAIN 成本闸（行）"},
    "res.max_join_tables": {"en": "Max joined tables", "zh": "最大关联表数"},
    "res.join_sample_size": {"en": "Join sample size (rows)", "zh": "关联采样行数"},
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
    "toast.assets_built": {"en": "Assets built", "zh": "资产已构建"},
    "toast.no_databases": {"en": "No databases found on this connection", "zh": "该连接下未发现数据库"},
    "toast.select_database": {"en": "Select at least one database", "zh": "请至少选择一个数据库"},
    "toast.db_scope": {"en": "Database scope: {scope}", "zh": "数据库范围：{scope}"},
    "toast.model": {"en": "Model: {name}", "zh": "模型：{name}"},
    "toast.waiting_reply": {"en": "Waiting for your reply", "zh": "等待你的回复"},
    "toast.connection_ok": {"en": "Connection OK", "zh": "连接正常"},
    # SQL tab
    "sql.run": {"en": "Run", "zh": "运行"},
    "sql.run_tooltip": {"en": "Run read-only query", "zh": "运行只读查询"},
    "sql.validate": {"en": "Validate SQL", "zh": "校验 SQL"},
    "sql.explain": {"en": "Explain SQL", "zh": "解释 SQL"},
    "sql.placeholder": {
        "en": "Paste SQL here. Only single read-only statements are allowed.",
        "zh": "在此粘贴 SQL，仅允许单条只读语句。",
    },
    # Sidebar
    "sidebar.filter": {"en": "Filter tree · Enter to semantic search", "zh": "筛选结构树 · 回车进行语义搜索"},
    # Ask tab empty state
    "ask.open_settings": {"en": "Open Settings", "zh": "打开设置"},
    "ask.empty_title": {"en": "Connect your first database", "zh": "连接你的第一个数据库"},
    "ask.empty_subtitle": {
        "en": "Open Settings to add a connection and configure the model.",
        "zh": "打开设置以添加连接并配置模型。",
    },
    "note.error": {"en": "Error", "zh": "错误"},
    "note.assets_built": {"en": "Assets built", "zh": "资产已构建"},
    # Build dialog
    "build.title": {"en": "Build Assets", "zh": "构建资产"},
    "build.hint": {
        "en": "Unchecked databases keep their existing offline assets.",
        "zh": "未勾选的数据库将保留其现有的离线资产。",
    },
    "build.select_for": {"en": "Select databases to build for `{conn}`", "zh": "选择要为 `{conn}` 构建的数据库"},
    "build.select_all": {"en": "Select all", "zh": "全选"},
    "build.select_none": {"en": "Select none", "zh": "全不选"},
    "build.profile_depth": {"en": "Profile depth", "zh": "画像深度"},
    "build.concurrency": {"en": "Concurrency (workers)", "zh": "并发数（worker）"},
    "build.time_budget": {"en": "Total time budget", "zh": "总时间预算"},
    "build.time_suffix": {"en": " s  (0 = unlimited)", "zh": " 秒（0 = 不限）"},
    "build.profile_note": {
        "en": "Connection load profile: {profile}. Large tables auto-fall back to metadata-only profiling.",
        "zh": "连接负载档位：{profile}。大表会自动降级为仅元数据画像。",
    },
    # Connection dialog
    "conn.browse": {"en": "Browse…", "zh": "浏览…"},
    "conn.add_title": {"en": "Add Connection", "zh": "添加连接"},
    "conn.name": {"en": "Name", "zh": "名称"},
    "conn.type": {"en": "Type", "zh": "类型"},
    "conn.sqlite_path": {"en": "SQLite path", "zh": "SQLite 路径"},
    "conn.host": {"en": "Host", "zh": "主机"},
    "conn.port": {"en": "Port", "zh": "端口"},
    "conn.database": {"en": "Database", "zh": "数据库"},
    "conn.user": {"en": "User", "zh": "用户"},
    "conn.password": {"en": "Password", "zh": "密码"},
    "conn.load_profile": {"en": "Load profile", "zh": "负载档位"},
    # Settings page headers
    "settings.connections.subtitle": {"en": "Manage database connections.", "zh": "管理数据库连接。"},
    "settings.models.subtitle": {
        "en": "Configure LLM providers. Switch models from the composer.",
        "zh": "配置 LLM 提供方。可在输入栏切换模型。",
    },
    # Model form
    "model.profile": {"en": "Profile", "zh": "配置名"},
    "model.provider": {"en": "Provider", "zh": "提供方"},
    "model.base_url": {"en": "Base URL", "zh": "Base URL"},
    "model.api_key": {"en": "API Key", "zh": "API Key"},
    "model.model_id": {"en": "Model ID", "zh": "模型 ID"},
    "model.timeout": {"en": "Timeout (s)", "zh": "超时（秒）"},
    # Right-panel header menu
    "toast.join_saved": {"en": "Join saved", "zh": "关联已保存"},
    "toast.join_updated": {"en": "Join updated", "zh": "关联已更新"},
    "toast.join_deleted": {"en": "Join deleted", "zh": "关联已删除"},
    "toast.enter_question": {"en": "Enter a question first", "zh": "请先输入问题"},
    "toast.enter_reply": {"en": "Enter a reply first", "zh": "请先输入回复"},
    "panel.copy_trace": {"en": "Copy trace", "zh": "复制轨迹"},
    "panel.clear_trace": {"en": "Clear trace", "zh": "清空轨迹"},
    "panel.clear_conversation": {"en": "Clear conversation", "zh": "清空对话"},
    "menu.history": {"en": "History…", "zh": "历史记录…"},
    "menu.joins": {"en": "Saved joins…", "zh": "已保存的关联…"},
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
