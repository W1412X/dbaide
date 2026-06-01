# DBAide 生产级 DB Helper 全新设计文档

生成日期：2026-05-30

本文档替代单纯的 BUG 清单，目标是重新定义 DBAide 的产品定位、agent 核心架构、离线资产体系、UI 全重构方案、交互细节、异常体验和实施路径。

**最高设计标准：Claude Code / Codex。**

DBAide 的 agent 设计、工具设计、UI 设计、CLI 设计、错误体验、执行确认、任务 trace、结果展示，都应以 Claude Code / Codex 这类成熟 agent 产品为主标准：上下文优先、工具边界清晰、计划可见、执行可控、结果可验证、失败可恢复、用户始终知道 agent 正在做什么。`AskDB-Public` 只作为 Text-to-SQL 领域的参考素材，不作为产品形态和系统复杂度的蓝本。

## 1. 产品定位

DBAide 不应该定位为“另一个 AskDB”，也不应该只是一个“自然语言转 SQL 工具”。它应该定位为：

**面向开发者、数据分析师、DBA、产品运营的本地优先数据库助手。**

它的核心价值不是一次性生成 SQL，而是帮助用户安全、透明、高效地理解数据库、定位字段、构造查询、验证 SQL、解释结果、排查性能问题，并把这些过程沉淀成可读、可复用、可审计的数据库知识资产。

产品关键词：

- 本地优先：配置、资产、查询历史默认保存在本机，敏感数据不默认上传。
- 安全优先：默认只读，危险 SQL 永远不可执行，低置信 SQL 默认只生成不执行。
- 渐进式披露：不把全库 schema 一股脑塞给模型，而是按任务逐层获取实例、库、表、列、样本、统计、执行证据。
- 离线资产增强：构建可读、可搜索、可预览的数据库知识库，减少重复扫描和重复问模型。
- Agent 化协作：像 Claude Code/Codex 一样，先理解上下文，再制定计划，再调用工具，再验证结果，再向用户解释。
- 人在回路：遇到口径不明、候选冲突、执行风险时，向用户确认，而不是硬猜。
- 可观测：用户能看到 agent 在想什么阶段、用了哪些工具、为什么选择某张表、为什么拒绝某条 SQL。

不做的事情：

- 不照搬 `AskDB-Public` 的重型多意图 DAG、全套 SchemaLink、多 Agent 长链路。
- 不强制 embedding 初始化，不把向量检索作为启动门槛。
- 不把 LLM 当作唯一智能来源，所有关键边界都要有确定性校验。
- 不做“黑盒 SQL 生成器”，不能只输出一个 SQL 就结束。
- 不默认执行高风险 SQL，不能为了体验牺牲安全。

### 1.1 当前项目已有的优秀能力必须保留

重新设计不是推翻当前项目。当前 DBAide 已经有一些正确且有价值的能力，必须纳入新架构，不能在重构中丢失。

必须保留并强化的能力：

- CLI-first 基因：当前 `dbaide` 已有连接管理、ask、chat、find、tree、ddl、relations、doc、diff、sql、diagnose、assets build/show/enrich 等命令，这些能力应成为核心能力矩阵，而不是被 GUI 重构覆盖。
- 多数据库 adapter：SQLite、MySQL/MariaDB、Postgres 的 adapter 分层是正确的，未来所有 agent、工具和 UI 都必须继续通过 adapter 抽象访问数据库。
- 渐进式披露：`DisclosureContext` 的 L0-L5 披露思想正确，应升级为 workflow trace 和 context budget 的核心，而不是删除。
- 离线资产：`AssetBuilder`、`AssetStore`、`AssetSearch` 的思路正确，应继续作为 schema linking、字段检索、资产预览、文档导出的基础。
- 安全查询默认值：单语句、只读、timeout、limit、EXPLAIN preflight 的方向正确，应升级为更严格的 SQL validation pipeline。
- 无模型降级：`NullLLMClient` + heuristic fallback 的思路有价值。生产设计中应保留“无模型也能做 schema explore、find、SQL validate、简单查询”的能力。
- 多实例能力：`MultiInstanceAssistant` 的 fan-out 思路正确，应升级为可解释的多实例结果汇总，而不是移除。
- Developer tools：tree、ddl、relations、doc、diff 是 DB helper 的重要专业能力，应在 GUI 中拥有同等入口。
- 资产 profile：列级统计、top values、sample values、semantic summary 对 Text-to-SQL 很关键，应继续扩展。

因此，新的 DBAide 不是“从零做一个复杂 agent”，而是把现有 CLI/adapter/assets/guard 的基础，升级为一个更自洽、更可解释、更安全、更好用的 agent 工作台。

### 1.2 能力边界

DBAide 的核心能力分为 8 类：

1. 连接管理：添加、测试、编辑、删除、默认连接、只读能力检查。
2. 资产管理：构建、增量更新、校验、清理、预览、导出 Markdown。
3. Schema 探索：查看库、表、列、DDL、关系、字段含义、字段位置。
4. 自然语言查询：从问题到计划、SQL、校验、执行、解释。
5. SQL 工作台：校验、执行、EXPLAIN、诊断、改写、格式化、保存片段。
6. 多实例/多库辅助：fan-out 查询、schema diff、跨环境对比、结果汇总。
7. 诊断与优化：慢 SQL 分析、索引建议、扫描风险、空结果解释。
8. 历史与审计：查询历史、trace、用户确认记录、导出诊断包。

这 8 类能力必须同时出现在 CLI 和 GUI 中，只是呈现方式不同。

## 2. 设计基准：Claude Code / Codex 为主，AskDB-Public 为辅

DBAide 的所有设计判断按以下优先级排序：

1. 是否符合 Claude Code / Codex 式 agent 工作台体验。
2. 是否符合当前 DBAide 的本地优先、安全优先、渐进式披露、CLI-first 基因。
3. 是否能提升真实 Text-to-SQL 正确性和可解释性。
4. 是否能吸收 `AskDB-Public` 的某个局部优点。

如果 `AskDB-Public` 的做法与前两条冲突，应舍弃。比如：重型多意图 DAG、强 embedding 初始化、复杂多 Agent 长链路，都不应成为 DBAide 的默认形态。

### 2.1 Claude/Codex 标准下的产品要求

DBAide 必须像一个专业 agent，而不是传统数据库客户端加聊天框。

Agent 要求：

- 必须先观察上下文，再行动。
- 必须把任务拆成可见阶段。
- 必须通过工具访问外部世界。
- 必须在关键步骤产出可审计 trace。
- 必须把计划、执行、结果、错误分开。
- 必须支持暂停、继续、取消、重试。
- 必须在危险动作前请求确认。
- 必须能解释“为什么这样做”。
- 必须能在失败后基于证据修复。

UI 要求：

- 用户能看到当前任务状态，而不是只有 loading。
- 用户能看到 agent 用了哪些工具。
- 用户能看到计划和 SQL 的关系。
- 用户能看到执行风险和确认原因。
- 用户能看到结果来自哪条 SQL、哪些表、哪些字段。
- 用户能一键复制、重跑、修改、继续。
- 用户能从 CLI 得到同等能力。

CLI 要求：

- CLI 不是二等入口，应与 GUI 使用同一 workflow。
- CLI 可以用 `--json`、`--show-trace`、`--dry-run`、`--policy` 暴露 GUI 同等能力。
- CLI 应适合脚本、CI、调试和自动化。

### 2.2 从 AskDB-Public 汲取什么，去除什么

#### 应该吸收的精华

- SchemaLink 思想：SQL 前必须确认当前问题所需的最小 schema 是否足够。
- 工具注册思想：所有工具都要有名称、输入 schema、输出 schema、权限等级、超时和错误码。
- 中间计划思想：不要从自然语言直接跳到 SQL，应先形成结构化查询计划。
- 多层校验思想：SQL 需要经过只读、安全、schema、语法、执行预检、语义一致性校验。
- 澄清恢复思想：信息不足时暂停并问用户，用户回答后从原任务继续。
- 进度可视化思想：用户应看到阶段、工具、结果、检查点，而不是只看到“Thinking...”。

#### 不应照搬的部分

- 不照搬复杂多意图 DAG：当前 DBAide 首先要把单意图、多表、可验证查询做好。
- 不强依赖 embedding：embedding 可作为插件增强，不应成为首次使用门槛。
- 不照搬 LangChain 重依赖：当前轻量 `llm.py` 抽象可以保留，只增强结构化输出和重试。
- 不照搬 Web SSE 架构：桌面端用 PyQt signal/event stream 即可，核心是事件模型。
- 不照搬 MySQL 单方言假设：DBAide 必须继续保持 SQLite、MySQL、Postgres adapter 结构。
- 不让 agent 数量爆炸：生产体验要快，简单任务要走快速路径，复杂任务才进入多阶段推理。

## 3. Claude Code / Codex 式 Agent 设计原则

Claude Code 和 Codex 的优秀之处不在于“模型很强”，而在于它们的执行范式：

1. 先观察环境，再行动。
2. 把任务拆成阶段，而不是一次性生成最终答案。
3. 工具调用可见，结果可追踪。
4. 重要操作前有计划，危险操作前有确认。
5. 出错后根据证据修复，而不是盲目重试。
6. 保持上下文，不重复做已经完成的工作。
7. 输出不仅给机器执行，也给人理解。

DBAide 应采用同样思路：

- 先读取离线资产和当前连接状态。
- 再判断任务类型和风险等级。
- 再构造最小上下文。
- 再形成可解释查询计划。
- 再生成 SQL。
- 再验证 SQL。
- 再根据风险决定自动执行、请求确认或只展示。
- 最后解释结果，并保留完整 trace。

### 3.1 必须对齐的 Claude/Codex 范式

`Plan first`

- 复杂任务不能直接执行。
- 必须先生成可见计划。
- 计划必须能被用户确认或修改。

`Tool mediated`

- Agent 不直接访问数据库、文件、配置。
- 所有外部动作通过工具。
- 工具调用结果进入 trace。

`Human-in-the-loop`

- 高风险动作暂停。
- 低置信推断暂停。
- 多候选关键字段暂停。
- 用户确认后从原阶段继续。

`Evidence based`

- 回答必须引用执行证据。
- 结果解释必须基于 QueryResult。
- SQL 必须可追溯到 QueryPlan。

`Reversible and inspectable`

- 用户可以查看计划。
- 用户可以查看 SQL。
- 用户可以查看验证报告。
- 用户可以查看 trace。
- 用户可以导出 debug 包。

`Same engine everywhere`

- CLI、GUI、API 调用同一个 engine。
- 不允许 GUI 有私有业务逻辑。
- 不允许 CLI 绕过 SQLValidator。

## 4. 最终产品形态

DBAide 应该有三个一致的入口：

- 桌面 GUI：面向日常使用，是主产品。
- CLI：面向开发者和自动化，功能与 GUI 同源。
- Python API：面向未来插件、脚本和 Web 服务，复用同一 workflow。

三者共享同一核心：

- `WorkflowEngine`
- `AgentRuntime`
- `ToolRegistry`
- `AssetStore`
- `SQLGuard`
- `Adapter`
- `RunTrace`

GUI 不应该自己拼业务逻辑，CLI 也不应该绕过资产系统。所有入口都调用同一套核心工作流。

### 4.1 CLI 与 UI 能力等价原则

CLI 和 UI 必须提供同等能力。区别只在交互方式，不在功能边界。

规则：

- 每一个 GUI 按钮背后都应该能映射到一个 CLI 命令或 Python API。
- 每一个重要 CLI 命令都应该在 GUI 中有入口。
- CLI 的输出可以是文本、JSON、Markdown；GUI 的输出是卡片、表格、trace 和预览，但两者来自同一个 `WorkflowResult`。
- CLI 的 `--json` 输出应该和 GUI 内部消费的数据结构一致。
- CLI 的 `--show-trace` 应展示 GUI 右侧 Trace 面板同一份 trace。
- CLI 的 `--dry-run` 应对应 GUI 的 `Generate SQL only`。
- CLI 的 `--confirm` / `--no-confirm` 应对应 GUI 的执行策略。
- CLI 的 `assets build/status/show/enrich` 应对应 GUI 的 Assets Tab。
- CLI 的 `tree/ddl/relations/doc/diff` 应对应 GUI 的 Schema/Assets/Developer tools。

能力映射：

