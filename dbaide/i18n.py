"""Lightweight runtime internationalisation (English / 简体中文).

A single string table keyed by a stable id, plus a process-wide "current language".
``t("key")`` returns the string for the current language (falling back to English,
then the key itself). This drives desktop UI labels. Agent answers use the
language detected from the user's current question.

The current language is set once at startup from config (``[ui].language``) and can
be switched at runtime; callers that hold already-rendered widgets re-read ``t()``
when they retranslate.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

_logger = logging.getLogger("dbaide.i18n")
_warned_keys: set[str] = set()

DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

# id → {lang: text}. Keep ids stable; English is the fallback.
_STRINGS: dict[str, dict[str, str]] = {
    # Tabs / panels
    "tab.data": {"en": "Data", "zh": "数据"},
    "mode.assistant": {"en": "Chat", "zh": "对话"},
    "mode.workbench": {"en": "Workbench", "zh": "工作台"},
    "mode.dashboards": {"en": "Dashboards", "zh": "看板"},
    "data.empty_hint": {
        "en": "Double-click a table in the schema (left) to browse its data.",
        "zh": "双击左侧结构树中的表即可浏览数据。",
    },
    "data.filter_placeholder": {"en": "WHERE filter (optional)…", "zh": "WHERE 筛选条件(可选)…"},
    "data.no_rows": {"en": "No rows", "zh": "无数据"},
    "data.rows_range": {"en": "Rows {start}–{end}", "zh": "第 {start}–{end} 行"},
    "data.rows_range_total": {"en": "Rows {start}–{end} of {total}", "zh": "第 {start}–{end} 行 / 共 {total}"},
    "data.count": {"en": "Count", "zh": "统计行数"},
    "data.open_referenced": {"en": "Open referenced row in {table}", "zh": "打开 {table} 中被引用的行"},
    "data.count_total": {"en": "{n} rows", "zh": "{n} 行"},
    "data.refresh": {"en": "Refresh", "zh": "刷新"},
    "data.loading": {"en": "Loading…", "zh": "加载中…"},
    "data.page_size": {"en": "Page size", "zh": "每页"},
    "data.prev": {"en": "Previous page", "zh": "上一页"},
    "data.next": {"en": "Next page", "zh": "下一页"},
    "data.sorted_by": {"en": "sorted by {col} {dir}", "zh": "按 {col} {dir} 排序"},
    "data.sort_asc": {"en": "Sort ascending ↑", "zh": "升序 ↑"},
    "data.sort_desc": {"en": "Sort descending ↓", "zh": "降序 ↓"},
    "data.sort_clear": {"en": "Clear sort", "zh": "取消排序"},
    "tab.doc": {"en": "Doc", "zh": "文档"},
    "tab.structure": {"en": "Structure", "zh": "结构"},
    "tab.history": {"en": "History", "zh": "历史"},
    "workbench.new_query": {"en": "New SQL editor", "zh": "新建 SQL 编辑器"},
    "workbench.query_n": {"en": "Query {n}", "zh": "查询 {n}"},
    "history.clear": {"en": "Clear", "zh": "清空"},
    "history.failed": {"en": "failed", "zh": "失败"},
    "history.rows": {"en": "{n} rows", "zh": "{n} 行"},
    "history.empty_hint": {
        "en": "Queries you run appear here. Click to load, double-click to run.",
        "zh": "运行过的查询会显示在这里。单击载入，双击运行。",
    },
    "history.empty_title": {"en": "No query history yet", "zh": "暂无查询历史"},
    "history.open_editor": {"en": "Open SQL editor", "zh": "打开 SQL 编辑器"},
    "data.empty_title": {"en": "No table open", "zh": "尚未打开表"},
    "structure.empty_title": {"en": "No structure to show", "zh": "暂无结构可显示"},
    "structure.empty_hint": {
        "en": "Double-click a table in the schema (left) to see its structure.",
        "zh": "双击左侧结构树中的表即可查看其结构。",
    },
    "structure.ddl": {"en": "CREATE statement (generated)", "zh": "CREATE 语句(自动生成)"},
    "structure.ddl_real": {"en": "CREATE statement", "zh": "CREATE 语句"},
    "structure.references": {"en": "References:", "zh": "外键引用："},
    "structure.referenced_by": {"en": "Referenced by:", "zh": "被引用："},
    "structure.indexes": {"en": "Indexes:", "zh": "索引："},
    "structure.copy_ddl": {"en": "Copy DDL", "zh": "复制 DDL"},
    # Top bar
    "topbar.build": {"en": "Build Assets", "zh": "构建资产"},
    "topbar.settings": {"en": "Settings", "zh": "设置"},
    "topbar.refresh": {"en": "Refresh", "zh": "刷新"},
    "topbar.connection": {"en": "Connection", "zh": "连接"},
    "topbar.update.tooltip": {
        "en": "Update available — download v{version}",
        "zh": "有更新可用 — 下载 v{version}",
    },
    # Composer
    "composer.send": {"en": "Send", "zh": "发送"},
    "composer.stop": {"en": "Stop", "zh": "停止"},
    "toast.run_queued": {
        "en": "Queued — starts when a run slot frees up",
        "zh": "已排队 — 有空位后自动开始",
    },
    "status.runs_active": {"en": "{n} running", "zh": "{n} 运行中"},
    "session.running": {"en": "Running…", "zh": "运行中…"},
    "composer.placeholder.ready": {
        "en": "Ask anything about your data…",
        "zh": "用自然语言提问你的数据…",
    },
    "composer.placeholder.build": {
        "en": "Ask a question, or build assets for better accuracy",
        "zh": "直接提问，或先构建资产以提升准确度",
    },
    "composer.placeholder.building": {
        "en": "Asset work in progress — please wait…",
        "zh": "资产正在更新，请稍候…",
    },
    "composer.placeholder.no_conn": {
        "en": "Add or select a connection to start",
        "zh": "请先添加或选择一个连接",
    },
    "composer.hint": {"en": "Enter = newline · ⌘Enter = send", "zh": "Enter 换行 · ⌘Enter 发送"},
    "composer.attach_tooltip": {
        "en": "Add a database/table as context",
        "zh": "添加数据库/表作为上下文",
    },
    "agent.loop_failed": {
        "en": "I couldn't complete this request. Please try rephrasing it or run it again.",
        "zh": "我没能完成这个请求，请换个说法或重试。",
    },
    "agent.loop_failed_reason": {"en": "Agent stopped: {reason}", "zh": "智能体已停止：{reason}"},
    "trace.title": {"en": "Agent trace", "zh": "智能体执行轨迹"},
    "trace.view": {"en": "View agent trace", "zh": "查看执行轨迹"},
    "trace.view_failed": {"en": "View agent trace · failed", "zh": "查看执行轨迹 · 失败"},
    "trace.copy": {"en": "Copy trace", "zh": "复制轨迹"},
    "trace.copy_raw": {"en": "Copy raw JSON", "zh": "复制原始数据"},
    "trace.show_details": {"en": "View details", "zh": "查看详情"},
    "trace.hide_details": {"en": "Hide details", "zh": "收起详情"},
    "trace.open_details": {"en": "Open detail panel", "zh": "打开详情面板"},
    "trace.substeps": {"en": "Substeps", "zh": "子步骤"},
    "trace.children": {"en": "{n} substeps", "zh": "{n} 个子步骤"},
    "trace.detail.truncated": {
        "en": "(truncated for display — {n} chars total; use Copy raw for the full event)",
        "zh": "（已为显示截断——共 {n} 字符；完整事件请用“复制原始数据”）",
    },
    "message.copy_selection": {"en": "Copy selection", "zh": "复制选中内容"},
    "message.copy_message": {"en": "Copy message", "zh": "复制整条消息"},
    "message.copy_code": {"en": "Copy code", "zh": "复制代码"},
    "trace.workflow": {"en": "Workflow", "zh": "执行流程"},
    "trace.idle": {"en": "Idle", "zh": "空闲"},
    "trace.starting": {"en": "Starting", "zh": "启动中"},
    "trace.done": {"en": "Done", "zh": "完成"},
    "trace.failed": {"en": "Failed", "zh": "失败"},
    "trace.running": {"en": "Running", "zh": "运行中"},
    "trace.waiting": {"en": "Waiting", "zh": "等待中"},
    "trace.steps": {"en": "{n} steps", "zh": "{n} 步"},
    "trace.step": {"en": "Step {n}", "zh": "第 {n} 步"},
    "trace.thinking": {"en": "Thinking", "zh": "思考中"},
    "trace.llm_call": {"en": "LLM call: {stage}", "zh": "模型调用：{stage}"},
    "trace.agenda": {"en": "Task list updated", "zh": "任务列表已更新"},
    "trace.call_tool": {"en": "Calling tool: {tool}", "zh": "调用工具：{tool}"},
    "trace.tool_done": {"en": "Tool finished: {tool}", "zh": "工具完成：{tool}"},
    "trace.subagent": {"en": "{agent}: {title}", "zh": "{agent}：{title}"},
    "trace.phase.discover_schema": {"en": "Exploring schema", "zh": "探索数据库结构"},
    "trace.phase.retrieve_schema_context": {"en": "Reading schema evidence", "zh": "读取结构证据"},
    "trace.phase.describe_table": {"en": "Reading table", "zh": "读取表结构"},
    "trace.phase.retrieve_join_context": {"en": "Mapping relations", "zh": "查找关联关系"},
    "trace.phase.validate_joins": {"en": "Validating joins", "zh": "校验关联关系"},
    "trace.phase.generate_sql": {"en": "Writing SQL", "zh": "生成 SQL"},
    "trace.phase.validate_sql": {"en": "Validating SQL", "zh": "校验 SQL"},
    "trace.phase.explain_sql": {"en": "Checking query cost", "zh": "检查查询成本"},
    "trace.phase.execute_sql": {"en": "Running SQL", "zh": "执行 SQL"},
    "trace.phase.update_agenda": {"en": "Planning work", "zh": "规划任务"},
    "trace.phase.profile_table": {"en": "Profiling table", "zh": "分析表数据"},
    "trace.phase.column_stats": {"en": "Profiling columns", "zh": "分析字段数据"},
    "trace.phase.ask_user": {"en": "Waiting for user", "zh": "等待用户确认"},
    "trace.phase.build_assets": {"en": "Building assets", "zh": "构建资产"},
    "trace.phase.loop": {"en": "Agent loop", "zh": "智能体循环"},
    "trace.phase.environment_check": {"en": "Checking environment", "zh": "检查环境"},
    "trace.phase.agent_request": {"en": "Starting agent", "zh": "启动智能体"},
    "trace.agent.schema_link": {"en": "Schema discovery", "zh": "结构发现"},
    "trace.agent.sql_writer": {"en": "SQL writer", "zh": "SQL 生成器"},
    "trace.agent.join_infer": {"en": "Join inference", "zh": "关联推断"},
    "trace.agent.join_validate": {"en": "Join validation", "zh": "关联校验"},
    "trace.agent.join_catalog": {"en": "Join catalog", "zh": "关联目录"},
    "trace.agent.risk": {"en": "Risk gate", "zh": "风险检查"},
    "trace.agent.explain": {"en": "Cost estimate", "zh": "成本估算"},
    "trace.agent.sql": {"en": "SQL", "zh": "SQL"},
    "trace.type.phase": {"en": "Phase", "zh": "阶段"},
    "trace.type.tool": {"en": "Tool", "zh": "工具"},
    "trace.type.sql": {"en": "SQL", "zh": "SQL"},
    "trace.type.llm": {"en": "Model", "zh": "模型"},
    "trace.type.decision": {"en": "Think", "zh": "思考"},
    "trace.type.io": {"en": "I/O", "zh": "输入输出"},
    "trace.field.input": {"en": "args", "zh": "输入"},
    "trace.field.output": {"en": "output", "zh": "输出"},
    "trace.field.result_data": {"en": "Structured output", "zh": "结构化输出"},
    "trace.field.llm_calls": {"en": "LLM calls", "zh": "模型调用"},
    "trace.field.prompt": {"en": "Prompt", "zh": "提示词"},
    "trace.field.response": {"en": "Response", "zh": "响应"},
    "trace.field.decision": {"en": "Decision", "zh": "决策"},
    "trace.field.thought": {"en": "Thought", "zh": "思考"},
    "trace.field.question": {"en": "question", "zh": "问题"},
    "trace.field.options": {"en": "Options", "zh": "选项"},
    "trace.field.sql": {"en": "SQL", "zh": "SQL"},
    "trace.field.status": {"en": "Status", "zh": "状态"},
    "trace.field.stage": {"en": "Stage", "zh": "阶段"},
    "trace.field.agent": {"en": "Agent", "zh": "执行者"},
    "trace.field.duration": {"en": "Duration", "zh": "耗时"},
    "trace.field.rows": {"en": "{n} rows", "zh": "{n} 行"},
    "trace.field.database": {"en": "Database", "zh": "数据库"},
    "trace.field.raw_event": {"en": "Raw event", "zh": "原始事件"},
    "dialog.close": {"en": "Close", "zh": "关闭"},
    "clarify.title": {"en": "Clarification needed", "zh": "需要澄清"},
    "clarify.progress": {"en": "Question {current} / {total}", "zh": "问题 {current} / {total}"},
    "clarify.next": {"en": "Next", "zh": "下一题"},
    "clarify.finish": {"en": "Finish", "zh": "完成"},
    "clarify.back": {"en": "Back", "zh": "上一题"},
    "clarify.type_answer": {"en": "Type your answer…", "zh": "输入你的回答…"},
    "clarify.type_multi": {
        "en": "Type your answers (one line covers all the questions)…",
        "zh": "输入你的回答（一行可覆盖所有问题）…",
    },
    "dialog.ok": {"en": "OK", "zh": "确定"},
    "dialog.cancel": {"en": "Cancel", "zh": "取消"},
    "dialog.confirm": {"en": "Confirm", "zh": "确认"},
    "risk.confirm_title": {
        "en": "This query may be expensive or risky. Confirm before executing.",
        "zh": "此查询可能较慢或存在风险。请确认是否执行。",
    },
    "risk.reason": {"en": "Reason: {reason}", "zh": "原因：{reason}"},
    "risk.estimated_rows": {
        "en": "Estimated rows from EXPLAIN: ~{rows}",
        "zh": "EXPLAIN 预估扫描行数：约 {rows}",
    },
    "risk.warnings": {"en": "Warnings:", "zh": "警告："},
    "risk.sql": {"en": "SQL:", "zh": "SQL："},
    "risk.execute_anyway": {"en": "Execute anyway", "zh": "仍然执行"},
    "risk.cancel": {"en": "Cancel", "zh": "取消"},
    "composer.attach_none": {
        "en": "No schema — build assets first",
        "zh": "暂无结构 — 请先构建资产",
    },
    "composer.placeholder.reply": {
        "en": "Reply to continue…",
        "zh": "回复以继续…",
    },
    # Settings nav / sections
    "settings.title": {"en": "Settings", "zh": "设置"},
    "settings.connections": {"en": "Connections", "zh": "连接"},
    "settings.models": {"en": "Models", "zh": "模型"},
    "settings.resources": {"en": "Resources", "zh": "资源"},
    "settings.general": {"en": "General", "zh": "通用"},
    "settings.about": {"en": "About", "zh": "关于"},
    "settings.about.subtitle": {
        "en": "Version, developer, and project links.",
        "zh": "版本、开发者与项目链接。",
    },
    "settings.about.tagline": {
        "en": "A local-first AI database assistant — ask your data in plain language, safely.",
        "zh": "本地优先的 AI 数据库助手 — 用自然语言安全地提问你的数据。",
    },
    "settings.about.version": {"en": "Version", "zh": "版本"},
    "settings.about.latest_version": {"en": "Latest release", "zh": "最新版本"},
    "settings.about.latest_checking": {"en": "Checking…", "zh": "正在检查…"},
    "settings.about.latest_up_to_date": {"en": "Up to date (v{version})", "zh": "已是最新（v{version}）"},
    "settings.about.latest_ahead": {
        "en": "Ahead of GitHub release (v{version})",
        "zh": "高于 GitHub 发布版（v{version}）",
    },
    "settings.about.latest_available": {"en": "v{version} available", "zh": "有 v{version} 可更新"},
    "settings.about.latest_unavailable": {
        "en": "Could not check (network or GitHub unavailable)",
        "zh": "无法检查（网络或 GitHub 不可用）",
    },
    "settings.about.developer": {"en": "Developer", "zh": "开发者"},
    "settings.about.license": {"en": "License", "zh": "许可证"},
    "settings.about.links": {"en": "Project links", "zh": "项目链接"},
    "settings.about.link.github": {"en": "GitHub repository", "zh": "GitHub 仓库"},
    "settings.about.link.releases": {"en": "Releases & downloads", "zh": "版本发布与下载"},
    "settings.about.link.issues": {"en": "Issues & feedback", "zh": "问题反馈"},
    "settings.about.link.readme": {"en": "Documentation (README)", "zh": "文档（README）"},
    "settings.integrations": {"en": "Integrations", "zh": "集成"},
    "settings.integrations.subtitle": {
        "en": "Register DBAide as an MCP server in AI coding tools",
        "zh": "将 DBAide 注册为 AI 编程工具的 MCP 服务器",
    },
    "settings.integrations.mode": {"en": "Mode", "zh": "模式"},
    "settings.integrations.mode.full": {
        "en": "Full (ask + tools)",
        "zh": "完整（ask + 原子工具）",
    },
    "settings.integrations.mode.ask": {
        "en": "Ask only (AI pipeline)",
        "zh": "仅 Ask（AI 全流程）",
    },
    "settings.integrations.mode.tools": {
        "en": "Tools only (atomic DB tools)",
        "zh": "仅工具（原子数据库工具）",
    },
    "settings.integrations.mode.desc": {
        "en": "Choose which tools to expose via MCP",
        "zh": "选择通过 MCP 暴露哪些工具",
    },
    "settings.integrations.install": {"en": "Install", "zh": "安装"},
    "settings.integrations.installed": {"en": "Installed", "zh": "已安装"},
    "settings.integrations.install_all": {"en": "Install All", "zh": "全部安装"},
    "settings.integrations.uninstall": {"en": "Uninstall", "zh": "卸载"},
    "settings.integrations.reinstall": {"en": "Reinstall", "zh": "重装"},
    "settings.integrations.error": {
        "en": "Integration error: {error}",
        "zh": "集成操作失败：{error}",
    },
    "settings.integrations.help_tooltip": {
        "en": "How to use integrations",
        "zh": "集成使用说明",
    },
    "settings.integrations.help.title": {
        "en": "MCP integration guide",
        "zh": "MCP 集成使用说明",
    },
    "settings.integrations.help.body": {
        "en": (
            "What it does\n"
            "• Install writes a dbaide entry under mcpServers in the tool's config file.\n"
            "• The AI assistant can then call DBAide tools via MCP.\n\n"
            "Three modes\n"
            "• Full (default): exposes both the ask tool and atomic DB tools. "
            "The AI can either ask a high-level question or directly call "
            "list_tables, execute_sql, column_stats, etc.\n"
            "• Ask only: exposes a single ask tool. Natural-language questions "
            "→ DBAide's AI pipeline handles everything (schema discovery, SQL, execution, answer). "
            "Best when the external agent doesn't need low-level DB access.\n"
            "• Tools only: exposes atomic DB tools without the AI pipeline. "
            "list_databases, list_tables, describe_table, execute_sql, validate_sql, "
            "explain_sql, column_stats, profile_table, sample_rows, etc. "
            "Best when the external agent has its own reasoning and only needs "
            "DBAide as a database toolkit.\n\n"
            "Before you install\n"
            "1. Settings → Connections: add your database connection and test it.\n"
            "2. Settings → Models: configure an LLM profile (only needed for Full or Ask mode).\n"
            "3. Recommended: build schema assets for that connection once (faster discovery).\n"
            "4. Select the mode above, then click Install or Install All.\n"
            "5. Fully quit and restart the target AI tool so it reloads MCP servers.\n\n"
            "Tips\n"
            "• Install always replaces the old entry — safe to switch modes anytime.\n"
            "• conn selects the connection name (default connection if omitted).\n"
            "• The GUI app and MCP share the same ~/.dbaide config.\n"
            "• CLI equivalent: dbaide setup claude --mode full"
        ),
        "zh": (
            "有什么用\n"
            "• 点击「安装」会在对应工具的配置文件中写入 mcpServers.dbaide。\n"
            "• AI 编程助手可通过 MCP 调用 DBAide 的工具。\n\n"
            "三种模式\n"
            "• 完整模式（默认）：同时暴露 ask 工具和原子数据库工具。"
            "AI 既可以直接提问，也可以调用 list_tables、execute_sql、column_stats 等底层工具。\n"
            "• 仅 Ask：只暴露一个 ask 工具。自然语言提问 → DBAide 的 AI 全流程处理"
            "（发现表结构、生成 SQL、只读执行、格式化回答）。适合外部 Agent 不需要直接操作数据库的场景。\n"
            "• 仅工具：只暴露原子数据库工具，不包含 AI 流程。"
            "list_databases、list_tables、describe_table、execute_sql、validate_sql、"
            "explain_sql、column_stats、profile_table、sample_rows 等。"
            "适合外部 Agent 自带推理能力、只需要 DBAide 作为数据库工具箱的场景。\n\n"
            "安装前请准备好\n"
            "1. 设置 → 连接：添加数据库连接并测试连通。\n"
            "2. 设置 → 模型：配置 LLM（仅「完整」和「仅 Ask」模式需要）。\n"
            "3. 建议：对该连接执行一次「构建资产」，加速后续 schema 发现。\n"
            "4. 在上方选择模式，然后点击「安装」或「全部安装」。\n"
            "5. 完全退出并重启目标 AI 工具，使其重新加载 MCP 配置。\n\n"
            "提示\n"
            "• 安装会自动替换旧配置——随时可以切换模式。\n"
            "• conn 为连接名称（省略则用默认连接）。\n"
            "• 桌面应用与 MCP 共用 ~/.dbaide 配置。\n"
            "• CLI 等价命令：dbaide setup claude --mode full"
        ),
    },
    "settings.back": {"en": "← Back", "zh": "← 返回"},
    "settings.theme": {"en": "Theme", "zh": "主题"},
    "settings.stream_answers": {"en": "Answers", "zh": "回答"},
    "settings.stream_answers.label": {"en": "Reveal answers progressively", "zh": "逐步显示回答"},
    "settings.debug_trace": {"en": "Debug trace", "zh": "调试轨迹"},
    "settings.debug_trace.label": {
        "en": "Capture full LLM prompts/responses (copied trace shows every stage's context)",
        "zh": "记录完整的 LLM 输入/输出（复制轨迹时包含每个阶段的上下文）",
    },
    "settings.theme.light": {"en": "Light", "zh": "浅色"},
    "settings.theme.dark": {"en": "Dark", "zh": "深色"},
    "settings.language": {"en": "Language", "zh": "语言"},
    "settings.more": {"en": "More ▾", "zh": "更多 ▾"},
    "settings.set_default": {"en": "Set as default", "zh": "设为默认"},
    "settings.remove": {"en": "Remove", "zh": "删除"},
    "settings.err.conn_name": {
        "en": "Connection name is required.",
        "zh": "请填写连接名称。",
    },
    "settings.err.select_conn_test": {
        "en": "Select or enter a connection to test.",
        "zh": "请选择或输入要测试的连接。",
    },
    "settings.err.save_conn_first": {
        "en": "Save the connection first.",
        "zh": "请先保存连接。",
    },
    "settings.confirm.remove_conn": {
        "en": "Remove connection '{name}'?",
        "zh": "删除连接「{name}」？",
    },
    "settings.err.model_name": {
        "en": "Profile name is required.",
        "zh": "请填写模型配置名称。",
    },
    "settings.err.select_model_test": {
        "en": "Select or enter a model profile to test.",
        "zh": "请选择或输入要测试的模型配置。",
    },
    "settings.err.save_model_first": {
        "en": "Save the model profile first.",
        "zh": "请先保存模型配置。",
    },
    "settings.confirm.remove_model": {
        "en": "Remove model profile '{name}'?",
        "zh": "删除模型配置「{name}」？",
    },
    "settings.language.hint": {
        "en": "Interface language. Answers follow the user's question language.",
        "zh": "界面语言。回答会跟随用户提问语言。",
    },
    "settings.restart_required": {
        "en": "This setting will apply after you restart DBAide.",
        "zh": "该设置将在重启 DBAide 后生效。",
    },
    "settings.resources.title": {"en": "Resources & Safety", "zh": "资源与安全"},
    "settings.resources.subtitle": {
        "en": "Hard limits that keep database load negligible. Values shown are the connection's load-profile defaults; change one to override it.",
        "zh": "将数据库负载控制到极低的硬性限制。显示的是连接负载档位的默认值，修改某项即为覆盖该默认值。",
    },
    # Resources page field labels
    "res.max_inflight_queries": {"en": "Max concurrent queries", "zh": "最大并发查询数"},
    "res.statement_timeout_seconds": {"en": "Statement timeout (s)", "zh": "语句超时（秒）"},
    "res.build_max_workers": {"en": "Build workers", "zh": "构建并发数"},
    "res.default_row_limit": {"en": "Default row limit", "zh": "默认行数上限"},
    "res.max_row_limit": {"en": "Large LIMIT confirmation threshold", "zh": "大 LIMIT 确认阈值"},
    "res.agent_max_steps": {"en": "Agent step budget", "zh": "Agent 步数预算"},
    "res.big_table_rows": {"en": "Big-table threshold (rows)", "zh": "大表阈值（行）"},
    "res.explain_max_rows": {"en": "EXPLAIN cost gate (rows)", "zh": "EXPLAIN 成本闸（行）"},
    "res.join_sample_size": {"en": "Join sample size (rows)", "zh": "关联采样行数"},
    "res.prior_turns_window": {"en": "Prior turns in context", "zh": "上下文中的历史轮数"},
    "res.max_batch_tools": {"en": "Max parallel tool calls", "zh": "最大并行工具调用数"},

    "res.latest_result_limit": {"en": "Latest result limit (chars, 0=unlimited)", "zh": "最新结果上限（字符，0=不限）"},
    "res.session_uncompressed_turns": {"en": "Recent turns kept uncompressed", "zh": "保持未压缩的最近轮次"},
    "res.compress_threshold": {"en": "Compress threshold (%)", "zh": "压缩触发阈值（%）"},
    "res.max_concurrent_runs": {"en": "Max concurrent runs", "zh": "最大并发运行数"},
    "res.per_run_note": {
        "en": "How many sessions may run at once. The limits below apply to each run individually.",
        "zh": "最多同时运行多少个会话。下面的限制对每个运行单独生效。",
    },
    "res.group.app": {"en": "Application", "zh": "应用"},
    "res.group.database": {"en": "Database Limits", "zh": "数据库限制"},
    "res.group.agent": {"en": "Agent Behavior", "zh": "Agent 行为"},
    "res.group.build": {"en": "Asset Build", "zh": "资产构建"},
    # Common buttons
    "btn.save": {"en": "Save", "zh": "保存"},
    "btn.cancel": {"en": "Cancel", "zh": "取消"},
    "btn.test": {"en": "Test", "zh": "测试"},
    "btn.build": {"en": "Build", "zh": "构建"},
    "btn.new": {"en": "New", "zh": "新建"},
    "btn.create": {"en": "Create", "zh": "创建"},
    "btn.copy": {"en": "Copy", "zh": "复制"},
    "btn.close": {"en": "Close", "zh": "关闭"},
    "btn.reset_defaults": {"en": "Reset to defaults", "zh": "恢复默认"},
    # Status / toasts
    "status.ready": {"en": "Ready", "zh": "就绪"},
    "status.building": {"en": "Building assets", "zh": "正在构建资产"},
    "status.syncing": {"en": "Syncing schema…", "zh": "正在同步库结构…"},
    "status.enriching": {"en": "Enriching docs…", "zh": "正在补充文档…"},
    "toast.task_running": {"en": "A task is already running", "zh": "已有任务在运行"},
    "toast.assets_busy": {
        "en": "Asset work is still running — please wait before asking",
        "zh": "资产仍在更新中，请稍后再提问",
    },
    "toast.cancelling": {"en": "Cancelling…", "zh": "正在取消…"},
    "toast.cancelled": {"en": "Cancelled", "zh": "已取消"},
    "toast.select_connection": {"en": "Select a connection first", "zh": "请先选择一个连接"},
    "toast.conn_saved": {"en": "Connection saved", "zh": "连接已保存"},
    "toast.conn_removed": {"en": "Connection removed", "zh": "连接已删除"},
    "toast.model_saved": {"en": "Model saved", "zh": "模型已保存"},
    "toast.model_removed": {"en": "Model removed", "zh": "模型已删除"},
    "toast.resources_saved": {"en": "Resource limits saved", "zh": "资源限制已保存"},
    "toast.assets_built": {"en": "Assets built", "zh": "资产已构建"},
    "toast.schema_initialized": {"en": "Structure initialized", "zh": "基础结构已初始化"},
    "toast.no_databases": {"en": "No databases found on this connection", "zh": "该连接下未发现数据库"},
    "toast.select_database": {"en": "Select at least one database", "zh": "请至少选择一个数据库"},
    "toast.table_not_found": {"en": "Table not found: {table}", "zh": "未找到表：{table}"},
    "schema.open_data": {"en": "Open data", "zh": "打开数据"},
    "schema.view_doc": {"en": "View doc", "zh": "查看文档"},
    "schema.enrich": {"en": "Enrich doc (summary + samples)", "zh": "补充文档（摘要 + 采样）"},
    "schema.status_base": {
        "en": "Structure only (from the catalog). Use ⋯ → Enrich for summary/samples.",
        "zh": "仅结构（来自 catalog）。点击 ⋯ → 补充文档 可加摘要/采样。",
    },
    "schema.status_enriched": {"en": "Enriched (summary + samples)", "zh": "已补充（摘要 + 采样）"},
    "schema.status_stale": {
        "en": "Enrichment is stale — the table's structure changed. Re-enrich to refresh it.",
        "zh": "补充内容已过期——表结构已变更。重新补充以刷新。",
    },
    "schema.asset_state.missing": {"en": "No assets", "zh": "尚无资产"},
    "schema.asset_state.base": {"en": "Structure", "zh": "基础结构"},
    "schema.asset_state.sampled": {"en": "Sampled", "zh": "已采样"},
    "schema.asset_state.partial": {"en": "Partially sampled", "zh": "部分采样"},
    "schema.asset_state.stale": {"en": "Stale enrichment", "zh": "增强过期"},
    "schema.asset_state.failed": {"en": "Build issues", "zh": "构建有错误"},
    "schema.asset_state.detail": {
        "en": "{tables} tables · {columns} columns · {sampled} sampled · profiles on demand",
        "zh": "{tables} 表 · {columns} 字段 · {sampled} 已采样 · 画像按需",
    },
    "schema.asset_state.errors": {"en": " · {errors} errors", "zh": " · {errors} 个错误"},
    "toast.enriching": {"en": "Enriching {target}…", "zh": "正在补充 {target} 的文档…"},
    "toast.enriched": {"en": "Enriched {target}", "zh": "{target} 文档已补充"},
    "toast.enrich_failed": {"en": "Enrich failed: {error}", "zh": "补充失败：{error}"},
    "schema.generate_sql": {"en": "Generate SQL", "zh": "生成 SQL"},
    "schema.gen_select_star": {"en": "SELECT *", "zh": "SELECT *"},
    "schema.gen_select_columns": {"en": "SELECT columns", "zh": "SELECT 列"},
    "schema.gen_count": {"en": "COUNT(*)", "zh": "COUNT(*)"},
    "schema.gen_insert": {"en": "INSERT template", "zh": "INSERT 模板"},
    "schema.gen_update": {"en": "UPDATE template", "zh": "UPDATE 模板"},
    "schema.copy_name": {"en": "Copy name", "zh": "复制名称"},
    "schema.copy_qualified": {"en": "Copy qualified name", "zh": "复制限定名"},
    "schema.no_assets": {"en": "No assets yet", "zh": "尚无资产"},
    "schema.loading": {"en": "Loading schema…", "zh": "正在加载库结构…"},
    "schema.projecting": {"en": "Reading schema from the database…", "zh": "正在从数据库读取库结构…"},
    "schema.no_assets_hint": {
        "en": "Build assets from the toolbar for richer answers.",
        "zh": "从工具栏构建资产以获得更准确的回答。",
    },
    "schema.load_failed": {"en": "Schema load failed: {error}", "zh": "结构加载失败：{error}"},
    "toast.model": {"en": "Model: {name}", "zh": "模型：{name}"},
    "toast.waiting_reply": {"en": "Waiting for your reply", "zh": "等待你的回复"},
    "toast.connection_ok": {"en": "Connection OK", "zh": "连接正常"},
    # SQL tab
    "sql.run": {"en": "Run", "zh": "运行"},
    "sql.running": {"en": "Running", "zh": "运行中"},
    "sql.run_hint": {"en": "⌘↵ run selection or statement at cursor", "zh": "⌘↵ 运行选中内容或光标处语句"},
    "sql.format": {"en": "Format", "zh": "格式化"},
    "sql.format_tooltip": {"en": "Format SQL (⌘⇧F)", "zh": "格式化 SQL（⌘⇧F）"},
    "sql.explain": {"en": "Explain", "zh": "执行计划"},
    "sql.explain_tooltip": {"en": "Show the query plan (EXPLAIN)", "zh": "查看查询计划（EXPLAIN）"},
    "sql.result": {"en": "Result", "zh": "结果"},
    "sql.messages": {"en": "Messages", "zh": "消息"},
    "sql.run_tooltip": {"en": "Run read-only query", "zh": "运行只读查询"},
    "sql.placeholder": {
        "en": "Write SQL here. Drag the handle below to resize. Ctrl+Space for suggestions.",
        "zh": "在此编写 SQL。拖动下方分隔条调整高度；Ctrl+Space 触发补全。",
    },
    "sql.result_truncated": {
        "en": "Result truncated to the row limit. Narrow the query with WHERE/LIMIT, or raise the limit in Settings → Resources.",
        "zh": "结果已截断至行数上限。请用 WHERE/LIMIT 缩小范围，或在 设置 → 资源 中提高限制。",
    },
    "error.llm.unconfigured": {
        "en": "No LLM configured. Open Settings → Models and add an API key.",
        "zh": "未配置 LLM。请打开 设置 → 模型 并填写 API Key。",
    },
    "error.llm.auth": {
        "en": "Model authentication failed. Check the API key and base URL in Settings → Models.",
        "zh": "模型鉴权失败。请检查 设置 → 模型 中的 API Key 与 Base URL。",
    },
    "error.llm.rate_limit": {
        "en": "Model rate limit hit. Wait a moment and try again.",
        "zh": "模型请求被限流。请稍后再试。",
    },
    "error.llm.timeout": {
        "en": "Model request timed out. Try again or increase the timeout in Settings → Models.",
        "zh": "模型请求超时。请重试，或在 设置 → 模型 中增大超时时间。",
    },
    "error.llm.ssl": {
        "en": (
            "HTTPS certificate verification failed when calling the model API. "
            "Check proxy or corporate certificates; see Settings → Models."
        ),
        "zh": (
            "调用模型 API 时 HTTPS 证书校验失败。"
            "请检查代理或公司根证书；详见 设置 → 模型。"
        ),
    },
    "error.llm.network": {
        "en": "Could not reach the model endpoint. Check network, proxy, and base URL.",
        "zh": "无法连接模型服务。请检查网络、代理与 Base URL。",
    },
    "error.llm.server": {
        "en": "Model server error. Try again in a few minutes.",
        "zh": "模型服务端错误。请稍后再试。",
    },
    "error.llm.generic": {
        "en": "Model call failed. See the trace or export a debug bundle for details.",
        "zh": "模型调用失败。可查看轨迹或导出调试包获取详情。",
    },
    # ── Non-LLM user-facing errors ────────────────────────────────────────
    "error.connection": {
        "en": "Could not connect to the database. Check the connection settings.",
        "zh": "无法连接数据库。请检查连接配置。",
    },
    "error.permission": {
        "en": "Permission denied. The database user may lack privileges for this operation.",
        "zh": "权限不足。当前数据库用户可能没有执行此操作的权限。",
    },
    "error.timeout": {
        "en": "The operation timed out. Try simplifying the query or increasing the timeout.",
        "zh": "操作超时。请尝试简化查询或增大超时设置。",
    },
    "error.sql_syntax": {
        "en": "SQL syntax error. Check the query and try again.",
        "zh": "SQL 语法错误。请检查查询语句后重试。",
    },
    "error.sql_execution": {
        "en": "Query failed: {detail}",
        "zh": "查询失败：{detail}",
    },
    "error.table_not_found": {
        "en": "Table or view not found. It may have been renamed or deleted.",
        "zh": "表或视图不存在。可能已被重命名或删除。",
    },
    "error.column_not_found": {
        "en": "Column not found. The schema may have changed — try syncing.",
        "zh": "字段不存在。表结构可能已变更，请尝试同步。",
    },
    "error.bootstrap_failed": {
        "en": "Failed to load connection data. Check your connection and try refreshing.",
        "zh": "加载连接数据失败。请检查连接配置并尝试刷新。",
    },
    "error.operation_failed": {
        "en": "Operation failed. Please try again.",
        "zh": "操作失败，请重试。",
    },
    "error.save_failed": {
        "en": "Could not save changes. Please try again.",
        "zh": "保存失败，请重试。",
    },
    "error.rename_failed": {
        "en": "Rename failed. Please try again.",
        "zh": "重命名失败，请重试。",
    },
    "error.delete_failed": {
        "en": "Delete failed. Please try again.",
        "zh": "删除失败，请重试。",
    },
    "error.generic": {
        "en": "Something went wrong. Please try again or export a debug bundle.",
        "zh": "出了点问题。请重试或导出调试包。",
    },
    "error.turn.cancelled": {
        "en": "Cancelled by user.",
        "zh": "已被用户取消。",
    },
    "error.turn.error": {
        "en": "**Error**: {message}",
        "zh": "**错误**：{message}",
    },
    # Sidebar
    "sidebar.filter": {"en": "Search schema…", "zh": "搜索结构…"},
    "sidebar.chats": {"en": "Chats", "zh": "对话"},
    "sidebar.schema": {"en": "Schema", "zh": "结构"},
    "sidebar.schema_heading": {"en": "SCHEMA", "zh": "结构"},
    "sidebar.filter.hint": {
        "en": "Filter the schema tree · press Enter for semantic search",
        "zh": "筛选结构树 · 回车进行语义搜索",
    },
    # Ask tab empty state
    "ask.open_settings": {"en": "Open Settings", "zh": "打开设置"},
    "ask.empty_title": {"en": "Connect your first database", "zh": "连接你的第一个数据库"},
    "ask.empty_subtitle": {
        "en": "Open Settings to add a connection and configure the model.",
        "zh": "打开设置以添加连接并配置模型。",
    },
    "ask.empty_model_title": {"en": "Configure a model", "zh": "配置一个模型"},
    "ask.empty_model_subtitle": {
        "en": "A connection is set. Open Settings to add an LLM (provider, base URL, key, model).",
        "zh": "连接已就绪。打开设置以添加大模型（provider、base URL、key、model）。",
    },
    "ask.empty_ready_title": {"en": "Ask anything about your data", "zh": "随便问点关于你数据的问题"},
    "ask.empty_ready_subtitle": {
        "en": "Type a question below to start a new chat.",
        "zh": "在下方输入问题即可开始新对话。",
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
    "build.concurrency": {"en": "Concurrency (workers)", "zh": "并发数（worker）"},
    "build.time_budget": {"en": "Total time budget", "zh": "总时间预算"},
    "build.time_suffix": {"en": " s  (0 = unlimited)", "zh": " 秒（0 = 不限）"},
    "build.progress_title": {"en": "Build progress", "zh": "构建进度"},
    "build.progress_for": {"en": "Building assets for {conn}", "zh": "正在为 {conn} 构建资产"},
    "build.progress_all_databases": {"en": "all visible databases", "zh": "所有可见数据库"},
    "build.progress_scope": {"en": "Scope: {databases}", "zh": "范围：{databases}"},
    "build.progress_waiting": {"en": "Preparing build…", "zh": "正在准备构建…"},
    "build.progress_discovering": {"en": "Discovering schema…", "zh": "正在发现库结构…"},
    "build.progress_tables": {"en": "{done}/{total}", "zh": "{done}/{total}"},
    "build.progress_current": {"en": "Current table: {table}", "zh": "当前表：{table}"},
    "build.progress_log": {"en": "Build log", "zh": "构建日志"},
    "build.progress_complete": {"en": "Build completed", "zh": "构建完成"},
    "build.progress_done_summary": {
        "en": "{tables} tables, {columns} columns, {queries} queries, {errors} warnings/errors",
        "zh": "{tables} 张表，{columns} 列，{queries} 次查询，{errors} 条警告/错误",
    },
    "build.progress_failed": {"en": "Build failed: {error}", "zh": "构建失败：{error}"},
    "build.progress_failed_short": {"en": "Failed", "zh": "失败"},
    # Build progress titles emitted by AssetBuilder (English keys → localized at display time)
    "build.emit.root": {"en": "Building assets · {instance}", "zh": "正在构建资产 · {instance}"},
    "build.emit.testing_conn": {"en": "Testing connection {instance}", "zh": "正在测试连接 {instance}"},
    "build.emit.discovered": {
        "en": "Discovered {count} database(s): {names}",
        "zh": "发现 {count} 个数据库：{names}",
    },
    "build.emit.listing_tables": {"en": "{database} · listing tables…", "zh": "{database} · 正在列出表…"},
    "build.emit.db_tables": {"en": "{database} · {count} tables", "zh": "{database} · {count} 张表"},
    "build.emit.skipped_budget": {
        "en": "{database}: skipped (time budget)",
        "zh": "{database}：已跳过（超出时间预算）",
    },
    "build.emit.db_progress": {
        "en": "{database} · {done}/{total} tables · {table}",
        "zh": "{database} · {done}/{total} 张表 · {table}",
    },
    "build.emit.db_summary": {
        "en": "{database} · {tables} tables · {columns} columns",
        "zh": "{database} · {tables} 张表 · {columns} 列",
    },
    "build.emit.fk_saved": {
        "en": "Saved {count} foreign-key join(s) to the catalog",
        "zh": "已保存 {count} 条外键关联至目录",
    },
    "build.emit.dry_run_start": {
        "en": "Dry-run estimate for {instance}",
        "zh": "正在为 {instance} 估算模拟运行",
    },
    "build.emit.dry_run_done": {
        "en": "Dry-run · {tables} tables · {columns} columns · ≈{queries} queries",
        "zh": "模拟运行 · {tables} 张表 · {columns} 列 · ≈{queries} 次查询",
    },
    "build.emit.summary": {
        "en": "{tables} tables · {columns} columns · {profiled} profiled{light} · {queries} queries · peak {peak}{errors}",
        "zh": "{tables} 张表 · {columns} 列 · {profiled} 已画像{light} · {queries} 次查询 · 峰值 {peak}{errors}",
    },
    "build.emit.summary_light": {"en": " · {count} light", "zh": " · {count} 轻量"},
    "build.emit.summary_errors": {"en": " · {count} errors", "zh": " · {count} 个错误"},
    "build.table.starting": {"en": "starting…", "zh": "开始…"},
    "build.table.describing": {"en": "describing…", "zh": "读取结构…"},
    "build.table.sampling": {"en": "sampling…", "zh": "采样…"},
    "build.table.counting": {"en": "counting rows…", "zh": "统计行数…"},
    "build.table.writing": {"en": "writing metadata…", "zh": "写入元数据…"},
    "build.table.queries_one": {"en": "{table} · 1 query", "zh": "{table} · 1 次查询"},
    "build.table.queries_many": {"en": "{table} · {count} queries", "zh": "{table} · {count} 次查询"},
    "build.table.queries_failed": {"en": " · {count} failed", "zh": " · {count} 失败"},
    "build.table.with_note": {"en": "{table} · {note}", "zh": "{table} · {note}"},
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
    "conn.session_timezone": {"en": "Session time zone", "zh": "会话时区"},
    "conn.load_profile": {"en": "Load profile", "zh": "负载档位"},
    "conn.sslmode": {"en": "TLS / SSL mode", "zh": "TLS/SSL 模式"},
    "conn.ssl_ca": {"en": "CA certificate", "zh": "CA 证书"},
    "conn.sslmode_tooltip": {
        "en": "TLS for remote connections. verify-ca/verify-full validate the server "
              "certificate; require encrypts without verifying. Empty = driver default.",
        "zh": "远程连接的 TLS。verify-ca/verify-full 会校验服务器证书；require 仅加密不校验。留空=驱动默认。",
    },
    "conn.ssl_ca_tooltip": {
        "en": "Path to a CA certificate bundle for verify-ca/verify-full. "
              "Leave empty to use the system/certifi trust store.",
        "zh": "verify-ca/verify-full 使用的 CA 证书路径。留空则使用系统/certifi 信任库。",
    },
    # Settings page headers
    "settings.connections.subtitle": {"en": "Manage database connections.", "zh": "管理数据库连接。"},
    "settings.export": {"en": "Export", "zh": "导出"},
    "settings.import": {"en": "Import", "zh": "导入"},
    "settings.export_conn": {"en": "Export connection", "zh": "导出连接"},
    "settings.import_conn": {"en": "Import connection…", "zh": "导入连接…"},
    "settings.export_conn_tooltip": {
        "en": "Export this connection's config, joins & notes to a JSON file.",
        "zh": "将此连接的配置、关联和备注导出为 JSON 文件。",
    },
    "settings.import_conn_tooltip": {
        "en": "Import a previously exported connection file.",
        "zh": "导入之前导出的连接文件。",
    },
    "settings.export_all": {"en": "Export all…", "zh": "导出全部…"},
    "settings.export_all_tooltip": {
        "en": "Export all connections, models and settings to a JSON file.",
        "zh": "将所有连接、模型和设置导出为 JSON 文件。",
    },
    "toast.export_ok": {"en": "Exported to {path}", "zh": "已导出到 {path}"},
    "toast.import_ok": {"en": "Imported connection: {name}", "zh": "已导入连接：{name}"},
    # Excel/CSV import — collection management
    "conn.kind_title": {"en": "New connection", "zh": "新建连接"},
    "conn.kind_hint": {"en": "What do you want to connect to?", "zh": "你想连接到什么？"},
    "conn.kind.database": {"en": "Database", "zh": "数据库"},
    "conn.kind.database_desc": {
        "en": "Connect to a MySQL, PostgreSQL or SQLite database.",
        "zh": "连接到 MySQL、PostgreSQL 或 SQLite 数据库。",
    },
    "conn.kind.excel": {"en": "Excel / CSV", "zh": "Excel / CSV"},
    "conn.kind.excel_desc": {
        "en": "Build a local read-only database from Excel/CSV files — add or remove workbooks anytime.",
        "zh": "从 Excel/CSV 文件构建一个本地只读数据库，可随时增删工作簿。",
    },
    "excel.file_filter": {
        "en": "Spreadsheets (*.xlsx *.xlsm *.csv *.tsv);;All files (*)",
        "zh": "表格文件 (*.xlsx *.xlsm *.csv *.tsv);;所有文件 (*)",
    },
    "excel.pick_title": {"en": "Choose Excel/CSV files", "zh": "选择 Excel/CSV 文件"},
    "excel.collection_title": {"en": "Excel collection · {name}", "zh": "Excel 集合 · {name}"},
    "excel.collection_hint": {
        "en": "Read-only · generated from imported files. Add or remove workbooks below.",
        "zh": "只读 · 由导入的文件生成。可在下方添加或移除工作簿。",
    },
    "excel.new_title": {"en": "New Excel collection", "zh": "新建 Excel 连接"},
    "excel.new_hint": {
        "en": "Name the connection, then add or drag in one or more files. Rename any table before creating.",
        "zh": "为连接命名，然后添加（或拖入）一个或多个文件。创建前可重命名任意表。",
    },
    "excel.reimport": {"en": "Re-import", "zh": "重新导入"},
    "excel.reimport_pick_title": {"en": "Re-select the source file", "zh": "重新选择源文件"},
    "excel.preview_btn": {"en": "Preview data", "zh": "预览数据"},
    "excel.preview_title": {"en": "Data preview · {name}", "zh": "数据预览 · {name}"},
    "excel.preview_rows": {
        "en": "{rows} rows · {cols} cols (first {limit})",
        "zh": "{rows} 行 · {cols} 列（预览前 {limit} 行）",
    },
    "excel.preview_empty": {"en": "No tables.", "zh": "没有表。"},
    "excel.conn_name": {"en": "Connection name", "zh": "连接名称"},
    "excel.conn_name_ph": {"en": "e.g. sales", "zh": "例如 sales"},
    "excel.add_files": {"en": "+ Add files", "zh": "+ 添加文件"},
    "excel.table_name_ph": {"en": "table name", "zh": "表名"},
    "excel.create": {"en": "Create", "zh": "创建"},
    "excel.no_files": {"en": "Add at least one file.", "zh": "至少添加一个文件。"},
    "excel.err.empty_name": {"en": "Every table needs a name.", "zh": "每个表都需要一个名称。"},
    "excel.err.dup_name": {"en": "Duplicate table name: {name}", "zh": "表名重复：{name}"},
    "excel.header_btn": {"en": "Header…", "zh": "选表头…"},
    "excel.header_title": {"en": "Choose the header row", "zh": "选择表头行"},
    "excel.header_hint": {
        "en": "Click the top-left header cell — its row/columns to the right become the table; cells above or to the left are skipped.",
        "zh": "点击表头左上角的单元格——其所在行及右侧各列构成表格，上方与左侧的单元格将跳过。",
    },
    "excel.header_sheet": {"en": "Sheet", "zh": "工作表"},
    "excel.header_auto": {"en": "auto-detected", "zh": "自动识别"},
    "excel.header_current": {"en": "Header: row {r}, col {c}", "zh": "表头：第 {r} 行 · 第 {c} 列"},
    "excel.header_apply_all": {"en": "Apply to all sheets", "zh": "应用到所有表"},
    "excel.header_empty_warn": {
        "en": "No header label at/after this cell — pick a header cell.",
        "zh": "这一格起没有表头文字——请选表头单元格。",
    },
    "excel.header_no_data_warn": {
        "en": "No data rows below this header.",
        "zh": "该表头下方没有数据行。",
    },
    "excel.add_title": {"en": "Add files to this collection", "zh": "向集合添加文件"},
    "excel.header_set": {"en": "Configured ✓", "zh": "已配置 ✓"},
    "excel.header_include": {"en": "Import this sheet", "zh": "导入此表"},
    "excel.header_table_name": {"en": "Table name", "zh": "表名"},
    "excel.importing": {"en": "Importing…", "zh": "正在导入…"},
    "excel.importing_title": {"en": "Importing", "zh": "导入中"},
    "excel.no_sheets_warn": {"en": "Select at least one sheet to import.", "zh": "请至少选择一个要导入的表。"},
    "excel.sheet_excluded": {"en": "This sheet will be skipped.", "zh": "此表将不导入。"},
    "excel.skipped_sheets": {
        "en": "Imported, but skipped {n} sheet(s):\n{detail}",
        "zh": "已导入，但跳过了 {n} 个工作表：\n{detail}",
    },
    "excel.rename_title": {"en": "Rename workbook", "zh": "重命名工作簿"},
    "excel.rename_prompt": {"en": "New name:", "zh": "新名称："},
    "excel.rename": {"en": "Rename", "zh": "重命名"},
    "excel.confirm_overwrite": {
        "en": "Replace existing workbook(s) of the same name ({names})? Old tables are dropped.",
        "zh": "覆盖同名的工作簿（{names}）？旧表将被删除。",
    },
    "excel.add_workbook": {"en": "+ Add Excel", "zh": "+ 添加 Excel"},
    "excel.sheet_rows": {"en": "{sheets} sheet(s) · {rows} rows", "zh": "{sheets} 个表 · {rows} 行"},
    "excel.empty": {"en": "No workbooks yet — add one.", "zh": "还没有工作簿——添加一个。"},
    "excel.remove_workbook": {"en": "Remove", "zh": "删除"},
    "excel.confirm_remove": {
        "en": "Remove “{file}” from this collection? Its tables will be dropped.",
        "zh": "从该集合移除“{file}”？其数据表将被删除。",
    },
    "excel.confirm_remove_last": {
        "en": "“{file}” is the last workbook. Removing it deletes the whole connection. Continue?",
        "zh": "“{file}”是最后一个工作簿，移除它将删除整个连接。是否继续？",
    },
    "excel.err.name_taken": {"en": "A connection named “{name}” already exists.", "zh": "已存在名为“{name}”的连接。"},
    "excel.err.bad_name": {
        "en": "The name can’t contain “/” or “\\”.",
        "zh": "名称不能包含“/”或“\\”。",
    },
    "excel.err.import_failed": {"en": "Import failed: {error}", "zh": "导入失败：{error}"},
    "toast.excel_created": {
        "en": "Imported {tables} table(s) into “{name}”",
        "zh": "已导入 {tables} 张表到“{name}”",
    },
    "toast.excel_added": {"en": "Added {file}", "zh": "已添加 {file}"},
    "toast.excel_removed": {"en": "Removed {file}", "zh": "已移除 {file}"},
    "toast.import_all_ok": {"en": "Imported {n} connection(s), {m} model(s)", "zh": "已导入 {n} 个连接、{m} 个模型"},
    "error.import_failed": {"en": "Import failed: {error}", "zh": "导入失败：{error}"},
    "error.export_failed": {"en": "Export failed: {error}", "zh": "导出失败：{error}"},
    "import.file_filter": {"en": "DBAide Export (*.json)", "zh": "DBAide 导出文件 (*.json)"},
    "import.confirm_title": {"en": "Import Connection", "zh": "导入连接"},
    "import.confirm_overwrite": {
        "en": 'Connection "{name}" already exists. Overwrite?',
        "zh": '连接 "{name}" 已存在，是否覆盖？',
    },
    "import.confirm_overwrite_full": {
        "en": "{n} connection(s) and {m} model(s) will be imported. Existing entries with the same name will be overwritten. Continue?",
        "zh": "将导入 {n} 个连接和 {m} 个模型。同名项将被覆盖。是否继续？",
    },
    "settings.new_connection": {"en": "New connection", "zh": "新建连接"},
    "settings.new_connection_hint": {
        "en": "Fill the form, then create the connection.",
        "zh": "填写表单后创建连接。",
    },
    "settings.models.subtitle": {
        "en": "Configure LLM providers. Switch models from the composer.",
        "zh": "配置 LLM 提供方。可在输入栏切换模型。",
    },
    "settings.models.ssl_note": {
        "en": (
            "HTTPS calls to your model API use the bundled Mozilla CA certificates (certifi). "
            "If you still see SSL errors, check proxy or corporate root certificates — "
            "see the startup warning or README troubleshooting."
        ),
        "zh": (
            "连接模型 API 的 HTTPS 请求使用内置的 Mozilla 根证书（certifi）。"
            "若仍出现 SSL 错误，请检查代理或公司根证书 — "
            "可参考启动时的提示或 README 故障排除说明。"
        ),
    },
    "startup.ssl.warning.title": {
        "en": "HTTPS certificate check failed",
        "zh": "HTTPS 证书校验失败",
    },
    "startup.ssl.warning.message": {
        "en": (
            "DBAide could not verify HTTPS to a public API host using the bundled CA bundle. "
            "LLM calls may fail with SSL errors.\n\n"
            "Common fixes:\n"
            "• Corporate proxy: import your organization's root certificate into the system keychain\n"
            "• macOS Python: run Install Certificates.command in your Python folder\n"
            "• Check network / VPN / firewall\n\n"
            "See Settings → Models for more about HTTPS and certifi."
        ),
        "zh": (
            "DBAide 使用内置 CA 证书包无法完成对公共 API 主机的 HTTPS 校验，"
            "LLM 调用可能出现 SSL 错误。\n\n"
            "常见处理方式：\n"
            "• 公司代理：将组织根证书导入系统钥匙串并设为信任\n"
            "• macOS Python：运行 Python 安装目录中的 Install Certificates.command\n"
            "• 检查网络 / VPN / 防火墙\n\n"
            "更多说明见 设置 → 模型 中的 HTTPS 提示。"
        ),
    },
    "settings.new_model": {"en": "New model", "zh": "新建模型"},
    "settings.new_model_hint": {
        "en": "Fill the provider, key and model id, then create the profile.",
        "zh": "填写提供方、密钥和模型 ID 后创建配置。",
    },
    # Model form
    "model.profile": {"en": "Profile", "zh": "配置名"},
    "model.provider": {"en": "Provider", "zh": "提供方"},
    "model.base_url": {"en": "Base URL", "zh": "Base URL"},
    "model.api_key": {"en": "API Key", "zh": "API Key"},
    "model.model_id": {"en": "Model ID", "zh": "模型 ID"},
    "model.timeout": {"en": "Timeout (s)", "zh": "超时（秒）"},
    "model.context_length": {"en": "Context length (k)", "zh": "上下文长度（k）"},
    # Right-panel header menu
    "toast.trace_copied": {"en": "Trace copied to clipboard", "zh": "当前轨迹已复制到剪贴板"},
    "toast.conversation_copied": {"en": "Conversation trace copied", "zh": "整个对话轨迹已复制"},
    "toast.trace_empty": {"en": "No trace to copy yet", "zh": "暂无可复制的轨迹"},
    "toast.join_saved": {"en": "Join saved", "zh": "关联已保存"},
    "toast.join_updated": {"en": "Join updated", "zh": "关联已更新"},
    "toast.join_deleted": {"en": "Join deleted", "zh": "关联已删除"},
    "toast.debug_trace_on": {"en": "Debug trace on — next query captures full LLM I/O", "zh": "调试轨迹已开启——下次查询将记录完整 LLM 输入输出"},
    "toast.debug_trace_off": {"en": "Debug trace off", "zh": "调试轨迹已关闭"},
    "toast.debug_exported": {
        "en": "Debug bundle saved to {path}",
        "zh": "调试包已保存至 {path}",
    },
    "toast.note_saved": {"en": "Note saved", "zh": "备注已保存"},
    "toast.note_deleted": {"en": "Note deleted", "zh": "备注已删除"},
    "toast.enter_question": {"en": "Enter a question first", "zh": "请先输入问题"},
    "toast.enter_reply": {"en": "Enter a reply first", "zh": "请先输入回复"},
    "panel.copy_conversation": {"en": "Copy conversation trace", "zh": "复制整个对话轨迹"},
    "menu.joins": {"en": "Saved joins…", "zh": "已保存的关联…"},
    "menu.export_debug": {"en": "Export debug bundle…", "zh": "导出调试包…"},
    "menu.sync_schema": {"en": "Sync schema with database", "zh": "与数据库同步库结构"},
    "toast.syncing": {"en": "Syncing schema with the database…", "zh": "正在与数据库同步库结构…"},
    "toast.synced": {"en": "Schema synced · {summary}", "zh": "库结构已同步 · {summary}"},
    "toast.sync_failed": {"en": "Sync failed: {error}", "zh": "同步失败：{error}"},
    # Object annotations (user notes on db/table/column) — edited from the schema
    # tree's pencil icon, displayed inside the asset document.
    "schema.edit_note": {"en": "Edit note", "zh": "编辑备注"},
    "schema.more": {"en": "More", "zh": "更多"},
    "schema.refresh_node": {"en": "Update from database", "zh": "从数据库更新"},
    # Backup
    "schema.backup_table": {"en": "Backup table…", "zh": "备份表…"},
    "schema.backup_database": {"en": "Backup database…", "zh": "备份库…"},
    "backup.title": {"en": "Backup", "zh": "备份"},
    "backup.format": {"en": "Format", "zh": "格式"},
    "backup.batch_size": {"en": "Batch size", "zh": "每批行数"},
    "backup.threads": {"en": "Threads", "zh": "并行线程数"},
    "backup.start": {"en": "Start Backup", "zh": "开始备份"},
    "backup.scope.table": {"en": "Table", "zh": "表"},
    "backup.scope.database": {"en": "Database", "zh": "库"},
    "backup.running": {"en": "Backing up {target}…", "zh": "正在备份 {target}…"},
    "backup.done": {"en": "Backup complete: {count} table(s), {rows} rows", "zh": "备份完成：{count} 个表，{rows} 行"},
    "backup.failed": {"en": "Backup failed: {error}", "zh": "备份失败：{error}"},
    "backup.manager": {"en": "Backups", "zh": "备份管理"},
    "backup.empty": {"en": "No backups yet. Use the ⋯ menu on a table or database to back up.", "zh": "暂无备份。点击表或库的 ⋯ 菜单即可备份。"},
    "backup.delete": {"en": "Delete", "zh": "删除"},
    "backup.delete_confirm": {"en": "Delete this backup? The file will be removed.", "zh": "删除此备份？文件将被移除。"},
    "backup.col.table": {"en": "Table", "zh": "表"},
    "backup.col.database": {"en": "Database", "zh": "库"},
    "backup.col.date": {"en": "Date", "zh": "时间"},
    "backup.col.rows": {"en": "Rows", "zh": "行数"},
    "backup.col.size": {"en": "Size", "zh": "大小"},
    "backup.col.format": {"en": "Format", "zh": "格式"},
    "backup.open_folder": {"en": "Open folder", "zh": "打开文件夹"},
    "notes.edit_title": {"en": "Edit note", "zh": "编辑备注"},
    "notes.editor_hint": {
        "en": "Authoritative note — shown in the document and given to the assistant at "
              "high priority. Stored separately from the asset, so a rebuild keeps it. "
              "Clear the text to remove the note.",
        "zh": "权威备注 —— 显示在文档中，并作为高优先级信息提示给助手。与 asset 分开存储，"
              "重建不会覆盖。清空内容即删除备注。",
    },
    "notes.editor_ph": {
        "en": "e.g. UTC timestamp, show +8 · this table is deprecated, use orders_v2",
        "zh": "如：UTC 时间戳，展示需 +8 · 此表已弃用，改用 orders_v2",
    },
    "notes.scope_database": {"en": "Database", "zh": "库"},
    "notes.scope_table": {"en": "Table", "zh": "表"},
    "notes.scope_column": {"en": "Column", "zh": "列"},
    # Chat sessions (会话 → 对话)
    "session.chats": {"en": "CHATS", "zh": "会话"},
    "session.new": {"en": "New chat", "zh": "新建会话"},
    "session.empty": {"en": "No chats yet — ask a question to start one.", "zh": "暂无会话 — 提问即可开启"},
    "session.rename": {"en": "Rename…", "zh": "重命名…"},
    "session.delete": {"en": "Delete", "zh": "删除"},
    "session.rename_title": {"en": "Rename chat", "zh": "重命名会话"},
    "session.title_label": {"en": "Title:", "zh": "标题："},
    "session.turns_one": {"en": "1 turn", "zh": "1 轮对话"},
    "session.turns_many": {"en": "{n} turns", "zh": "{n} 轮对话"},
    "session.just_now": {"en": "just now", "zh": "刚刚"},
    "session.minutes_ago": {"en": "{n}m ago", "zh": "{n} 分钟前"},
    "session.hours_ago": {"en": "{n}h ago", "zh": "{n} 小时前"},
    "session.days_ago": {"en": "{n}d ago", "zh": "{n} 天前"},
    # Result grid (toolbar, export menu, cell/header context menus)
    "result.export": {"en": "Export ▾", "zh": "导出 ▾"},
    "result.copy_csv": {"en": "Copy as CSV", "zh": "复制为 CSV"},
    "result.copy_json": {"en": "Copy as JSON", "zh": "复制为 JSON"},
    "result.copy_markdown": {"en": "Copy as Markdown", "zh": "复制为 Markdown"},
    "result.copy_insert": {"en": "Copy as INSERT", "zh": "复制为 INSERT"},
    "result.save_csv": {"en": "Save as CSV…", "zh": "保存为 CSV…"},
    "result.save_json": {"en": "Save as JSON…", "zh": "保存为 JSON…"},
    "result.copy_cell": {"en": "Copy cell", "zh": "复制单元格"},
    "result.copy_row": {"en": "Copy row (JSON)", "zh": "复制整行（JSON）"},
    "result.value_viewer": {"en": "Value viewer", "zh": "值查看器"},
    "result.export_title": {"en": "Export results", "zh": "导出结果"},
    "result.export_scope_title": {"en": "Export scope", "zh": "导出范围"},
    "result.export_current_page": {"en": "Current page", "zh": "当前页"},
    "result.export_all_rows": {"en": "All rows (no LIMIT)", "zh": "全部数据（不限行数）"},
    "result.export_capped": {"en": "Export capped at {n} rows (table has more).", "zh": "导出上限 {n} 行（表中还有更多数据）。"},
    "result.export_failed": {"en": "Could not write the file:\n{path}", "zh": "无法写入文件：\n{path}"},
    "result.autofit_column": {"en": "Auto-fit column", "zh": "自适应列宽"},
    "result.autofit_all": {"en": "Auto-fit all columns", "zh": "自适应所有列宽"},
    "result.showing": {
        "en": "Showing {shown} of {total} rows{suffix}{elapsed}",
        "zh": "显示 {shown} / {total} 行{suffix}{elapsed}",
    },
    "result.truncated_suffix": {"en": " · truncated", "zh": " · 已截断"},
    "result.no_results": {"en": "No results", "zh": "无结果"},
    "result.value_title": {"en": "Value", "zh": "值"},
    # ── Topbar status badge ──────────────────────────────────────────────
    "topbar.status.ready": {"en": "Ready", "zh": "就绪"},
    "topbar.status.no_assets": {"en": "No assets", "zh": "无资产"},
    "topbar.status.building": {"en": "Building", "zh": "构建中"},
    "topbar.status.idle": {"en": "Idle", "zh": "空闲"},
    # ── Ask tab / conversation actions ───────────────────────────────────
    "ask.hint": {
        "en": "Ask about your schema or data in natural language.",
        "zh": "用自然语言提问你的库结构或数据。",
    },
    "ask.search_no_results": {
        "en": "No matches for `{query}`. Try building assets or asking in natural language.",
        "zh": "未找到与 `{query}` 匹配的内容。请尝试构建资产或用自然语言提问。",
    },
    "ask.search_results": {"en": "Found {n} matches for `{query}`:", "zh": "找到 {n} 条与 `{query}` 匹配的结果："},
    "ask.copied": {"en": "Copied", "zh": "已复制"},
    "ask.copy_answer": {"en": "Copy answer", "zh": "复制回复"},
    "ask.export_answer_html": {"en": "Export HTML…", "zh": "导出 HTML…"},
    "ask.export_answer_html_hint": {
        "en": "Adjust page padding and preview the exported document, then copy or save.",
        "zh": "调整页面边距并实时预览，然后复制或保存。",
    },
    "ask.export_padding": {"en": "Page padding", "zh": "页面边距"},
    "ask.export_padding_top": {"en": "Top", "zh": "上"},
    "ask.export_padding_right": {"en": "Right", "zh": "右"},
    "ask.export_padding_bottom": {"en": "Bottom", "zh": "下"},
    "ask.export_padding_left": {"en": "Left", "zh": "左"},
    "ask.export_padding_preset_embedded": {"en": "Match chat", "zh": "与对话一致"},
    "ask.export_padding_preset_comfortable": {"en": "Comfortable", "zh": "舒适边距"},
    "ask.export_preview": {"en": "Preview", "zh": "预览"},
    "ask.export_preview_unavailable": {
        "en": "Install PyQt6-WebEngine to preview HTML export.",
        "zh": "安装 PyQt6-WebEngine 后可预览 HTML 导出。",
    },
    "ask.export_copy_html": {"en": "Copy", "zh": "复制"},
    "ask.export_save_html": {"en": "Save…", "zh": "保存…"},
    "ask.export_copied": {"en": "Copied", "zh": "已复制"},
    "ask.interactive_charts": {"en": "Chart tools…", "zh": "图表交互…"},
    "ask.pin_to_dashboard": {"en": "Pin to dashboard…", "zh": "钉到看板…"},
    "toast.pinned": {"en": "Pinned {n} chart(s) to a dashboard", "zh": "已钉 {n} 个图表到看板"},
    "board.pin_title": {"en": "Pin to dashboard", "zh": "钉到看板"},
    "board.pin_pick_charts": {"en": "Charts to pin:", "zh": "选择要钉的图表："},
    "board.pin_target": {"en": "Dashboard", "zh": "看板"},
    "board.pin_existing": {"en": "Add to an existing dashboard", "zh": "加入已有看板"},
    "board.pin_new": {"en": "Create a new dashboard", "zh": "新建看板"},
    "board.pin_new_ph": {"en": "New dashboard name", "zh": "新看板名称"},
    "board.pin_confirm": {"en": "Pin", "zh": "钉住"},
    "board.new": {"en": "New dashboard", "zh": "新建看板"},
    "board.new_prompt": {"en": "Dashboard name", "zh": "看板名称"},
    "board.rename": {"en": "Rename dashboard", "zh": "重命名看板"},
    "board.rename_prompt": {"en": "New name", "zh": "新名称"},
    "board.delete": {"en": "Delete dashboard", "zh": "删除看板"},
    "board.delete_confirm": {"en": "Delete this dashboard? Its tiles are removed; the saved questions remain.",
                             "zh": "删除这个看板？其中的图块会移除，已保存的问题仍保留。"},
    "board.refresh_all": {"en": "Refresh all", "zh": "刷新全部"},
    "board.empty": {"en": "No dashboards yet. Pin a chart from an answer to start one.",
                    "zh": "还没有看板。在回答里把图表「钉到看板」即可创建。"},
    "board.empty_board": {"en": "This dashboard has no tiles yet.", "zh": "这个看板还没有图块。"},
    "board.tile_refresh": {"en": "Refresh", "zh": "刷新"},
    "board.tile_rename_hint": {"en": "Double-click to rename", "zh": "双击重命名"},
    "board.tile_remove": {"en": "Remove from dashboard", "zh": "从看板移除"},
    "board.tile_refreshing": {"en": "Refreshing…", "zh": "刷新中…"},
    "board.tile_error": {"en": "Refresh failed: {error}", "zh": "刷新失败：{error}"},
    "board.tile_no_snapshot": {"en": "No snapshot — click refresh to load.", "zh": "暂无快照——点刷新加载。"},
    "board.tile_static": {"en": "Static snapshot", "zh": "静态快照"},
    "board.tile_updated": {"en": "Updated {when}", "zh": "更新于 {when}"},
    "ask.interactive_charts_hint": {
        "en": "Zoom and pan charts here without affecting the conversation scroll.",
        "zh": "在此缩放、拖动图表，不会影响对话区域的上下滚动。",
    },
    "ask.interactive_charts_empty": {
        "en": "This answer has no charts to interact with.",
        "zh": "这条回复里没有可交互的图表。",
    },
    "ask.copy_sql": {"en": "Copy SQL", "zh": "复制 SQL"},
    "ask.copy_cli": {"en": "Copy CLI", "zh": "复制 CLI"},
    "ask.more_actions": {"en": "More", "zh": "更多"},
    # ── Conversation inline labels ───────────────────────────────────────
    "conversation.warnings": {"en": "Warnings", "zh": "警告"},
    "conversation.notes": {"en": "Notes", "zh": "备注"},
    "conversation.code": {"en": "Code", "zh": "代码"},
    "conversation.agenda": {"en": "Task list", "zh": "任务列表"},
    "conversation.agenda_done": {"en": "Done", "zh": "完成"},
    "conversation.agenda_pending": {"en": "Pending", "zh": "待办"},
    "conversation.agenda_in_progress": {"en": "In progress", "zh": "进行中"},
    "conversation.agenda_dropped": {"en": "Dropped", "zh": "已放弃"},
    "conversation.chart": {"en": "Chart", "zh": "图表"},
    "conversation.chart_no_data": {"en": "No data to chart", "zh": "无可绘制的数据"},
    "conversation.chart_points": {"en": "{n} data points", "zh": "{n} 个数据点"},
    "conversation.chart_series": {"en": "{n} series", "zh": "{n} 条序列"},
    "conversation.chart_right_axis": {"en": "Right axis: {label}", "zh": "右轴：{label}"},
    "conversation.chart_type.bar": {"en": "Bar chart", "zh": "柱状图"},
    "conversation.chart_type.horizontal_bar": {"en": "Horizontal bar", "zh": "条形图"},
    "conversation.chart_type.line": {"en": "Line chart", "zh": "折线图"},
    "conversation.chart_type.area": {"en": "Area chart", "zh": "面积图"},
    "conversation.chart_type.pie": {"en": "Pie chart", "zh": "饼图"},
    "conversation.chart_type.donut": {"en": "Donut chart", "zh": "环形图"},
    "conversation.chart_type.stacked_bar": {"en": "Stacked bar", "zh": "堆叠柱状图"},
    "conversation.chart_type.scatter": {"en": "Scatter plot", "zh": "散点图"},
    "conversation.chart_type.combo": {"en": "Combo chart", "zh": "组合图"},
    "conversation.chart_type.grouped_bar": {"en": "Grouped bar", "zh": "分组柱状图"},
    "conversation.chart_type.stacked_area": {"en": "Stacked area", "zh": "堆叠面积图"},
    "conversation.chart_type.multi_axis_line": {"en": "Dual-axis line", "zh": "双轴折线图"},
    "conversation.chart_type.bubble": {"en": "Bubble chart", "zh": "气泡图"},
    "conversation.chart_type.radar": {"en": "Radar chart", "zh": "雷达图"},
    "conversation.chart_type.heatmap": {"en": "Heatmap", "zh": "热力图"},
    "conversation.chart_type.funnel": {"en": "Funnel chart", "zh": "漏斗图"},
    "conversation.chart_type.gauge": {"en": "Gauge", "zh": "仪表盘"},
    "conversation.chart_type.sankey": {"en": "Sankey", "zh": "桑基图"},
    "conversation.chart_type.treemap": {"en": "Treemap", "zh": "矩形树图"},
    "conversation.chart_type.sunburst": {"en": "Sunburst", "zh": "旭日图"},
    "conversation.chart_type.waterfall": {"en": "Waterfall chart", "zh": "瀑布图"},
    "conversation.chart_type.candlestick": {"en": "Candlestick", "zh": "K 线图"},
    "conversation.chart_type.boxplot": {"en": "Box plot", "zh": "箱线图"},
    "status.thinking": {"en": "Thinking…", "zh": "思考中…"},
    "status.waiting_reply": {"en": "Waiting for your reply…", "zh": "等待你的回复…"},
    # ── SQL tab messages ─────────────────────────────────────────────────
    "sql.executed_in": {"en": "Executed in {ms}ms", "zh": "执行耗时 {ms}ms"},
    # ── Build stats toast ────────────────────────────────────────────────
    "toast.build_dryrun": {"en": "≈{n} queries (dry-run)", "zh": "≈{n} 条查询（模拟运行）"},
    "toast.build_stats": {
        "en": " · {queries} queries · peak {peak}",
        "zh": " · {queries} 条查询 · 峰值 {peak}",
    },
    # ── Composer fallback ────────────────────────────────────────────────
    "composer.no_model": {"en": "No model", "zh": "无模型"},
    "composer.remove": {"en": "Remove", "zh": "移除"},
    # ── Settings busy / test status ──────────────────────────────────────
    "settings.saving_conn": {"en": "Saving connection…", "zh": "正在保存连接…"},
    "settings.saving_model": {"en": "Saving model…", "zh": "正在保存模型…"},
    "settings.testing_conn": {"en": "Testing connection…", "zh": "正在测试连接…"},
    "settings.testing_model": {"en": "Testing model…", "zh": "正在测试模型…"},
    "settings.test_ok": {"en": "OK", "zh": "成功"},
    "settings.test_failed": {"en": "Failed", "zh": "失败"},
    "settings.api_key_placeholder": {
        "en": "Leave blank to keep existing key",
        "zh": "留空以保留现有密钥",
    },
    "settings.api_key_saved": {
        "en": "API key saved · leave blank to keep",
        "zh": "API 密钥已保存 · 留空以保留",
    },
    # ── Connection dialog ────────────────────────────────────────────────
    "conn.browse_title": {"en": "Select SQLite database", "zh": "选择 SQLite 数据库"},
    "conn.password_saved": {
        "en": "Password saved · leave blank to keep",
        "zh": "密码已保存 · 留空以保留",
    },
    "conn.load_profile_tooltip": {
        "en": "production: lowest DB load (light profiling, low concurrency, strict limits).\n"
              "staging: balanced. dev: highest concurrency and limits.",
        "zh": "production：最低数据库负载（轻量画像、低并发、严格限制）。\n"
              "staging：平衡。dev：最高并发和限制。",
    },
    "conn.timezone_tooltip": {
        "en": "Session time zone applied after connecting. MySQL accepts offsets like +00:00; "
              "PostgreSQL also accepts names like UTC.",
        "zh": "连接后应用的会话时区。MySQL 接受如 +00:00 的偏移量；PostgreSQL 还接受如 UTC 的名称。",
    },
    # ── Build dialog ─────────────────────────────────────────────────────
    "build.db_built": {"en": "{name}  ·  built", "zh": "{name}  ·  已构建"},
    # ── Joins feature ────────────────────────────────────────────────────
    "join.edit_title": {"en": "Edit Join", "zh": "编辑关联"},
    "join.add_title": {"en": "Add Join", "zh": "添加关联"},
    "join.left_table": {"en": "Left table", "zh": "左表"},
    "join.left_column": {"en": "Left column", "zh": "左列"},
    "join.right_table": {"en": "Right table", "zh": "右表"},
    "join.right_column": {"en": "Right column", "zh": "右列"},
    "join.database": {"en": "Database (optional)", "zh": "数据库（可选）"},
    "join.note": {"en": "Note (optional)", "zh": "备注（可选）"},
    "join.hint": {
        "en": "User joins (0.99) · Agent-saved candidates · sorted by confidence",
        "zh": "用户定义关联 (0.99) · 智能体保存的候选 · 按置信度排序",
    },
    "join.add": {"en": "Add", "zh": "添加"},
    "join.edit": {"en": "Edit", "zh": "编辑"},
    "join.delete": {"en": "Delete", "zh": "删除"},
    "join.refresh": {"en": "Refresh", "zh": "刷新"},
    "join.empty": {
        "en": "No saved joins. Add one or run a multi-table Ask query.",
        "zh": "尚无已保存的关联。可手动添加，或运行多表提问自动发现。",
    },
    "join.fields_required": {
        "en": "All four endpoint fields are required.",
        "zh": "四个端点字段均为必填。",
    },
    "join.select_to_edit": {"en": "Select a join to edit.", "zh": "请选择要编辑的关联。"},
    "join.select_to_delete": {"en": "Select a join to delete.", "zh": "请选择要删除的关联。"},
    "join.confirm_delete": {"en": "Remove this saved join?", "zh": "删除此已保存的关联？"},
    "join.title": {"en": "Join", "zh": "关联"},
    "join.delete_title": {"en": "Delete join", "zh": "删除关联"},
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


def detect_user_language(text: str | None) -> str:
    """Detect the language the user used for this question.

    The app currently supports English and Simplified Chinese. Keep this fallback
    deterministic and conservative; the LLM intent step may provide a language field,
    but this local detector covers fast paths, null-model tests and malformed output.
    """
    value = str(text or "")
    cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in value if ("a" <= ch.lower() <= "z"))
    if cjk > 0 and cjk >= max(1, letters // 6):
        return "zh"
    return DEFAULT_LANGUAGE


def t(key: str, /, **kwargs: object) -> str:
    entry = _STRINGS.get(key)
    if not entry:
        if key not in _warned_keys:
            _warned_keys.add(key)
            _logger.debug("missing i18n key: %s", key)
        return key
    text = entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text


_TABLE_NOTE_KEYS = {
    "starting…": "build.table.starting",
    "describing…": "build.table.describing",
    "sampling…": "build.table.sampling",
    "counting rows…": "build.table.counting",
    "writing metadata…": "build.table.writing",
}


def localized_build_title(title: str) -> str:
    """Map AssetBuilder's English progress titles to the current UI language."""
    text = str(title or "").strip()
    if not text:
        return text

    m = re.match(r"^Building assets · (.+)$", text)
    if m:
        return t("build.emit.root", instance=m.group(1))
    m = re.match(r"^testing connection (.+)$", text)
    if m:
        return t("build.emit.testing_conn", instance=m.group(1))
    m = re.match(r"^discovered (\d+) database\(s\): (.+)$", text)
    if m:
        return t("build.emit.discovered", count=m.group(1), names=m.group(2))
    m = re.match(r"^saved (\d+) foreign-key join\(s\) to the catalog$", text)
    if m:
        return t("build.emit.fk_saved", count=m.group(1))
    m = re.match(r"^dry-run estimate for (.+)$", text)
    if m:
        return t("build.emit.dry_run_start", instance=m.group(1))
    m = re.match(r"^dry-run · (\d+) tables · (\d+) columns · ≈(\d+) queries$", text)
    if m:
        return t("build.emit.dry_run_done", tables=m.group(1), columns=m.group(2), queries=m.group(3))
    m = re.match(r"^(.+?) · listing tables…$", text)
    if m:
        return t("build.emit.listing_tables", database=m.group(1))
    m = re.match(r"^(.+?): skipped \(time budget\)$", text)
    if m:
        return t("build.emit.skipped_budget", database=m.group(1))
    m = re.match(r"^(.+?) · (\d+)/(\d+) tables · (.+)$", text)
    if m:
        return t("build.emit.db_progress", database=m.group(1), done=m.group(2), total=m.group(3), table=m.group(4))
    m = re.match(r"^(.+?) · (\d+) tables · (\d+) columns$", text)
    if m:
        return t("build.emit.db_summary", database=m.group(1), tables=m.group(2), columns=m.group(3))
    m = re.match(r"^(.+?) · (\d+) tables$", text)
    if m:
        return t("build.emit.db_tables", database=m.group(1), count=m.group(2))

    m = re.match(r"^(.+?) · (\d+) quer(?:y|ies)( · (\d+) failed)?$", text)
    if m:
        table, count = m.group(1), int(m.group(2))
        base = t("build.table.queries_one", table=table) if count == 1 else t(
            "build.table.queries_many", table=table, count=count
        )
        if m.group(3):
            base += t("build.table.queries_failed", count=int(m.group(4)))
        return base

    m = re.match(r"^(.+?) · (.+)$", text)
    if m:
        note_key = _TABLE_NOTE_KEYS.get(m.group(2))
        if note_key:
            return t("build.table.with_note", table=m.group(1), note=t(note_key))

    m = re.match(
        r"^(\d+) tables · (\d+) columns · (\d+) profiled"
        r"( · (\d+) light)? · (\d+) queries · peak (\d+)( · (\d+) errors)?$",
        text,
    )
    if m:
        light = t("build.emit.summary_light", count=m.group(5)) if m.group(5) else ""
        errors = t("build.emit.summary_errors", count=m.group(8)) if m.group(8) else ""
        return t(
            "build.emit.summary",
            tables=m.group(1),
            columns=m.group(2),
            profiled=m.group(3),
            light=light,
            queries=m.group(6),
            peak=m.group(7),
            errors=errors,
        )
    return text


def on_change(callback: Callable[[str], None]) -> Callable[[], None]:
    """Register a callback fired when the language changes; returns an unsubscribe."""
    _listeners.append(callback)

    def _off() -> None:
        if callback in _listeners:
            _listeners.remove(callback)

    return _off


def answer_language_directive(lang: str | None = None) -> str:
    """Instruction appended to prompts for the target answer language.

    Callers should pass the language detected from the user's current question.
    When no language is supplied, UI-owned prose uses the current interface
    language. SQL, identifiers and code are kept verbatim.
    """
    code = normalize(lang if lang is not None else _current)
    target = "Simplified Chinese (简体中文)" if code == "zh" else "English"
    return (
        f"Language: write ALL final user-facing prose for this answer — explanations, "
        f"summaries, notes and clarification questions — in {target}, because that is "
        f"the user's question language. Keep SQL, table/column identifiers and code verbatim."
    )
