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

from typing import Callable

DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

# id → {lang: text}. Keep ids stable; English is the fallback.
_STRINGS: dict[str, dict[str, str]] = {
    # Tabs / panels
    "tab.data": {"en": "Data", "zh": "数据"},
    "mode.assistant": {"en": "Chat", "zh": "对话"},
    "mode.workbench": {"en": "Workbench", "zh": "工作台"},
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
    "message.copy_selection": {"en": "Copy selection", "zh": "复制选中内容"},
    "message.copy_message": {"en": "Copy message", "zh": "复制整条消息"},
    "trace.workflow": {"en": "Workflow", "zh": "执行流程"},
    "trace.idle": {"en": "Idle", "zh": "空闲"},
    "trace.done": {"en": "Done", "zh": "完成"},
    "trace.failed": {"en": "Failed", "zh": "失败"},
    "trace.running": {"en": "Running", "zh": "运行中"},
    "trace.waiting": {"en": "Waiting", "zh": "等待中"},
    "trace.steps": {"en": "{n} steps", "zh": "{n} 步"},
    "trace.step": {"en": "Step {n}", "zh": "第 {n} 步"},
    "trace.thinking": {"en": "Thinking", "zh": "思考中"},
    "trace.llm_call": {"en": "LLM call: {stage}", "zh": "模型调用：{stage}"},
    "trace.intent": {"en": "Decomposing intent", "zh": "意图分解中"},
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
    "trace.phase.profile_table": {"en": "Profiling table", "zh": "分析表数据"},
    "trace.phase.column_stats": {"en": "Profiling columns", "zh": "分析字段数据"},
    "trace.phase.ask_user": {"en": "Waiting for user", "zh": "等待用户确认"},
    "trace.phase.build_assets": {"en": "Building assets", "zh": "构建资产"},
    "trace.phase.loop": {"en": "Agent loop", "zh": "智能体循环"},
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
    "res.max_concurrent_runs": {"en": "Max concurrent runs", "zh": "最大并发运行数"},
    "res.per_run_note": {
        "en": "How many sessions may run at once. The limits below apply to each run individually.",
        "zh": "最多同时运行多少个会话。下面的限制对每个运行单独生效。",
    },
    # Common buttons
    "btn.save": {"en": "Save", "zh": "保存"},
    "btn.cancel": {"en": "Cancel", "zh": "取消"},
    "btn.test": {"en": "Test", "zh": "测试"},
    "btn.build": {"en": "Build", "zh": "构建"},
    "btn.new": {"en": "New", "zh": "新建"},
    "btn.create": {"en": "Create", "zh": "创建"},
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
    "toast.no_databases": {"en": "No databases found on this connection", "zh": "该连接下未发现数据库"},
    "toast.select_database": {"en": "Select at least one database", "zh": "请至少选择一个数据库"},
    "toast.table_not_found": {"en": "Table not found: {table}", "zh": "未找到表：{table}"},
    "schema.open_data": {"en": "Open data", "zh": "打开数据"},
    "schema.view_doc": {"en": "View doc", "zh": "查看文档"},
    "schema.enrich": {"en": "Enrich doc (summary + sample + profile)", "zh": "补充文档（摘要 + 采样 + 画像）"},
    "schema.status_base": {
        "en": "Structure only (from the catalog). Right-click → Enrich for summary/samples.",
        "zh": "仅结构（来自 catalog）。右键 → 补充文档 可加摘要/采样。",
    },
    "schema.status_enriched": {"en": "Enriched (summary + samples)", "zh": "已补充（摘要 + 采样）"},
    "schema.status_stale": {
        "en": "Enrichment is stale — the table's structure changed. Re-enrich to refresh it.",
        "zh": "补充内容已过期——表结构已变更。重新补充以刷新。",
    },
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
    "conn.session_timezone": {"en": "Session time zone", "zh": "会话时区"},
    "conn.load_profile": {"en": "Load profile", "zh": "负载档位"},
    # Settings page headers
    "settings.connections.subtitle": {"en": "Manage database connections.", "zh": "管理数据库连接。"},
    "settings.new_connection": {"en": "New connection", "zh": "新建连接"},
    "settings.new_connection_hint": {
        "en": "Fill the form, then create the connection.",
        "zh": "填写表单后创建连接。",
    },
    "settings.models.subtitle": {
        "en": "Configure LLM providers. Switch models from the composer.",
        "zh": "配置 LLM 提供方。可在输入栏切换模型。",
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
    "ask.copy_sql": {"en": "Copy SQL", "zh": "复制 SQL"},
    "ask.open_in_sql": {"en": "Open in SQL", "zh": "在 SQL 中打开"},
    "ask.open_in_sql_tooltip": {"en": "Open this query in the SQL tab", "zh": "在 SQL 标签页中打开此查询"},
    "ask.copy_cli": {"en": "Copy CLI", "zh": "复制 CLI"},
    # ── Conversation inline labels ───────────────────────────────────────
    "conversation.warnings": {"en": "Warnings", "zh": "警告"},
    "conversation.notes": {"en": "Notes", "zh": "备注"},
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
    # ── Connection dialog ────────────────────────────────────────────────
    "conn.browse_title": {"en": "Select SQLite database", "zh": "选择 SQLite 数据库"},
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
        return key
    text = entry.get(_current) or entry.get(DEFAULT_LANGUAGE) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
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