```text
GUI: Add Connection              CLI: dbaide connect add
GUI: Test Connection             CLI: dbaide connect test
GUI: Build Assets                CLI: dbaide assets build
GUI: Asset Status                CLI: dbaide assets status
GUI: Open Asset                  CLI: dbaide assets show
GUI: Search Field                CLI: dbaide find
GUI: Ask                         CLI: dbaide ask
GUI: Chat                        CLI: dbaide chat
GUI: Validate SQL                CLI: dbaide sql
GUI: Execute SQL                 CLI: dbaide sql --execute
GUI: Diagnose SQL                CLI: dbaide diagnose
GUI: Schema Tree                 CLI: dbaide tree
GUI: Show DDL                    CLI: dbaide ddl
GUI: Relations                   CLI: dbaide relations
GUI: Export Schema Markdown      CLI: dbaide doc
GUI: Compare Schemas             CLI: dbaide diff
GUI: Query History               CLI: dbaide history
GUI: Export Debug Trace          CLI: dbaide runs export
```

新增 CLI 命令建议：

```text
dbaide runs list
dbaide runs show <workflow_id>
dbaide runs export <workflow_id> --out debug.zip
dbaide eval run --dataset golden.jsonl
dbaide snippets list
dbaide snippets save <name> --sql "..."
```

### 4.2 单一核心，多个适配层

最终代码结构应是：

```text
dbaide/core/
  workflow.py
  runtime.py
  events.py
  errors.py
  result.py

dbaide/agent/
  router.py
  schema_linker.py
  planner.py
  renderer.py
  interpreter.py
  error_router.py
  prompts/

dbaide/tools/
  registry.py
  schema.py
  query.py
  profile.py
  asset.py
  diagnose.py

dbaide/cli.py
dbaide/gui_app/
dbaide/api.py
```

CLI 和 GUI 只做：

- 收集用户输入。
- 选择连接、数据库、模型、执行策略。
- 调用 `WorkflowEngine.run()`。
- 展示 `WorkflowResult` 和事件流。

CLI 和 GUI 不应分别实现 ask、SQL 校验、资产构建的业务逻辑。

## 5. 核心工作流设计

### 5.1 总体阶段

一个自然语言请求进入系统后，按如下阶段运行：

1. 创建 workflow：生成 `workflow_id`，记录用户问题、连接、数据库、执行策略。
2. 环境检查：检查连接、资产状态、模型配置、只读能力、当前权限。
3. 任务路由：识别是查数据、找字段、看结构、诊断 SQL、改写 SQL、比较 schema、构建资产还是配置问题。
4. 上下文收集：从离线资产和 live schema 中获取最小必要上下文。
5. Schema linking：确认候选库、表、列、join path、时间列、指标列、过滤列。
6. 计划生成：生成结构化 `QueryPlan`，不是 SQL。
7. 计划校验：确认计划中的表、列、join、聚合、过滤都存在且合理。
8. SQL 渲染：按方言把计划渲染为 SQL。
9. SQL 校验：只读、安全、单语句、schema、方言、EXPLAIN、limit、预期输出列。
10. 风险决策：自动执行、请求确认、请求澄清或拒绝。
11. 执行：只读事务、timeout、limit、truncated 标记、错误捕获。
12. 结果解释：只基于执行结果解释，不编造。
13. 汇总输出：答案、SQL、结果表、假设、警告、下一步建议、trace。
14. 持久化：保存会话、计划、SQL、结果摘要、错误、用户确认记录。

### 5.2 快速路径与深度路径

不是所有问题都应该走完整复杂链路。

快速路径适用于：

- “有哪些表？”
- “users 表有哪些字段？”
- “订单金额字段在哪？”
- “执行这条 SQL”
- “解释这条 SQL 为什么慢”
- “SELECT count(*) FROM users”

快速路径特点：

- 不调用或少调用 LLM。
- 优先用离线资产、正则、SQL parser、adapter。
- 延迟目标小于 1 秒到 3 秒。

深度路径适用于：

- “最近 7 天每个渠道的订单转化率”
- “找出复购用户占比，并按城市排序”
- “为什么这周收入下降了？”
- “帮我查每个客户最近一次购买的商品类别”

深度路径特点：

- 需要 schema linking、多表 join、查询计划、LLM 协助。
- 必须显示计划和置信度。
- 低置信度必须询问用户。

### 5.3 Workflow 状态模型

每次请求应生成结构化状态：

```text
Workflow
  workflow_id
  user_question
  connection
  database_scope
  mode: ask | sql | inspect | diagnose | asset
  status: pending | running | wait_user | need_confirm | completed | failed | cancelled
  execution_policy: auto | confirm | sql_only
  created_at
  updated_at
  phases[]
  trace[]
  result
```

每个 phase 包含：

```text
Phase
  name
  status
  started_at
  ended_at
  input_summary
  output_summary
  warnings[]
  errors[]
```

每个 trace event 包含：

```text
TraceEvent
  event_id
  timestamp
  type: agent | tool | validation | execution | user | system
  stage
  title
  summary
  detail
  duration_ms
  input_preview
  output_preview
  metadata
```

这样 GUI 可以展示时间线，CLI 可以 `--show-trace`，调试时可以导出完整诊断包。

### 5.4 全场景覆盖矩阵

核心工作流必须覆盖以下场景。每个场景都要明确是否需要 LLM、是否可自动执行、失败时如何处理。

```text
场景：查看数据库结构
入口：GUI Schema Tree / CLI tree
LLM：不需要
执行：metadata only
输出：库表列树、表数量、字段数量
失败处理：连接失败、资产缺失、权限不足
```

```text
场景：查找字段在哪
入口：GUI Search / CLI find
LLM：可选，默认不需要
执行：asset search
输出：候选表列、分数、摘要、字段类型
失败处理：无资产时提示构建或 live fallback
```

```text
场景：解释表或字段
入口：Schema Inspector / CLI inspect/assets show
LLM：不需要，除非用户要求重新总结
执行：asset read + optional profile
输出：Markdown 文档、JSON、profile
失败处理：资产缺失则 live describe
```

```text
场景：简单单表查询
入口：Ask / CLI ask
LLM：可选
执行：低风险时自动执行
输出：答案、SQL、结果表、假设
失败处理：列不明则澄清，SQL 错误则 rerender
```

```text
场景：复杂多表查询
入口：Ask / CLI ask
LLM：需要
执行：默认确认后执行
输出：schema linking、join path、计划、SQL、结果
失败处理：join 低置信则 ask_user 或 request_confirmation
```

```text
场景：SQL 手动执行
入口：SQL Tab / CLI sql
LLM：不需要
执行：validate 后执行
输出：validation report、结果表
失败处理：拒绝危险 SQL，显示具体规则
```

```text
场景：SQL 诊断
入口：SQL Tab Diagnose / CLI diagnose
LLM：可选
执行：EXPLAIN only，必要时只读 sample
输出：执行计划、扫描风险、索引建议、改写建议
失败处理：EXPLAIN 失败则给出数据库错误和语法建议
```

```text
场景：SQL 改写
入口：SQL Tab Rewrite / CLI rewrite
LLM：需要
执行：默认不执行
输出：改写 SQL、理由、风险、是否语义等价
失败处理：无法保证等价时只给建议不替换
```

```text
场景：资产构建
入口：Assets Tab / CLI assets build
LLM：可选
执行：metadata/profile/sample
输出：进度、错误、统计、Markdown/JSON 资产
失败处理：单列失败不终止整体，最终报告 partial
```

```text
场景：schema diff
入口：Assets Diff / CLI diff
LLM：不需要
执行：asset compare
输出：缺失表、字段差异、类型差异、索引/关系差异
失败处理：资产缺失提示构建
```

```text
场景：多实例查询
入口：Ask with connections / CLI ask --conn all
LLM：可选
执行：每实例隔离
输出：按实例分组结果、失败实例、汇总解释
失败处理：部分失败显示 partial，不吞错误
```

```text
场景：空结果解释
入口：Ask / SQL
LLM：可选
执行：可选放宽过滤条件的诊断 SQL
输出：可能原因、下一步建议
失败处理：需要用户确认才能执行额外诊断
```

### 5.5 上下文层级与预算

Agent 不能无限制读取 schema，也不能把所有资产塞给模型。上下文必须分层和限额。

上下文层级：

```text
L0 当前环境：连接名、数据库类型、默认库、模型、执行策略
L1 资产摘要：实例/库/表数量、资产状态、stale 状态
L2 候选对象：候选表、候选列、候选 join path
L3 精确 schema：选中表的字段、类型、注释、主键、索引
L4 统计证据：profile、top values、sample values、row estimate
L5 执行证据：EXPLAIN、query result、错误、空结果
```

预算原则：

- 默认 prompt 只包含 L0-L3。
- L4 只有在判断字段语义、枚举值、时间范围、join 质量时才进入。
- L5 只进入结果解释、错误修复、诊断阶段。
- 每一轮都记录“为什么披露这层上下文”。
- 如果上下文超过预算，优先保留候选表列、join path、用户确认和错误反馈。

### 5.6 自洽性约束

设计文档中所有模块必须满足以下约束：

- Workflow 是唯一状态源，UI 不保存业务状态副本。
- Tool 是数据库访问唯一入口，Agent 不直接访问 adapter。
- QueryPlan 是 SQL 的唯一业务来源，SQLRenderer 不重新解释用户问题。
- SQLValidator 是执行前必经门，任何入口都不能绕过。
- RiskController 是自动执行前必经门，低置信和高风险都必须停下。
- ResultInterpreter 只能解释 QueryResult，不能根据常识补结果。
- CLI 和 GUI 消费同一个 WorkflowResult。
- JSON 资产服务机器，Markdown 资产服务人类，二者由同一次构建产生。

## 6. Agent 核心架构

Agent 架构必须以 Claude Code / Codex 为标准。也就是说，DBAide 的 agent 不是“SQL 生成模型”，而是一个有上下文、有工具、有计划、有执行边界、有验证、有恢复能力的数据库工作代理。

必须满足：

- 像 Claude Code 读代码一样，先读数据库资产和上下文。
- 像 Codex 执行任务一样，先形成计划再调用工具。
- 像成熟 agent 一样，所有外部动作可见、可审计、可取消。
- 像 IDE agent 一样，用户可以随时介入、修改目标、确认风险、继续执行。

### 6.1 Agent 组件

建议核心 agent 分为以下角色。

`RequestRouter`

- 输入：用户问题、当前页面、选中连接、是否有 SQL。
- 输出：任务类型、风险等级、是否需要模型、是否支持。
- 原则：不能把未支持任务落到 data_query；不确定时返回 `needs_clarification`。

`ContextCollector`

- 输入：任务类型、连接、资产状态、数据库范围。
- 输出：最小上下文包。
- 负责：查资产、查 schema、查历史、查当前 session disclosure。

`SchemaLinker`

- 输入：自然语言问题、上下文包、资产搜索结果。
- 输出：候选表、候选列、join path、指标列、时间列、过滤列、置信度、缺口。
- 关键：只做 schema 对齐，不写 SQL。

`QueryPlanner`

- 输入：用户问题、schema linking 结果、用户补充信息。
- 输出：结构化 `QueryPlan`。
- 关键：表达业务语义，不表达方言细节。

`PlanValidator`

- 输入：`QueryPlan`、已披露 schema。
- 输出：通过、失败原因、修复建议。
- 关键：确定性校验，不依赖 LLM。

`SQLRenderer`

- 输入：`QueryPlan`、dialect。
- 输出：一个或少量 SQL candidate。
- 关键：不要重新理解业务，只渲染计划。

`SQLValidator`

- 输入：SQL candidate、schema、execution policy。
- 输出：validation report。
- 校验：只读、单语句、危险函数、顶层 limit、schema 白名单、EXPLAIN、预期列。

`RiskController`

- 输入：validation report、查询计划、表规模、用户策略。
- 输出：auto execute / need confirm / sql only / reject。
- 关键：执行前最后一道门。

`ResultInterpreter`

- 输入：SQL、QueryResult、QueryPlan、warnings。
- 输出：自然语言解释、假设、限制、下一步。
- 关键：只解释真实结果，不重新编造分析。

`ErrorRouter`

- 输入：当前阶段、错误、上下文、历史尝试。
- 输出：repair action。
- 修复动作：重新 link schema、重新 plan、重新 render、重新 validate、重新 execute、ask user、stop。

### 6.2 Agent 运行时

所有 agent 调用都应走 `AgentRuntime`。

`AgentRuntime` 负责：

- 注入 system prompt。
- 注入最近 trace 摘要。
- 注入可用工具列表。
- 强制 JSON schema 输出。
- Pydantic/dataclass 校验。
- JSON 修复重试。
- semantic post_validate。
- 记录 token、耗时、模型、错误。
- 支持取消。

一个 agent 不能直接访问数据库；只能通过工具。

### 6.3 Prompt 设计原则

Prompt 不能散落在代码中，应集中管理。

目录建议：

```text
dbaide/agent/prompts/
  router.md
  schema_linker.md
  query_planner.md
  sql_renderer.md
  sql_semantic_validator.md
  result_interpreter.md
  error_router.md
```

每个 prompt 文档包含：

- 角色说明。
- 输入字段。
- 输出 schema。
- 严格规则。
- 禁止行为。
- 成功示例。
- 失败示例。
- 版本号。

Prompt 必须明确：

- 不允许编造表。
- 不允许编造列。
- 不允许假设 join。
- 不允许绕过 SQLValidator。
- 不允许基于未执行数据下结论。
- 不允许在低置信时硬猜。

### 6.4 工具设计

工具需要像 Claude Code 的工具一样清晰、有限、可审计。

核心工具分层：

`metadata tools`

- `list_instances`
- `list_databases`
- `list_tables`
- `describe_table`
- `show_table_doc`
- `show_column_doc`
- `search_assets`

`profile tools`

- `sample_rows`
- `profile_column`
- `profile_table`
- `estimate_table_size`
- `find_top_values`

`query tools`

- `validate_sql`
- `explain_sql`
- `execute_readonly_sql`

`asset tools`

- `build_assets`
- `refresh_asset`
- `validate_assets`
- `export_schema_markdown`

`interaction tools`

- `ask_user`
- `request_confirmation`
- `show_plan`

每个工具定义：

```text
ToolSpec
  name
  description
  input_schema
  output_schema
  permission_level
  timeout_seconds
  max_rows
  cache_policy
  safe_for_auto_call
```

权限等级：

- `safe_metadata`：可自动调用。
- `safe_profile`：可自动调用，但有预算限制。
- `costly_scan`：需要风险控制。
- `sql_validate`：可自动调用。
- `sql_execute`：必须经过 SQLValidator 和 RiskController。
- `config_write`：必须用户确认。

### 6.5 结构化查询计划

DBAide 不一定要完整复制 AskDB 的 RA，但必须有轻量计划层。

`QueryPlan` 应表达：

```text
QueryPlan
  intent_summary
  target_entities[]
  selected_columns[]
  filters[]
  joins[]
  aggregations[]
  group_by[]
  order_by[]
  limit
  time_range
  output_columns[]
  assumptions[]
  confidence
  missing_information[]
```

示例：

```text
intent_summary: 最近 7 天每天订单数
target_entities: orders
selected_columns: created_at
aggregations: count(*)
group_by: date(created_at)
filters: created_at >= current_date - 7 days
output_columns: day, order_count
confidence: 0.86
```

多表查询时：

```text
target_entities: orders, users
joins:
  orders.user_id = users.id
  source: foreign_key | name_heuristic | user_confirmed
  confidence: 0.91
```

所有计划都要可显示给用户。

### 6.6 Agent 决策循环

DBAide 的 agent 不应该是“调用一次 LLM 得到 SQL”。它应该像 Claude Code/Codex 一样持续执行一个受控循环：

```text
Observe -> Plan -> Act -> Validate -> Decide -> Report
```

`Observe`

- 读取当前 workflow 状态。
- 读取当前连接和数据库范围。
- 读取资产状态。
- 读取用户问题和历史上下文。
- 读取已有 trace 和上一次失败原因。

`Plan`

- 决定下一步需要什么信息。
- 选择工具，而不是直接猜。
- 如果工具不足，提出澄清问题。
- 如果风险太高，提出确认请求。

`Act`

- 调用一个或多个允许的工具。
- 工具调用必须有预算、超时、权限等级。
- 工具结果写入 trace。

`Validate`

- 对工具结果、agent 输出、query plan、SQL、执行结果做程序校验。
- 校验失败不能被自然语言解释绕过。

`Decide`

- 决定继续、重试、澄清、确认、执行或停止。
- 决策必须记录原因。

`Report`

- 向用户展示阶段性进展或最终结果。
- 报告必须区分事实、假设、建议和错误。

每个循环最多执行固定步数，例如默认 12 步。超过步数后必须停下并解释“我需要更多信息或更窄的问题”。

### 6.7 Agent 自动化等级

参考 Codex/Claude Code 的“自主但受控”思想，DBAide 应支持不同自动化等级。

`Level 0: Inspect only`

- 只能查看 schema、资产、历史。
- 不能生成执行 SQL。
- 适合首次连接和敏感环境。

`Level 1: Generate only`

- 可以生成 SQL 和计划。
- 不能执行 SQL。
- 默认适合生产库。

`Level 2: Safe auto execute`

- 低风险 SELECT 可自动执行。
- 多表低置信、全表大扫描、敏感列查询需要确认。
- 推荐默认等级。

`Level 3: Expert assisted`

- 用户可允许更多 profile/sample/explain 操作。
- 仍然禁止 DML/DDL。
- 适合开发库和分析库。

UI 中表现为 Bottom Composer 的执行策略；CLI 中表现为：

```text
--policy inspect-only
--policy sql-only
--policy safe-auto
--policy expert
```

### 6.8 工具调用纪律

Agent 调用工具时必须遵守：

- 先用低成本工具，再用高成本工具。
- 先用资产，再访问 live DB。
- 先 validate，再 explain，再 execute。
- 对同一工具同一参数命中缓存时不重复调用。
- 任何 `execute_readonly_sql` 前必须存在通过的 `ValidationReport`。
- 任何 `profile_table` 或大样本读取前必须检查预算。
- 工具失败必须进入 ErrorRouter，不能吞掉。

工具调用顺序示例：

```text
用户问：最近 7 天每个渠道订单数

1. search_assets("订单 渠道 时间")
2. show_table_doc(orders)
3. show_column_doc(orders.created_at)
4. show_column_doc(orders.channel)
5. build_query_plan()
6. validate_plan()
7. render_sql()
8. validate_sql()
9. explain_sql()
10. risk_decision()
11. execute_readonly_sql()
12. interpret_result()
```

如果第 2 步找不到明确渠道字段，不能直接猜，应进入：

```text
ask_user("我找到了 channel/source/utm_source 三个候选字段，你希望使用哪个作为渠道？")
```

### 6.8.1 Claude/Codex 风格工具界面要求

工具调用在 UI 和 CLI 中都要像 Claude/Codex 一样可见。

每次工具调用应显示：

```text
Tool: search_assets
Why: 查找与“订单渠道”相关的表和字段
Input: 订单 渠道 最近 7 天
Result: 8 candidates
Duration: 120ms
Status: success
```

用户不需要默认看到完整参数，但必须能展开查看。

工具调用状态：

- `queued`
- `running`
- `success`
- `warning`
- `failed`
- `cancelled`

工具失败时：

- 显示失败工具名。
- 显示失败原因。
- 显示 agent 下一步如何处理。
- 不允许静默 fallback。

CLI 对应：

```bash
dbaide ask "最近7天订单数" --show-trace
```

输出应包含工具调用摘要。

### 6.8.2 Agent 不允许做的事

为了符合 Claude/Codex 的成熟 agent 标准，以下行为必须禁止：

- 不读 schema 就生成 SQL。
- 不显示计划就执行复杂查询。
- 不经过 SQLValidator 就执行 SQL。
- 不经过 RiskController 就自动执行低置信 SQL。
- 不说明假设就给业务结论。
- 不基于结果就解释原因。
- 把数据库 driver 错误原样丢给用户。
- 在工具失败后假装成功。
- 在多候选字段冲突时随便选一个。
- 把“为什么收入下降”直接伪装成因果分析。

### 6.8.3 Agent 的用户介入点

Claude/Codex 式 agent 的关键不是全自动，而是恰当地让用户介入。

DBAide 至少有这些介入点：

- 选择数据库范围。
- 选择候选表。
- 选择候选字段。
- 确认 join path。
- 确认时间口径。
- 确认指标口径。
- 确认是否执行 SQL。
- 确认是否读取样本值。
- 确认是否构建或刷新资产。
- 修改 QueryPlan。
- 取消当前任务。
- 从失败阶段重试。

每个介入点都必须能在 CLI 中表达：

```text
--database
--table
--column
--confirm
--no-execute
--policy
--resume <workflow_id>
```

### 6.9 SchemaLinker 详细逻辑

SchemaLinker 是 Text-to-SQL 成功率的核心，不应只是表名匹配。

输入：

- 用户问题。
- 当前数据库范围。
- 资产搜索结果。
- 已知表列。
- 历史上下文。
- 用户选中的表/列。

输出：

```text
SchemaLinkResult
  candidate_tables[]
  candidate_columns[]
  selected_tables[]
  selected_columns[]
  join_paths[]
  measure_columns[]
  dimension_columns[]
  time_columns[]
  filter_columns[]
  missing_information[]
  confidence
  evidence[]
```

打分信号：

- 名称匹配。
- 中文别名匹配。
- 注释/Markdown 文档匹配。
- semantic_summary 匹配。
- column role 匹配。
- profile top values 匹配。
- FK 匹配。
- `*_id` 命名匹配。
- 用户历史选择。
- 当前 UI 选中对象。

输出必须解释：

- 为什么选这张表。
- 为什么选这个字段。
- join path 来源是什么。
- 哪些候选被排除。
- 哪些信息不确定。

置信度规则：

- `>= 0.85`：可继续计划。
- `0.65 - 0.85`：可生成 SQL，但执行前确认。
- `< 0.65`：必须澄清。
- 多个关键字段置信度接近时：必须澄清。

### 6.10 QueryPlanner 详细逻辑

QueryPlanner 不写 SQL。它只把业务问题变成可验证的结构化计划。

必须处理：

- 查询对象。
- 输出口径。
- 过滤条件。
- 时间范围。
- 聚合口径。
- 分组维度。
- 排序和 top N。
- join 关系。
- 空值处理。
- 去重逻辑。
- 用户假设。

必须拒绝：

- schema 不足。
- join 不足。
- 指标口径不明。
- 时间口径不明。
- 用户问题本身无法用数据库回答。

例如“为什么收入下降了”不能直接变成一个 SQL。它应拆成可执行分析计划：

```text
1. 确认收入字段。
2. 确认比较时间范围。
3. 计算当前周期收入。
4. 计算上一周期收入。
5. 按渠道/产品/地区分解差异。
6. 向用户说明这只是数据归因线索，不是因果结论。
```

如果时间范围缺失，先问用户。

### 6.11 SQLRenderer 详细逻辑

SQLRenderer 只消费 QueryPlan。

规则：

- 不允许新增表。
- 不允许新增列。
- 不允许新增过滤条件。
- 不允许改变 join 类型，除非计划允许。
- 不允许省略计划中的输出列。
- 必须按 dialect 渲染日期函数、limit、identifier quote。
- 必须给每个 candidate 附带 rationale 和 expected_columns。

对于复杂 SQL，允许输出多个 candidate，但最多 3 个。

Candidate 排序：

1. 最简单且最符合计划。
2. 方言兼容性最好。
3. 性能风险最低。

### 6.12 SQLValidator 详细逻辑

SQLValidator 必须比当前实现更严格。

校验层：

1. Parse：SQL 是否能解析。
2. Statement：是否单语句。
3. Readonly：是否 SELECT/WITH/EXPLAIN。
4. Forbidden：是否包含危险关键字、函数、文件访问、动态执行。
5. Schema：表和列是否在披露范围内。
6. Plan alignment：SQL 是否覆盖 QueryPlan 的实体、输出列、过滤、聚合、join。
7. Limit：是否有顶层 limit 或系统包装 limit。
8. Dialect：是否符合目标数据库方言。
9. Explain：数据库是否接受 EXPLAIN。
10. Risk：是否可能大扫描、敏感列、低置信 join。

输出：

```text
ValidationReport
  ok
  normalized_sql
  issues[]
  warnings[]
  risk_level
  requires_confirmation
  explain_summary
```

### 6.13 ErrorRouter 详细逻辑

错误不能只显示给用户，必须转化为下一步动作。

映射示例：

```text
UNKNOWN_COLUMN -> 回到 SchemaLinker，查找相似字段
UNKNOWN_TABLE -> 回到 SchemaLinker，扩大候选表范围
SQL_SYNTAX_ERROR -> 回到 SQLRenderer，带错误反馈重渲染
EXPLAIN_FAILED -> 如果是方言问题，回到 SQLRenderer；如果是权限问题，停止
TIMEOUT -> 降低 limit、添加过滤建议、请求用户确认
EMPTY_RESULT -> 解释空结果，提供放宽条件的诊断选项
LOW_CONFIDENCE_JOIN -> 请求用户确认 join
MODEL_UNAVAILABLE -> 走 heuristic 或提示配置模型
ASSET_STALE -> 提示刷新资产或继续使用旧资产
```

修复循环必须有上限：

- 同一阶段最多修复 2 次。
- 同一 workflow 总修复最多 5 次。
- 超限后停止并展示完整错误链。

### 6.14 Agent 输出质量标准

一个优秀的 DBAide agent 输出必须同时满足：

- 有答案。
- 有 SQL。
- 有结果证据。
- 有假设。
- 有风险说明。
- 有下一步建议。
- 有可追踪计划。

不合格输出：

- 只给 SQL。
- 只给自然语言，不给证据。
- SQL 中出现未披露字段。
- 结果为空但不解释。
- 低置信 join 却自动执行。
- 执行失败后只显示 driver exception。

## 7. 离线资产体系设计

### 7.1 当前方向是正确的

当前“离线资产 + 渐进式披露”的方向是正确的。它比每次 live introspection 更快、更稳定，也比直接把全库 schema 塞给 LLM 更安全。

但目前 JSON 文档对人不够友好。建议将资产分为两层：

- 机器层：JSON，供检索、校验、工具调用使用。
- 人类层：Markdown，供用户阅读、预览、审计、编辑业务说明。

### 7.2 资产目录建议

```text
~/.dbaide/assets/instances/<instance>/
  manifest.json
  instance.json
  instance.md
  databases.json
  databases/<database>/
    database.json
    database.md
    tables.json
    tables/<table>/
      table.json
      table.md
      columns.json
      columns/<column>.json
      columns/<column>.md
```

JSON 用于系统，Markdown 用于人类。

### 7.3 Markdown 资产内容

`table.md` 应包含：

- 表名。
- 所属库。
- 表类型。
- 估算行数。
- 业务描述。
- 主要字段。
- 主键。
- 外键。
- join hints。
- 常见查询场景。
- 敏感字段提示。
- 采样数据是否脱敏。
- 最近构建时间。

`column.md` 应包含：

- 字段名。
- 类型。
- 是否可空。
- 是否主键/索引。
- 原始注释。
- 语义角色。
- 示例值。
- top values。
- null rate。
- distinct ratio。
- 使用建议。
- 风险提示。

GUI 中默认显示 Markdown 预览，而不是裸 JSON。高级用户可以点击“查看 JSON”。

### 7.4 资产预览可视化

资产页应提供三种视图：

- Tree View：实例、库、表、列树。
- Document View：Markdown 渲染预览。
- Raw JSON View：格式化 JSON，可复制、折叠、搜索。

用户打开某个表时，右侧显示：

- 概览卡片。
- 字段列表。
- Join 关系图。
- 质量指标。
- 示例查询。
- 构建时间与资产版本。

### 7.5 资产编辑

允许用户编辑业务描述，但不直接改机器字段。

按钮：

- `Edit Description`：编辑表/列业务描述。
- `Add Alias`：添加同义词。
- `Mark Sensitive`：标记敏感字段。
- `Save`：保存用户注释。
- `Rebuild`：重建系统生成部分。
- `Reset Generated`：丢弃生成内容并重建。

用户编辑内容单独保存：

```text
overrides/
  tables/<table>.md
  columns/<table>.<column>.md
```

构建资产时合并 generated + user override。

## 8. UI 全新设计

UI 设计也必须以 Claude Code / Codex 为标准。DBAide 的 GUI 不应是传统数据库客户端，也不应只是一个聊天窗口，而应该是“数据库 agent 工作台”。

核心体验应类似：

- 左侧是工作上下文，像 IDE 的文件树。
- 中间是 agent 对话和任务结果，像 Claude/Codex 的主工作区。
- 右侧是计划、trace、证据、诊断，像 agent 的可观察执行面板。
- 底部是 composer，用户随时给指令、改目标、暂停、继续。

### 8.1 总体设计原则

UI 要参考 Claude Code / Codex 的工作台体验，而不是传统数据库客户端。

核心理念：

- 左侧是上下文。
- 中间是对话和任务。
- 右侧是证据和细节。
- 底部是输入与执行控制。
- 所有危险动作可见、可确认、可取消。

### 8.1.1 Claude/Codex 风格 UI 标准

`Persistent context`

- 左侧始终显示当前连接、数据库、schema tree、资产状态。
- 用户不用猜 agent 当前在哪个数据库工作。

`Visible agent activity`

- 右侧 Trace 始终能看到 agent 阶段。
- 工具调用不是隐藏日志，而是一等 UI 元素。

`Plan transparency`

- 复杂查询必须显示 QueryPlan。
- 用户可以确认、修改或要求重生成计划。

`Safe execution`

- SQL 执行按钮必须展示执行策略。
- 高风险执行必须弹出确认卡。
- 用户可以选择只生成 SQL。

`Editable artifacts`

- SQL 可以复制、打开到 SQL Tab、修改后运行。
- QueryPlan 可以在高级模式中编辑。
- 资产 Markdown 可以编辑用户注释。

`Recoverable failures`

- 错误卡片提供下一步按钮。
- 用户可以从失败阶段重试。
- 错误不是终点，而是 agent 修复循环的一部分。

`CLI parity`

- 每个 GUI 操作都能显示对应 CLI 命令。
- 每个结果卡可以复制 CLI 复现命令。

### 8.1.2 UI 中的 Agent 状态

主界面必须清楚显示 agent 当前状态：

```text
Idle
Reading assets
Planning
Waiting for confirmation
Validating SQL
Executing read-only query
Interpreting result
Failed
Cancelled
Completed
```

这些状态应出现在：

- Top Bar 全局状态。
- 当前回答卡片顶部。
- Right Trace 当前高亮节点。
- CLI trace 输出。

### 8.1.3 UI 中的确认卡

所有确认都不应使用简单 yes/no 弹窗，而应使用带证据的确认卡。

确认卡包含：

```text
Title: Confirm query execution
Reason: This query joins 3 tables and one join is inferred by naming.
SQL Preview
Tables involved
Risk level
Limit
Timeout
Buttons:
  Run read-only
  Generate SQL only
  Edit plan
  Cancel
```

确认卡必须可追踪，用户点击结果写入 workflow trace。

### 8.1.4 内容渲染规范

DBAide 的所有文本展示都必须有明确渲染规则，不能简单把纯文本塞进 `QTextEdit`。

`对话消息`

- 用户消息、assistant 消息、系统消息都必须支持 Markdown 渲染。
- Assistant 回答默认以 Markdown 渲染展示，包括标题、列表、粗体、代码块、表格、链接。
- 用户输入显示时也要 Markdown-safe，不能执行 HTML。
- 所有消息必须先做 HTML escape，再由受控 Markdown renderer 渲染。
- 禁止原样渲染未经清洗的 HTML。
- 代码块必须有语法高亮，SQL 代码块按 SQL 高亮。
- 代码块右上角有 `Copy`。
- 长代码块默认折叠，显示 `Show more`。

`资产 Markdown`

- `instance.md`、`database.md`、`table.md`、`column.md` 必须以渲染后的 Markdown Preview 展示。
- 预览不是显示 Markdown 源码，而是渲染后的文档。
- Preview 顶部提供 `Edit Markdown`、`Copy Markdown`、`Open Raw`、`Export`。
- 编辑时进入双栏：左侧 Markdown 源码，右侧实时预览。
- 保存前显示 diff，避免误覆盖系统生成内容。

`SQL`

- 所有 SQL 展示必须使用 SQL 高亮。
- SQL 块必须支持复制、格式化、打开到 SQL Tab、执行前校验。
- SQL 中的表名、列名可以 hover 显示资产摘要。
- SQL 错误应尽量定位到行列。
- SQL 过长时保留格式，不自动压成一行。

`JSON`

- JSON 预览必须格式化、缩进、可折叠。
- JSON 节点支持搜索。
- JSON 节点支持复制 path 和 value。
- 对大 JSON 默认折叠深层字段，避免 UI 卡顿。
- JSON 视图必须只用于高级用户，不应作为默认资产阅读方式。

`表格`

- 查询结果必须用真正的表格组件渲染，不用 Markdown 表格模拟。
- 表格支持列宽调整、横向滚动、复制单元格、复制行、复制整表。
- NULL、空字符串、0、false 必须视觉区分。
- 数字右对齐，文本左对齐，日期时间使用等宽或统一格式。
- 超长文本单元格默认截断，hover 或点击展开。
- 敏感字段如果被标记，默认遮罩，用户点击后需确认显示。

`链接`

- Markdown 中的外部链接点击前应提示打开外部浏览器。
- 内部链接可以跳转到资产、历史 run、SQL Tab、Trace event。

### 8.1.5 复制、导出与复现规范

所有重要 artifact 都必须能复制和复现。

每个 Answer Card 提供：

- `Copy Answer`
- `Copy SQL`
- `Copy Result as CSV`
- `Copy Result as Markdown`
- `Copy CLI Command`
- `Export Debug Bundle`

每个 QueryPlan 提供：

- `Copy Plan JSON`
- `Copy Plan Markdown`
- `Open in Inspector`

每个 Trace 提供：

- `Copy Trace Summary`
- `Copy Full Trace JSON`
- `Export Debug Bundle`

每个资产文档提供：

- `Copy Rendered Text`
- `Copy Markdown Source`
- `Copy JSON`
- `Export Markdown`

复制成功必须有轻量 toast，例如 `Copied SQL`，失败时显示明确错误。

### 8.1.6 加载态、空状态与骨架屏

所有页面都必须设计加载态、空状态、错误态。

`加载态`

- 首次加载 schema tree：显示 skeleton tree。
- 构建资产：显示进度条、当前库/表/列、耗时、取消按钮。
- Ask 执行中：回答卡先出现，占位显示当前阶段。
- SQL 执行中：结果区显示 running 状态和 timeout。

`空状态`

- 无连接：显示添加连接引导。
- 有连接无资产：显示构建资产引导，但允许 live schema fallback。
- 搜索无结果：显示改写关键词、构建资产、扩大范围建议。
- 查询空结果：显示“SQL 执行成功但无数据”，并给出放宽条件建议。
- 历史为空：显示示例问题和快捷入口。

`错误态`

- 错误态不能只显示红色文本。
- 必须显示错误阶段、原因、建议动作、技术详情折叠。
- 可恢复错误必须显示下一步按钮。

### 8.1.7 快捷键与键盘操作

必须提供专业工具级快捷键。

全局：

- `Cmd/Ctrl + K`：聚焦命令面板。
- `Cmd/Ctrl + L`：聚焦自然语言输入。
- `Cmd/Ctrl + Enter`：发送或执行。
- `Esc`：关闭弹窗或取消当前浮层。
- `Cmd/Ctrl + .`：打开当前任务 trace。

Ask：

- `Shift + Enter`：输入换行。
- `Cmd/Ctrl + Enter`：发送。
- `Cmd/Ctrl + Shift + Enter`：以 `Generate SQL only` 发送。

SQL Tab：

- `Cmd/Ctrl + Enter`：执行选中 SQL 或当前 SQL。
- `Cmd/Ctrl + Shift + Enter`：Explain。
- `Cmd/Ctrl + B`：格式化 SQL。
- `Cmd/Ctrl + S`：保存 snippet。

Assets：

- `Enter`：打开选中资产。
- `Cmd/Ctrl + F`：搜索资产。
- `Cmd/Ctrl + R`：刷新资产状态。

所有快捷键必须在设置页可查看，未来可配置。

### 8.1.8 命令面板

参考 Claude/Codex/IDE 工具，GUI 应提供命令面板。

入口：

- `Cmd/Ctrl + K`
- Top Bar 搜索图标

命令示例：

- `Add Connection`
- `Build Assets`
- `Search Column`
- `Ask about selected table`
- `Validate SQL`
- `Run SQL`
- `Open History`
- `Export Debug Bundle`
- `Toggle Trace Panel`
- `Set Execution Policy`

命令面板支持模糊搜索、最近命令、上下文命令。

### 8.1.9 通知与状态反馈

反馈类型：

- Toast：复制成功、保存成功、轻量提示。
- Inline Alert：资产缺失、模型未配置、低置信警告。
- Confirm Card：执行风险、敏感样本读取、明文密钥保存。
- Error Card：任务失败、连接失败、SQL 校验失败。

不要滥用系统 modal。只有阻塞型确认才使用弹层，其余在当前上下文内展示。

窗口结构：

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Bar: Project / Connection / Database / Model / Status     │
├───────────────┬───────────────────────────────┬──────────────┤
│ Left Sidebar  │ Main Workspace                │ Right Panel  │
│ Connections   │ Chat / SQL / Assets / History │ Trace        │
│ Schema Tree   │                               │ Plan         │
│ Assets        │                               │ Result Meta  │
├───────────────┴───────────────────────────────┴──────────────┤
│ Composer: natural language input + mode + execute policy      │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 Top Bar

Top Bar 高度约 44px。

左侧：

- 产品名 `DBAide`
- 当前 workspace 名称

中间：

- Connection 下拉
- Database/Schema 下拉
- Model 下拉
- Asset 状态 badge

右侧：

- `Build Assets` 按钮
- `Settings` 按钮
- 全局任务状态

Connection 下拉：

- 显示连接名和类型，如 `local · SQLite`
- 如果连接失败，显示红色状态点。
- 如果资产缺失，显示黄色状态点。
- 点击连接项后刷新 schema tree，但不清空当前聊天。

Database 下拉：

- 默认 `Auto`
- 可选具体 database/schema
- 多库时如果用户选择 `Auto`，agent 需要在 schema linking 阶段确认候选库。

Model 下拉：

- 显示当前模型，如 `Qwen Plus`
- 未配置时显示 `No model · heuristic only`
- 点击旁边小图标可测试模型。

Asset badge：

- `Ready`
- `Missing`
- `Stale`
- `Building`
- `Partial`
- 点击打开资产详情。

### 8.3 Left Sidebar

左侧宽度 280px，可折叠。

分区一：Connections

- `+ Add`：新增连接。
- `Test`：测试当前连接。
- `Set Default`：设为默认连接。
- `Refresh`：刷新连接列表。

连接项显示：

```text
● local
  SQLite · /path/app.db
  assets ready · 12 tables
```

分区二：Schema

Schema tree 支持：

- 搜索框：`Search table or column`
- filter：`All / Tables / Columns / Sensitive / Indexed`
- 树节点：database > table > column

Table 节点右键菜单：

- `Ask about this table`
- `Describe`
- `Show DDL`
- `Profile`
- `Open Asset`
- `Rebuild Table Asset`
- `Copy Table Name`

Column 节点右键菜单：

- `Ask about this column`
- `Show Profile`
- `Copy Column Name`
- `Mark Sensitive`
- `Find Related Columns`

双击行为：

- 双击 table：在主区打开 table asset 详情，不自动发问。
- 双击 column：打开 column profile 详情。

不要像当前实现那样双击后自动发送 `describe <table>`，这会让用户失去控制。

### 8.4 Main Workspace

主区有四个一级 tab：

- `Ask`
- `SQL`
- `Assets`
- `History`

#### Ask Tab

Ask 是默认页。

Ask Tab 的所有消息必须按 Markdown 渲染。Assistant 生成的自然语言、列表、代码块、SQL、警告、下一步建议都必须是结构化 Markdown 或结构化卡片，不允许直接展示未渲染纯文本。对话内容中的 SQL 代码块应自动识别，并提供 `Copy SQL`、`Open in SQL Tab`。

每条 assistant 回复采用卡片结构：

```text
Answer Card
  Summary
  SQL
  Result Table
  Assumptions
  Warnings
  Next Actions
```

用户问题卡：

- 显示问题原文。
- 显示连接、数据库、执行策略。
- 有 `Retry`、`Edit`、`Copy`。
- 原文按 Markdown-safe 方式展示，不允许 HTML 注入。
- 如果问题来自历史重跑，显示来源 workflow。

Assistant 答案卡：

- 顶部状态：`Completed in 3.2s`
- 如果执行了 SQL，显示 `Executed read-only`
- 如果未执行，显示 `SQL generated only`
- 如果有风险，显示 warning badge。
- Summary 区必须 Markdown 渲染。
- 如果回答中包含表格，优先转为结果表格组件，而不是 Markdown 表格。
- 如果包含业务结论，必须同时显示 Evidence。

SQL 区：

- 默认折叠一行摘要。
- 按钮：`Copy SQL`、`Open in SQL Tab`、`Explain`、`Run Again`。
- 如果 SQL 未执行，显示主按钮 `Review & Execute`。
- SQL 必须语法高亮。
- SQL 区显示 validation 状态：`not validated`、`passed`、`failed`。
- validation 失败时，SQL 区直接标出失败规则。

结果区：

- 小结果直接表格展示。
- 大结果显示前 N 行，并显示 `Showing 100 of 100+ rows`。
- 按钮：`Export CSV`、`Copy Table`、`Load More`。
- 表格列头显示类型和来源表。
- 表格支持列筛选和排序，但排序应标注“前端排序”或“数据库排序”。
- 加载更多必须重新通过只读执行策略。

Assumptions 区：

- 列出 agent 假设，如“使用 orders.created_at 作为订单时间”。
- 每条假设旁边有 `Change`，点击后可以补充指令并重跑。

Warnings 区：

- 资产过期。
- join path 低置信。
- 查询被 limit。
- 结果为空。
- LLM 不可用，使用启发式。

Next Actions：

- `Refine question`
- `Group by another field`
- `Add time range`
- `Explain SQL`
- `Profile involved columns`

#### SQL Tab

SQL Tab 是专业 SQL 工作区。

布局：

- 上方 SQL editor。
- 中间操作栏。
- 下方结果/诊断。

按钮：

- `Validate`：只做 SQLGuard + SchemaGuard。
- `Explain`：执行 EXPLAIN。
- `Run Read-only`：只读执行。
- `Diagnose`：分析性能和风险。
- `Format`：格式化 SQL。
- `Copy`
- `Save Snippet`

执行策略：

- 默认 limit 100。
- 可改 timeout。
- 可选择 database。
- 显示“只读事务”状态。

SQL editor 需要：

- 语法高亮。
- 行号。
- 当前选中 SQL 执行。
- 错误位置提示。
- 自动补全表/列名。
- 当前连接和 database scope 在 editor 顶部明显显示。
- 多语句时只能选择单条执行；未选择时提示用户选择。
- 自动补全来源于离线资产和 live schema，补全项显示字段类型和注释。
- 保存 snippet 时记录连接类型、dialect、创建时间和标签。

SQL 结果区需要：

- `Result` tab：表格结果。
- `Messages` tab：执行消息、warnings、truncated 信息。
- `Explain` tab：执行计划。
- `Validation` tab：SQLGuard/SchemaGuard/Plan alignment 详情。
- `Raw` tab：原始 JSON 结果。

#### Assets Tab

Assets Tab 用于浏览离线资产。

左侧：

- 资产树。
- 搜索。

右侧：

- Markdown Preview。
- JSON Preview。
- Profile。
- Relations。
- Build Log。

Markdown Preview 必须是渲染后的预览，不是 Markdown 源码。源码通过 `Open Raw` 或 `Edit Markdown` 查看。

JSON Preview 必须是格式化、可折叠、可搜索的树视图，不是普通文本框。

Profile 视图需要区分：

- 基础统计。
- top values。
- sample values。
- null/distinct 分布。
- 敏感值遮罩状态。

Relations 视图需要展示：

- 外键。
- name heuristic join。
- 用户确认 join。
- 置信度。
- 最近验证时间。

按钮：

- `Build All`
- `Build Selected`
- `Validate Assets`
- `Clean Stale`
- `Export Markdown`
- `Open Asset Folder`

构建时显示：

- 当前库。
- 当前表。
- 当前列。
- 已完成数量。
- 错误数量。
- 预计剩余时间。
- `Cancel` 按钮。

#### History Tab

History 记录所有 workflow。

列表显示：

- 时间。
- 问题。
- 连接。
- 状态。
- 是否执行 SQL。
- 耗时。

点击历史项：

- 打开完整回答。
- 查看 trace。
- 重新运行。
- 复制 SQL。
- 导出诊断包。

过滤：

- success / failed / cancelled。
- connection。
- date。
- contains SQL。

历史详情页必须完整渲染当时的 Answer Card、SQL、结果摘要、trace、用户确认记录。历史中的 Markdown 按渲染视图展示，同时允许查看原始 markdown/json。

### 8.5 Right Panel

右侧宽度 360px，可折叠。

有三个 tab：

- `Trace`
- `Plan`
- `Inspector`

#### Trace

显示 agent 时间线。

每行：

```text
12:31:02.120  route          Classified as data_query
12:31:02.340  search_assets  Found 8 candidate columns
12:31:02.910  schema_link    Selected orders + users
12:31:03.200  validate_sql   Passed readonly/schema/explain
```

点击一行展开：

- 输入摘要。
- 输出摘要。
- 耗时。
- 错误。
- 工具参数。

Trace 顶部按钮：

- `Copy Trace`
- `Export Debug`
- `Show Technical`
- `Clear`

#### Plan

显示结构化查询计划。

内容：

- 任务理解。
- 表。
- 列。
- join。
- filters。
- aggregations。
- assumptions。
- confidence。

每个候选表/列显示：

- 分数。
- 来源：asset search / FK / name heuristic / user selected。
- 为什么选择。
- 为什么没选其他候选。

按钮：

- `Edit Plan`
- `Confirm Plan`
- `Regenerate Plan`
- `Ask Clarification`

默认情况下，普通用户不需要编辑 plan，但高级模式可以打开。

#### Inspector

根据当前选中内容变化。

选中 SQL：

- 显示 validation report。
- 显示 EXPLAIN。
- 显示风险。

选中表：

- 显示 table doc。

选中列：

- 显示 column profile。

选中错误：

- 显示错误详情和修复建议。

### 8.6 Bottom Composer

底部输入区参考 Claude/Codex 的 composer。

组件：

- 多行输入框。
- 左侧 mode 选择。
- 中间 chips。
- 右侧发送按钮。

Mode：

- `Ask`
- `Find`
- `Generate SQL`
- `Diagnose SQL`
- `Explain Result`

Execution Policy：

- `Auto execute safe queries`
- `Ask before execute`
- `Generate SQL only`

默认推荐：

- 首次使用：`Ask before execute`
- 用户明确勾选后：简单低风险查询可自动执行。

按钮：

- `Send`
- `Stop`
- `Attach SQL`
- `Use selected table`
- `Clear context`

输入框 placeholder：

- 无连接：`Add or select a connection to start`
- 有连接无资产：`Ask a question, or build assets for better accuracy`
- 正常：`Ask about your data, e.g. "最近 7 天每天订单数"`

发送按钮状态：

- 无问题：disabled。
- 无连接：disabled，并提示。
- 运行中：变为 `Stop`。
- wait_user：变为 `Submit Reply`。

## 9. 关键交互流程

### 9.1 首次启动

用户打开应用。

如果无连接：

主区显示欢迎页。

欢迎页内容：

- 标题：`Connect your first database`
- 说明：`DBAide uses read-only queries and local schema assets to help you understand and query databases safely.`
- 按钮：
  - `Add SQLite`
  - `Add MySQL`
  - `Add Postgres`
  - `Import Config`
  - `Open Demo Database`

点击 `Add SQLite`：

- 打开连接配置弹窗。
- 只显示 SQLite 需要的字段。

保存连接后：

- 自动测试连接。
- 询问是否构建资产：
  - `Build assets now`
  - `Skip for now`

### 9.2 新增连接弹窗

字段：

- Name
- Type
- Host
- Port
- Database
- User
- Password Env
- Password
- SQLite Path
- SSL Mode
- Read-only check

设计：

- 默认推荐 `Password Env`。
- `Password` 字段旁显示：`Not recommended to save secrets in plain text`
- 如果填写 password，保存时二次确认。

按钮：

- `Test Connection`
- `Save`
- `Save & Build Assets`
- `Cancel`

测试连接结果：

- 成功：显示数据库版本、当前用户、只读能力。
- 失败：显示错误摘要、技术详情折叠、修复建议。

### 9.3 构建资产流程

用户点击 `Build Assets`。

弹窗：

- 选择 database。
- 选择 profile mode：`none`、`auto`、`all`。
- sample limit。
- top values。
- 并发数。
- 脱敏策略。

按钮：

- `Start`
- `Cancel`

构建中：

- 显示进度条。
- 显示当前表/列。
- 显示错误列表。
- 可以取消。

完成后：

- 显示 summary。
- 按钮：
  - `Open Assets`
  - `Ask a question`
  - `Export Markdown`

### 9.4 普通问答流程

用户输入：`最近 7 天每天订单数`

系统行为：

1. Trace 显示 `route: data_query`。
2. 搜索资产，找到 orders 表和 created_at 字段。
3. 形成计划。
4. SQL 生成。
5. SQL 校验。
6. 如果安全，执行。
7. 展示答案和表格。

右侧 Plan 显示：

- 使用表：orders。
- 时间列：created_at。
- 聚合：count。
- 分组：date(created_at)。
- 假设：订单时间使用 created_at。

如果存在多个时间字段：

- created_at
- paid_at
- completed_at

系统应进入澄清：

```text
我找到了多个可能代表“订单时间”的字段：
1. created_at：订单创建时间
2. paid_at：支付时间
3. completed_at：完成时间

你希望按哪个时间统计“最近 7 天”？
```

按钮：

- `Use created_at`
- `Use paid_at`
- `Use completed_at`
- `Explain fields`

用户选择后，从 schema linking 阶段继续。

### 9.5 低置信 join 流程

用户问：`每个客户最近一次购买的商品类别`

系统找到：

- customers
- orders
- order_items
- products

join path：

- orders.customer_id = customers.id，confidence 0.92
- order_items.order_id = orders.id，confidence 0.88
- order_items.product_id = products.id，confidence 0.76

如果某个 join 只是 name heuristic，系统不应直接执行。

显示确认卡：

```text
我需要确认以下关联关系：

orders.customer_id -> customers.id
order_items.order_id -> orders.id
order_items.product_id -> products.id

第三个关联来自字段命名推断，不是数据库外键。是否继续？
```

按钮：

- `Confirm and run`
- `Generate SQL only`
- `Choose different columns`
- `Cancel`

### 9.6 SQL 手动执行流程

用户进入 SQL Tab，输入 SQL。

点击 `Validate`：

- 显示 pass/fail。
- 如果 fail，定位原因。

点击 `Explain`：

- 如果 SQL 未通过安全校验，不允许 explain。
- 如果通过，执行 EXPLAIN。

点击 `Run Read-only`：

- 再次通过 RiskController。
- 大表无 filter 时提示确认。

确认弹窗：

```text
This query may scan a large table.
Table: events
Estimated rows: 120,000,000
Limit: 100
Timeout: 10s

Continue?
```

按钮：

- `Run`
- `Cancel`
- `Add filter`

### 9.7 错误体验

错误卡片结构：

```text
Query failed at: Validate SQL

Reason:
Column "order_total" does not exist in table "orders".

What you can do:
1. Use "total_amount" instead.
2. Search related columns.
3. Edit the question and retry.

Technical details
...
```

按钮：

- `Use suggested column`
- `Search columns`
- `Edit question`
- `Retry from plan`
- `Copy error`

不要只显示 `ERROR: xxx`。

## 10. 安全设计

### 10.1 默认执行策略

默认策略：

- schema explore 自动执行。
- asset search 自动执行。
- SQL validate 自动执行。
- EXPLAIN 自动执行，但必须先过 SQLGuard。
- 数据查询在低风险时可自动执行。
- 多表低置信 join 需要确认。
- 大表 scan 需要确认。
- 任何 DML/DDL 永远拒绝。

### 10.2 SQL 禁止项

必须拒绝：

- INSERT
- UPDATE
- DELETE
- DROP
- ALTER
- TRUNCATE
- CREATE
- REPLACE
- MERGE
- GRANT
- REVOKE
- CALL
- COPY PROGRAM
- INTO OUTFILE
- LOAD_FILE
- SLEEP
- BENCHMARK
- 多语句
- 动态执行
- dangerous pragma

### 10.3 只读执行

SQLite：

- 使用 `mode=ro` 打开。
- `PRAGMA query_only=ON`。
- progress handler timeout。

MySQL：

- `START TRANSACTION READ ONLY`。
- `max_execution_time` 或 MariaDB `max_statement_time`。
- finally rollback。

Postgres：

- `BEGIN READ ONLY`。
- `SET LOCAL statement_timeout`。
- finally rollback。

### 10.4 敏感信息

敏感信息策略：

- API Key 不默认落盘。
- 数据库密码优先 env var。
- 日志脱敏。
- LLM prompt 脱敏。
- 样本值可配置脱敏。
- 敏感列默认不发送给 LLM 样本值。

敏感列识别：

- email
- phone
- mobile
- password
- token
- secret
- id_card
- ssn
- address
- name
- ip

用户可在资产中手动标记敏感字段。

## 11. 异常与修复设计

错误需要结构化。

```text
DBAideError
  code
  stage
  message
  hint
  retryable
  repair_action
  evidence
```

错误类型：

- `CONNECTION_FAILED`
- `MODEL_UNAVAILABLE`
- `ASSET_MISSING`
- `ASSET_STALE`
- `SCHEMA_LINK_LOW_CONFIDENCE`
- `UNKNOWN_TABLE`
- `UNKNOWN_COLUMN`
- `UNSAFE_SQL`
- `SQL_EXPLAIN_FAILED`
- `SQL_EXECUTION_FAILED`
- `QUERY_TIMEOUT`
- `EMPTY_RESULT`
- `PERMISSION_DENIED`
- `USER_CANCELLED`

修复动作：

- `ASK_USER`
- `CONFIRM`
- `REBUILD_ASSET`
- `REFRESH_SCHEMA`
- `REPLAN`
- `RERENDER_SQL`
- `REVALIDATE`
- `REEXECUTE`
- `STOP`

每个 repair 必须有最大次数。

## 12. 细节设计补充清单

这一节专门记录容易遗漏但会显著影响成熟度的 UI/UX 细节。

### 12.1 Markdown 渲染细节

- 对话消息必须 Markdown 渲染。
- 资产文档必须 Markdown 渲染后预览。
- 错误说明允许 Markdown，但必须清洗 HTML。
- Markdown 表格如果来自查询结果，应优先转为表格组件。
- Markdown 中的 SQL fenced block 自动识别为 SQL。
- Markdown 渲染器必须禁用任意 HTML/script。
- Markdown link 支持内部跳转到资产、trace、history。
- Markdown 源码和渲染预览必须都可复制。

### 12.2 代码与 SQL 展示细节

- SQL 代码块必须高亮。
- SQL 代码块必须有复制按钮。
- SQL 代码块必须有打开到 SQL Tab 的按钮。
- SQL 代码块必须保留换行和缩进。
- SQL 中的错误位置如果可定位，应标红。
- 生成 SQL 与执行 SQL 必须区分显示。
- 用户修改过 SQL 后，必须显示 `modified` 标记，不能再声称它完全来自 agent。

### 12.3 表格结果细节

- 表格组件必须支持横向滚动。
- 表头固定。
- 行号可选显示。
- NULL 显示为灰色 `NULL`。
- 空字符串显示为 `""`。
- 布尔值显示为 true/false badge。
- 数值列右对齐。
- 日期列统一格式。
- 大字段默认截断。
- 单元格点击可打开详情。
- 支持复制单元格、复制行、复制列、复制 CSV。
- 支持导出 CSV，但导出大结果必须明确只导出当前已加载行还是重新查询全量。

### 12.4 JSON 预览细节

- JSON 使用树状折叠视图。
- 支持搜索 key/value。
- 支持复制 JSONPath。
- 支持复制节点 JSON。
- 超大 JSON 要虚拟滚动或分页。
- JSON parse 失败时显示错误位置和 raw text。

### 12.5 加载与取消细节

- 每个长任务必须显示 elapsed time。
- 每个长任务必须有取消按钮。
- 取消后状态是 `cancelled`，不是 `failed`。
- 取消必须写入 trace。
- 如果底层无法立即取消，显示 `Cancelling...`。
- 任务完成后按钮恢复，不能卡在 disabled。

### 12.6 空状态细节

- 无连接：显示添加连接、导入配置、打开 demo。
- 无资产：显示构建资产、跳过使用 live schema、查看说明。
- 无搜索结果：显示建议关键词和扩大范围。
- 无查询结果：显示 SQL 成功执行但返回 0 行，并提供放宽条件。
- 无历史：显示示例问题。
- 无模型：显示启发式能力范围和配置模型入口。

### 12.7 错误展示细节

- 错误卡必须显示 stage。
- 错误卡必须显示 user-friendly message。
- 错误卡必须显示 suggested actions。
- 技术详情默认折叠。
- 技术详情可复制。
- 可恢复错误提供 `Retry from this stage`。
- 不可恢复错误提供 `Export Debug Bundle`。

### 12.8 设置页细节

- API Key 输入默认不回显。
- 已保存 key 只显示 `••••` 和来源 env var。
- 保存明文 key 必须二次确认。
- 测试模型必须真实请求。
- 测试连接必须显示版本、当前用户、权限、耗时。
- SQLite 路径选择使用文件选择器。
- MySQL/Postgres port 自动默认。
- 设置保存后显示 diff 或摘要。

### 12.9 命令可复现细节

- 每个 GUI workflow 都能复制 CLI 复现命令。
- CLI 复现命令包含 connection、database、policy、limit、timeout。
- 如果问题包含换行，CLI 命令使用 heredoc 形式。
- Debug bundle 包含 config 摘要但不包含密钥。

### 12.10 可访问性与视觉细节

- 所有按钮有 tooltip。
- 所有 icon 旁有文本或 aria label。
- 错误不只依赖红色表达。
- 深色/浅色主题对比度达标。
- 字体大小可调。
- 表格和树支持键盘导航。
- 高 DPI 下布局不溢出。

### 12.11 测试验收细节

- Markdown 渲染必须有 XSS 回归测试。
- SQL 代码块复制必须有 UI/service 测试。
- JSON 大文件预览必须有性能测试。
- 表格 NULL/空字符串/数字/日期显示必须有快照测试。
- 取消长任务必须有集成测试。
- CLI 复现命令必须有 golden test。

### 12.12 核心数据结构契约

为了保证 CLI、GUI、未来 API 完全一致，核心返回结构必须稳定。

`WorkflowResult` 最小字段：

```text
workflow_id
status
question
connection_name
database_scope
execution_policy
answer_markdown
answer_plaintext
query_plan
sql_candidates[]
selected_sql
validation_report
execution_result
assumptions[]
warnings[]
errors[]
next_actions[]
trace_summary
created_at
completed_at
```

`AnswerCard` 最小字段：

```text
title
summary_markdown
status_badges[]
sql_block
result_table
assumptions[]
warnings[]
actions[]
source_workflow_id
```

`ResultTable` 最小字段：

```text
columns[]
rows[]
row_count
displayed_row_count
truncated
limit
elapsed_ms
column_metadata[]
cell_rendering_hints
export_capabilities[]
```

`TraceEvent` 最小字段：

```text
event_id
workflow_id
parent_event_id
timestamp
level
kind
stage
actor
title
summary
input_preview
output_preview
duration_ms
status
metadata
```

这些结构必须版本化：

```text
schema_version: 1
```

未来变更必须兼容旧历史记录。

### 12.13 事件流与 UI 更新契约

GUI 不应等待任务结束后一次性刷新，而应消费事件流。

事件类型：

```text
workflow_started
phase_started
phase_completed
tool_started
tool_completed
tool_failed
agent_message
plan_generated
sql_generated
validation_completed
confirmation_requested
user_replied
execution_started
execution_completed
result_interpreted
workflow_completed
workflow_failed
workflow_cancelled
```

UI 更新规则：

- `phase_started`：Trace 增加一行，Answer Card 显示当前阶段。
- `tool_started`：Trace 展示 running tool。
- `tool_completed`：更新工具结果摘要。
- `plan_generated`：Right Panel 的 Plan tab 更新。
- `sql_generated`：Answer Card 的 SQL 区出现。
- `confirmation_requested`：主区显示确认卡，Composer 进入等待状态。
- `execution_completed`：Result Table 更新。
- `workflow_failed`：显示 Error Card。

CLI 对应：

- 默认只显示关键事件。
- `--verbose` 显示所有 phase。
- `--show-trace` 显示完整 trace。
- `--json` 输出最终 `WorkflowResult`。

### 12.14 组件级状态机

每个关键 UI 组件必须有状态机，避免按钮乱禁用或状态不同步。

`Composer` 状态：

```text
idle
editing
submitting
running
waiting_user
waiting_confirm
cancelling
disabled_no_connection
disabled_no_input
```

`AnswerCard` 状态：

```text
streaming
planning
needs_confirmation
running_sql
completed
failed
cancelled
```

`SQLBlock` 状态：

```text
draft
generated
modified
validated
validation_failed
executed
```

`AssetTree` 状态：

```text
loading
ready
empty
missing_assets
stale
error
```

每个状态必须定义：

- 显示文本。
- 可用按钮。
- 禁用按钮。
- loading indicator。
- 可执行下一步。

### 12.15 性能预算

需要在设计中明确性能目标，否则 UI 和 agent 很容易变慢。

交互性能：

- GUI 启动到主窗口可操作：小于 2 秒。
- 切换 tab：小于 100ms。
- 打开中等资产 Markdown：小于 300ms。
- 搜索资产：小于 500ms。
- schema tree 展开：小于 200ms。

Agent 性能：

- 简单 schema 问题：小于 1 秒。
- 简单 SQL validate：小于 500ms。
- 简单单表查询生成：小于 3 秒。
- 复杂多表查询首个计划：小于 10 秒内必须有可见进度。
- 任意超过 2 秒的任务必须显示当前阶段。

数据性能：

- 表格超过 1000 行必须虚拟滚动。
- JSON 超过 1MB 必须延迟渲染或折叠。
- Markdown 超过 200KB 必须分块或虚拟滚动。
- 大结果不允许一次性塞进 QTextEdit。

资产构建性能：

- 必须显示每秒处理表/列数量。
- 必须支持 per-column timeout。
- 单列失败不影响整体。
- 构建日志不能无限增长导致 UI 卡顿。

### 12.16 可观测性与日志细节

生产级 agent 必须可观测。

记录指标：

- workflow_count
- workflow_success_rate
- workflow_failure_rate
- avg_latency
- p95_latency
- llm_call_count
- llm_failure_count
- tool_call_count
- sql_validation_failure_count
- sql_execution_failure_count
- user_confirmation_count
- user_cancellation_count
- asset_build_duration

每个 workflow 记录：

- 模型名称。
- 执行策略。
- 工具调用数。
- SQL 执行数。
- 失败阶段。
- 修复次数。
- 是否用户确认。

日志分级：

- 默认日志不包含样本值。
- debug 日志也要脱敏。
- 用户可导出 debug bundle。
- debug bundle 必须排除密码、API key、token。

### 12.17 隐私与脱敏细节

LLM prompt 发送前必须经过脱敏层。

默认脱敏：

- email -> `<EMAIL>`
- phone -> `<PHONE>`
- token/api key -> `<SECRET>`
- password -> `<PASSWORD>`
- id card / ssn -> `<ID>`
- address -> `<ADDRESS>`

资产 sample values：

- 敏感列默认不写入 Markdown。
- 敏感列 JSON 中也只保留统计，不保留原始值，除非用户显式允许。
- GUI 显示敏感列时默认遮罩。

用户确认：

- 第一次读取敏感列样本值时必须确认。
- 第一次发送样本值给 LLM 时必须确认或在设置中允许。

设置项：

```text
Send schema comments to LLM: on/off
Send sample values to LLM: off by default
Mask sensitive values in UI: on by default
Store query history: on/off
Store result rows in history: off by default
```

### 12.18 模型配置细节

模型配置不能只保存 base_url/key/model。

应包含：

```text
provider
base_url
model
api_key_env
timeout_seconds
max_retries
supports_json_schema
supports_tool_calling
supports_streaming
context_window
default_temperature
```

测试模型时：

- 发起真实最小请求。
- 校验 JSON 输出能力。
- 校验超时设置。
- 显示响应耗时。
- 显示错误类型。

如果模型不支持 JSON schema：

- AgentRuntime 使用 JSON repair fallback。
- UI 显示能力降级提示。

### 12.19 连接与数据库上下文细节

连接上下文必须清楚。

每个 workflow 绑定：

- connection name。
- database/schema scope。
- adapter dialect。
- user。
- read-only status。
- asset version。

切换连接时：

- 不清空历史。
- 不复用旧连接的 schema context。
- Composer 显示当前连接 chip。
- 如果有未完成 workflow，提示是否继续、取消或切换。

多库场景：

- `Auto` 模式必须在 schema linking 阶段记录候选库。
- 如果多个库都有相似表，必须澄清或展示候选。

### 12.20 资产 Markdown 模板细节

Markdown 资产应统一模板，便于阅读和 diff。

`table.md` 模板：

```markdown
# table_name

## Summary
...

## Columns
| Column | Type | Role | Nullable | Indexed | Description |

## Keys And Relations
...

## Common Questions
...

## Data Quality
...

## Safety
...

## Build Metadata
...
```

`column.md` 模板：

```markdown
# table.column

## Meaning
...

## Type And Constraints
...

## Profile
...

## Sample Values
...

## Usage Hints
...

## Safety
...
```

Markdown 中的表格必须能被 GUI 正确渲染，也能被 CLI `dbaide doc` 输出。

### 12.21 历史记录细节

历史记录默认保存：

- 问题。
- SQL。
- 结果摘要。
- trace。
- 错误。
- 用户确认。
- 执行耗时。

默认不保存：

- 完整结果行。
- 敏感 sample values。
- 明文密钥。

用户可以设置：

- 保存结果摘要。
- 保存前 N 行结果。
- 不保存历史。

历史记录支持：

- 按连接过滤。
- 按状态过滤。
- 按日期过滤。
- 搜索问题和 SQL。
- 重新运行。
- 复制 CLI 复现命令。

### 12.22 主题与视觉系统细节

需要定义设计 token：

```text
color.background
color.surface
color.border
color.text.primary
color.text.secondary
color.warning
color.danger
color.success
font.ui
font.mono
spacing.xs/s/m/l/xl
radius.s/m/l
```

不同信息类型的颜色：

- success：绿色。
- warning：黄色。
- danger：红色。
- info：蓝色。
- muted：灰色。

禁止只用颜色表达状态，必须搭配文字或图标。

### 12.23 国际化细节

中英文都要完整覆盖。

所有 UI 文案必须走 i18n key。

包括：

- 按钮。
- tooltip。
- error message。
- empty state。
- confirmation card。
- settings。
- trace stage。
- validation issue。

错误码不翻译，错误说明翻译。

### 12.24 打包与分发细节

生产级工具需要考虑安装。

要求：

- CLI `pip install` 后可用。
- GUI `dbaide-gui` 可启动。
- 缺 PyQt 时 CLI 不受影响。
- 首次启动能创建配置目录。
- 配置目录和资产目录可在设置中打开。
- 版本升级时执行配置/资产迁移。

### 12.25 迁移策略细节

从当前项目迁移到新架构时，不应一次性推倒。

兼容策略：

- 保留当前 CLI 命令。
- 新 `WorkflowEngine` 先包裹现有 `DataAssistant`。
- 新 `ToolRegistry` 先适配现有 `SchemaTools`、`QueryTools`、`ProfileTools`。
- 新 `WorkflowResult` 先从现有 `AssistantResponse` 转换。
- GUI 先消费新 result，再逐步替换旧页面。
- 资产 JSON 保持兼容，新增 Markdown 旁路生成。

迁移验收：

- 老命令不破坏。
- 老资产可读取。
- 新资产可回退为 JSON。
- 测试覆盖新旧路径。

## 13. ARDAgent 实现规格

这一节面向后续实现 agent。目标是让另一个 ARDAgent 可以不重新理解产品方向，直接按模块、接口、任务和验收标准推进实现。

### 13.1 实现总原则

实现时必须遵守：

- 不一次性重写全项目。
- 先建立核心契约，再逐步替换旧逻辑。
- CLI 和 GUI 必须同时消费核心结果。
- 每一步都要有测试。
- 每个新增能力都要保持现有命令兼容。
- 所有危险动作都要先经过核心 guard。
- 所有 UI 渲染都要经过安全 renderer。

禁止：

- 在 GUI 中直接拼业务逻辑。
- 在 CLI 中绕过 `WorkflowEngine`。
- 在 agent 中直接调用 adapter。
- 在 SQL 执行前跳过 validation。
- 在 Markdown 渲染中允许原始 HTML。
- 在配置中默认保存明文密钥。

### 13.2 推荐目标目录结构

新增或重构目录：

```text
dbaide/core/
  __init__.py
  workflow.py
  events.py
  result.py
  errors.py
  policy.py
  cancellation.py

dbaide/agent/
  runtime.py
  router.py
  schema_linker.py
  query_planner.py
  plan_validator.py
  sql_renderer.py
  sql_validator.py
  risk_controller.py
  result_interpreter.py
  error_router.py
  prompts/

dbaide/tools/
  registry.py
  specs.py
  asset.py
  schema.py
  profile.py
  query.py
  diagnose.py
  interaction.py

dbaide/rendering/
  markdown.py
  sql.py
  json_view.py
  table.py
  sanitize.py

dbaide/history/
  store.py
  models.py

dbaide/eval/
  runner.py
  metrics.py
```

原则：

- `core/` 不依赖 GUI。
- `agent/` 不依赖 GUI。
- `tools/` 不依赖 GUI。
- `gui_app/` 只依赖 `core` 输出和服务层。
- `cli.py` 只做参数解析和结果呈现。

### 13.3 第一批必须新增的数据模型

文件：`dbaide/core/result.py`

定义：

```python
class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAIT_USER = "wait_user"
    NEED_CONFIRM = "need_confirm"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ExecutionPolicy(str, Enum):
    INSPECT_ONLY = "inspect_only"
    SQL_ONLY = "sql_only"
    SAFE_AUTO = "safe_auto"
    EXPERT = "expert"
```

```python
@dataclass(slots=True)
class WorkflowResult:
    schema_version: int
    workflow_id: str
    status: WorkflowStatus
    question: str
    connection_name: str
    database_scope: list[str]
    execution_policy: ExecutionPolicy
    answer_markdown: str = ""
    answer_plaintext: str = ""
    query_plan: QueryPlan | None = None
    sql_candidates: list[SQLCandidate] = field(default_factory=list)
    selected_sql: str = ""
    validation_report: ValidationReport | None = None
    execution_result: QueryResult | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[DBAideError] = field(default_factory=list)
    next_actions: list[NextAction] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    created_at: float = 0.0
    completed_at: float = 0.0
```

验收：

- 可序列化为 JSON。
- CLI `--json` 能输出。
- GUI 能直接消费。
- 旧 `AssistantResponse` 可转换为 `WorkflowResult`。

文件：`dbaide/core/events.py`

定义：

```python
@dataclass(slots=True)
class TraceEvent:
    event_id: str
    workflow_id: str
    parent_event_id: str = ""
    timestamp: float = 0.0
    level: str = "info"
    kind: str = "system"
    stage: str = ""
    actor: str = ""
    title: str = ""
    summary: str = ""
    input_preview: str = ""
    output_preview: str = ""
    duration_ms: float = 0.0
    status: str = "completed"
    metadata: dict[str, Any] = field(default_factory=dict)
```

验收：

- 工具调用、agent 调用、validation、execution 都生成 event。
- GUI Trace 使用该结构渲染。
- CLI `--show-trace` 使用该结构输出。

文件：`dbaide/core/errors.py`

定义：

```python
@dataclass(slots=True)
class DBAideError:
    code: str
    stage: str
    message: str
    hint: str = ""
    retryable: bool = False
    repair_action: str = "stop"
    evidence: dict[str, Any] = field(default_factory=dict)
```

验收：

- 所有用户可见错误都通过 `DBAideError`。
- 不再直接把原始 exception 当主文案。

### 13.4 ToolRegistry 实现规格

文件：`dbaide/tools/specs.py`

定义：

```python
@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission_level: str
    timeout_seconds: int
    max_rows: int | None = None
    cache_policy: str = "none"
    safe_for_auto_call: bool = True
```

文件：`dbaide/tools/registry.py`

必须提供：

```python
class ToolRegistry:
    def register(self, spec: ToolSpec, handler: Callable[..., Any]) -> None: ...
    def spec(self, name: str) -> ToolSpec: ...
    def list_specs(self, permission_level: str | None = None) -> list[ToolSpec]: ...
    def invoke(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult: ...
```

`ToolContext` 包含：

```text
workflow_id
connection
adapter
asset_store
session
execution_policy
cancellation_token
trace_sink
```

第一批工具：

- `search_assets`
- `list_databases`
- `list_tables`
- `describe_table`
- `show_table_doc`
- `show_column_doc`
- `validate_sql`
- `explain_sql`
- `execute_readonly_sql`
- `profile_table`
- `profile_column`
- `build_assets`
- `ask_user`
- `request_confirmation`

验收：

- 所有工具调用都有 trace event。
- 工具失败返回 `ToolResult(ok=False, error=DBAideError)`。
- `execute_readonly_sql` 内部强制要求 validation token 或 validation report。
- GUI 和 CLI 都不直接调用底层工具类，而是通过 registry。

### 13.5 WorkflowEngine 实现规格

文件：`dbaide/core/workflow.py`

接口：

```python
class WorkflowEngine:
    def run(self, request: WorkflowRequest) -> WorkflowResult: ...
    def stream(self, request: WorkflowRequest) -> Iterator[TraceEvent | WorkflowResult]: ...
    def resume(self, workflow_id: str, user_input: UserReply) -> WorkflowResult: ...
    def cancel(self, workflow_id: str) -> None: ...
```

`WorkflowRequest`：

```text
question
connection_name
database_scope
mode
execution_policy
limit
timeout_seconds
model_name
show_trace
```

阶段：

1. `create_workflow`
2. `check_environment`
3. `route_request`
4. `collect_context`
5. `schema_link`
6. `build_plan`
7. `validate_plan`
8. `render_sql`
9. `validate_sql`
10. `risk_decision`
11. `execute_sql`
12. `interpret_result`
13. `finalize`

验收：

- 每个阶段都有开始/结束 trace。
- 任一阶段失败进入 `ErrorRouter`。
- 任一阶段可被 cancellation token 中断。
- `SQL_ONLY` policy 不执行 SQL。
- `INSPECT_ONLY` policy 不进入 SQL render。

### 13.6 AgentRuntime 实现规格

文件：`dbaide/agent/runtime.py`

接口：

```python
class AgentRuntime:
    def run_json(
        self,
        agent_name: str,
        messages: list[LLMMessage],
        output_model: type[T],
        *,
        tools: list[ToolSpec] | None = None,
        trace: TraceSink,
        cancellation_token: CancellationToken,
    ) -> T: ...
```

必须实现：

- LLM unavailable fallback。
- JSON parse。
- Pydantic/dataclass validation。
- 最多 2 次 JSON 修复重试。
- post_validate hook。
- token/耗时记录。
- 错误转换为 `DBAideError`。

验收：

- malformed JSON 可触发 repair retry。
- 超时可中断。
- LLM 不可用时返回明确 error 或 heuristic fallback。

### 13.7 渲染层实现规格

文件：`dbaide/rendering/sanitize.py`

必须提供：

```python
def escape_user_text(text: str) -> str
def sanitize_markdown_html(html: str) -> str
def redact_sensitive_text(text: str) -> str
```

文件：`dbaide/rendering/markdown.py`

必须提供：

```python
def render_markdown(markdown: str) -> RenderedMarkdown
```

要求：

- 禁用 raw HTML 或严格 sanitize。
- 支持 fenced code block。
- 支持表格。
- 支持内部链接协议，如 `dbaide://asset/...`。

文件：`dbaide/rendering/sql.py`

必须提供：

```python
def format_sql(sql: str, dialect: str) -> str
def highlight_sql(sql: str, dialect: str) -> str
```

文件：`dbaide/rendering/table.py`

必须提供：

```python
def infer_column_render_hints(result: QueryResult) -> list[ColumnRenderHint]
def export_csv(result: QueryResult) -> str
def export_markdown_table(result: QueryResult) -> str
```

验收：

- Markdown XSS 测试通过。
- SQL copy/open actions 可拿到原始 SQL。
- NULL/空字符串显示正确。

### 13.8 GUI 实现任务拆分

第一步：不要一口气重写全部 GUI。先新增可复用组件，再替换主窗口。

新增组件建议：

```text
dbaide/gui_app/components/
  markdown_view.py
  sql_block.py
  result_table.py
  trace_panel.py
  plan_panel.py
  error_card.py
  confirmation_card.py
  empty_state.py
  command_palette.py
```

`MarkdownView`

- 输入 markdown。
- 输出渲染预览。
- 支持复制源码和复制纯文本。
- 禁止 raw HTML。

`SQLBlock`

- 显示 SQL。
- 显示 validation status。
- 按钮：Copy、Open in SQL Tab、Explain、Run。

`ResultTable`

- 接收 `ResultTable` 数据结构。
- 支持虚拟滚动。
- 支持复制单元格/行/CSV。

`TracePanel`

- 接收 `TraceEvent[]`。
- 支持筛选 stage/kind/status。
- 点击展开详情。

`PlanPanel`

- 接收 `QueryPlan`。
- 显示表、列、join、filters、aggregations、confidence。
- 高级模式支持编辑。

`ErrorCard`

- 接收 `DBAideError`。
- 展示 message、hint、actions、technical details。

`ConfirmationCard`

- 接收 confirmation request。
- 展示 reason、risk、SQL preview、buttons。

验收：

- 每个组件有最小单元测试或 snapshot。
- 主窗口不再用字符串拼 HTML。
- 所有内容从结构化 result 渲染。

### 13.9 CLI 实现任务拆分

现有 CLI 命令保留，新增统一参数：

```text
--json
--show-trace
--policy inspect-only|sql-only|safe-auto|expert
--workflow-id
--resume
--export-debug
```

`dbaide ask`

- 调用 `WorkflowEngine.run()`。
- 默认输出 answer markdown 的 plaintext 或 markdown。
- `--json` 输出 `WorkflowResult`。
- `--show-trace` 输出 trace。

`dbaide sql`

- 走 `WorkflowEngine` 的 SQL mode。
- 不允许绕过 validator。

`dbaide assets`

- 构建时生成 JSON + Markdown。
- 输出进度事件。

新增：

```text
dbaide runs list
dbaide runs show <workflow_id>
dbaide runs export <workflow_id>
```

验收：

- 旧 CLI E2E 继续通过。
- 新 `--json` 有 golden snapshot。
- GUI 复制的 CLI 命令可运行。

### 13.10 测试文件建议

新增测试：

```text
tests/test_workflow_engine.py
tests/test_workflow_result.py
tests/test_tool_registry.py
tests/test_agent_runtime.py
tests/test_schema_linker.py
tests/test_query_plan.py
tests/test_sql_validator.py
tests/test_risk_controller.py
tests/test_render_markdown.py
tests/test_render_table.py
tests/test_history_store.py
tests/test_cli_workflow.py
tests/test_gui_components.py
```

关键测试用例：

- 无模型时 schema explore 可用。
- 无资产时 live schema fallback 可用。
- 低置信 join 触发 confirmation。
- 多候选时间列触发 ask_user。
- 危险 SQL 被拒绝。
- SQL_ONLY 不执行。
- SAFE_AUTO 只执行低风险。
- Markdown XSS 被清洗。
- JSON preview 大文件不卡死。
- ResultTable 正确显示 NULL 和空字符串。
- CLI `--json` 和 GUI 消费结构一致。

### 13.11 分 PR 实施建议

PR 1：核心模型与 trace

- 新增 `core/result.py`、`core/events.py`、`core/errors.py`。
- 旧 `AssistantResponse` 转 `WorkflowResult`。
- CLI `ask --json` 输出新结构。

PR 2：ToolRegistry

- 新增 tool spec。
- 包装现有 `SchemaTools`、`QueryTools`、`ProfileTools`、`AssetSearch`。
- 所有工具调用生成 trace。

PR 3：安全渲染

- 新增 `rendering/`。
- GUI 聊天内容改用 MarkdownView。
- 修复 HTML 注入。

PR 4：WorkflowEngine wrapper

- 新 `WorkflowEngine` 包裹现有 `DataAssistant`。
- 实现 phase trace。
- CLI/GUI 初步切换到 engine。

PR 5：SQL validation 加强

- 顶层 limit。
- 列校验。
- validation report。
- SQL_ONLY / SAFE_AUTO policy。

PR 6：GUI 组件

- SQLBlock。
- ResultTable。
- TracePanel。
- ErrorCard。
- ConfirmationCard。

PR 7：资产 Markdown

- 生成 instance/database/table/column markdown。
- Assets Tab 默认渲染 Markdown Preview。
- JSON Preview 可折叠。

PR 8：SchemaLinker + QueryPlan

- 多候选表列。
- QueryPlan。
- PlanPanel。
- 低置信澄清。

PR 9：History and debug bundle

- runs list/show/export。
- GUI History。
- Debug bundle 脱敏。

PR 10：Golden eval

- eval runner。
- golden dataset。
- 指标输出。

### 13.12 每个 PR 的完成定义

每个 PR 必须满足：

- 不破坏现有 CLI 基本用法。
- 新增或更新测试。
- 文档中的对应条目可勾选。
- 无明文密钥写入。
- SQL 执行路径仍只读。
- GUI 不新增未清洗 HTML。
- 错误有 `DBAideError` 或兼容转换。
- 变更有最小手动验证步骤。

### 13.13 手动验证脚本

每轮实现后至少验证：

```bash
dbaide --version
dbaide connect list
dbaide assets status local
dbaide find "用户邮箱" --conn local
dbaide ask "有哪些表" --conn local --json
dbaide ask "最近7天每天订单数" --conn local --policy sql-only --show-trace
dbaide sql "select 1" --conn local --execute
dbaide diagnose "select * from users" --conn local
dbaide tree --conn local
dbaide doc --conn local --out schema.md
```

GUI 手动验证：

- 启动无连接欢迎页。
- 添加 SQLite 连接。
- 构建资产。
- 打开资产 Markdown 预览。
- Ask 一个简单问题。
- Ask 一个需要澄清的问题。
- 打开 SQL Tab 执行 select 1。
- 查看 Trace。
- 复制 CLI 复现命令。
- 导出 debug bundle。

### 13.14 ARDAgent 工作提示

如果让另一个 ARDAgent 实现，应给它以下工作准则：

```text
你不是在重写一个 demo，而是在把现有 DBAide 渐进升级为 Claude/Codex 风格 DB agent。
不要删除现有 CLI 能力。
不要绕过现有 adapter。
优先建立核心结构和兼容适配层。
每个阶段都必须有测试。
任何 SQL 执行必须保持只读。
任何 UI 文本渲染必须防注入。
每次只做一个可验证 PR。
```

## 14. BUG 修复清单

### P0

- 修复 GUI HTML 注入：所有聊天内容 HTML escape。
- 禁止默认明文保存密码和 API Key。
- 修复模型测试只构造 client 不实际请求的问题。
- 修复 GUI 构建资产阻塞 UI 线程。
- 修复 worker cancel 无法中断底层任务。
- 修复 `BuildStats` 并发线程安全问题。
- 修复 SQL 顶层 limit 识别。
- 修复 SchemaGuard 只校验表不校验列。
- 修复 QueryResult `truncated` 永远不准的问题。
- 修复 `SQL_REWRITE`、`DB_COMPARE`、`EXPORT` 无 handler 却被路由的问题。
- 修复 CLI ask 未显式注入共享 `AssetStore` 的路径不一致。
- 修复 GUI `app.py` 调用 `set_page` 但 `MainWindow` 无该方法的问题。
- 修复 GUI 新旧 pages 体系分裂。
- 修复 agent 校验失败 retry 不带错误反馈的问题。
- 修复多实例查询失败被吞成普通答案的问题。

### P1

- 增加结构化 `WorkflowResult`。
- 增加 `RunTrace`。
- 增加 `AskTicket`。
- 增加 `ToolRegistry`。
- 增加 `QueryPlan`。
- 增加 `PlanValidator`。
- 增加 `RiskController`。
- 增加 `ErrorRouter`。
- 增加 Markdown 资产。
- 增加资产 stale 检测。
- 增加结果表格组件。
- 增加 trace panel。
- 增加 plan panel。
- 增加 SQL editor。
- 增加历史记录。
- 增加 golden eval。

### P2

- 可选 embedding provider。
- Web UI 或 local server mode。
- 团队共享资产。
- 查询收藏。
- 自然语言数据字典编辑。
- 可视化 join graph。
- MCP/plugin 工具接口。

## 15. 推荐实施路线

### Phase 1：安全与稳定

目标：现有功能不炸、不泄漏、不误执行。

完成：

- HTML escape。
- 密钥策略。
- SQLGuard/SchemaGuard 修复。
- GUI 长任务 worker。
- cancel token。
- P0 测试修复。
- README 与实际 GUI 对齐。

### Phase 2：统一核心工作流

目标：CLI/GUI 共享同一核心。

完成：

- `WorkflowEngine`。
- `WorkflowResult`。
- `RunTrace`。
- `DBAideError`。
- `ToolRegistry`。
- `AgentRuntime`。

### Phase 3：Text-to-SQL 核心升级

目标：从直接写 SQL 升级为计划驱动。

完成：

- `SchemaLinker`。
- `QueryPlan`。
- `PlanValidator`。
- `SQLRenderer`。
- `SQLValidator`。
- `RiskController`。
- 多表 join path。
- 澄清恢复。

### Phase 4：UI 全重构

目标：从简单聊天框变成专业 DB agent 工作台。

完成：

- Top Bar。
- Left Sidebar。
- Ask Tab。
- SQL Tab。
- Assets Tab。
- History Tab。
- Right Trace/Plan/Inspector。
- Bottom Composer。
- 结构化结果卡。

### Phase 5：资产与评测

目标：可持续提升准确率。

完成：

- Markdown 资产。
- 资产预览。
- 资产编辑覆盖。
- golden dataset。
- `dbaide eval`。
- CI。

## 16. 生产验收标准

安全：

- 所有 SQL 默认只读。
- 危险 SQL 100% 拒绝。
- 密钥不默认明文落盘。
- 日志和 prompt 脱敏。

正确性：

- schema linking 结果可解释。
- SQL 中所有表列可追溯。
- 多表 join 有来源和置信度。
- 低置信问题必须澄清。

体验：

- 用户能看到计划、SQL、结果、假设、警告。
- 每个错误都有阶段、原因和下一步。
- 所有长任务可取消。
- 资产可读、可预览、可搜索。

性能：

- 简单 schema/find 请求低延迟。
- 复杂请求有进度反馈。
- 大库资产构建可增量、可取消、可恢复。

可维护性：

- prompt 集中管理。
- 工具有 schema。
- agent 输出有校验。
- workflow 有 trace。
- 核心逻辑与 UI 解耦。

## 17. 最终愿景

最终的 DBAide 应该像一个数据库领域的 Claude Code：

用户不是在“让模型猜 SQL”，而是在和一个可靠的数据库助手协作。它会先看上下文，告诉你它找到了哪些表和字段，指出不确定的地方，请你确认关键业务口径，然后生成 SQL，验证 SQL，安全执行，并解释结果。它既能给新手一个清晰答案，也能给专家完整证据链。它的离线资产让数据库知识逐渐沉淀，越用越准确，越用越像团队内部的数据地图。

这才是 DBAide 和 AskDB-Public 的根本区别：DBAide 不是研究型 Text-to-SQL pipeline，而是一个面向真实工作流、真实用户、真实数据库风险的生产级 DB agent。
