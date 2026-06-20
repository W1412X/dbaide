# DBAide 宣传截图文案

## 主标题
DBAide：面向真实数据库的 AI 数据分析与开发工作台

## 副标题
从资产初始化、结构理解、SQL 生成、风险校验到图表回答，技术人员和业务人员可以在同一个本地优先工作流里协作。

## 截图与配文

1. `01-assets-initializing.png`
   资产初始化不再是黑盒：左侧结构树边构建边更新，进度明确到已构建表数与当前表。

2. `02-runtime-thinking.png`
   复杂问题运行时可见：意图拆解、结构发现、关联校验、SQL 生成与风险检查都能追踪。

3. `17-agent-trace.png`
   Trace 不再是树状噪音，而是右侧时间线抽屉：步骤、耗时和详情分层查看。

4. `03-chart-answer-analysis.png`
   业务问题直接给出结构化结论：摘要、关键发现、趋势折线与双轴组合图同屏可读。

5. `04-chart-answer-breakdown.png`
   回答连续展示多种图表类型：堆叠面积、柱状、环形图、漏斗、仪表盘、热力图与库存风险条。

6. `05-clarification.png`
   当口径不唯一时先澄清：避免 AI 擅自假设财务归属、取消订单和差异阈值。

7. `06-database-client-sql.png`
   内置数据库客户端：多标签 SQL 编辑、结果表格、导出、历史和结构树在同一界面，SQL 证据可继续复核。

8. `07-database-client-table.png`
   表数据浏览与结构查看一体化：适合开发排障，也适合业务同学快速核对明细。

9. `08-developer-field-exploration.png`
   开发者专项：当字段名不存在时，Agent 会先查字段、读表结构、验证关联路径，再自动改写成可执行 SQL。

10. `09-developer-consistency-audit.png`
   开发者专项：跨 orders/payments/refunds/ledger_entries 自动对账，表格结论配合柱状、环形与桑基图展示异常分布与资金链路。

11. `10-settings-connections.png`
    连接管理、导入导出、默认连接切换都在一个面板里完成，便于团队迁移与环境管理。

12. `11-settings-models.png`
    模型配置与超时、上下文长度、API 凭据分离管理；桌面与 CLI 共享同一套模型配置。

13. `12-settings-resources.png`
    所有关键资源限制都可配置：SQL 超时、行数上限、Agent 步数、压缩阈值、结果截断长度与并发运行数。

14. `13-settings-integrations.png`
    MCP / coding tool 集成页可直接安装到 Claude、Codex、Cursor 等工具，并支持 full / ask / tools 三种模式。

15. `14-backup-manager.png`
    备份管理器统一查看历史备份、格式、行数、大小和文件位置，适合做本地快照与审计留存。

16. `15-build-assets-dialog.png`
    构建资产支持按库选择、并发与时间预算设置，不必每次重扫整实例。

17. `16-connection-dialog.png`
    连接表单内置只读负载配置、会话时区和 SSL 选项，便于安全地接入生产或分析库。

## 面向技术人员
- 看得见 agent 的每一步，便于调试 prompt、SQL、join 推断和性能风险。
- 本地优先连接数据库，内置资源限制、只读校验、EXPLAIN/扫描风险思路。
- 资产层把结构、外键、索引、样本、用户备注沉淀为可复用上下文。
- 开发排障可以从“字段是否存在”一直推进到跨表一致性校验、异常分桶和修复建议。

## 面向业务人员
- 直接用自然语言问复杂业务问题，不需要先知道表名和 join 关系。
- 图表、结论、SQL 证据同时输出，既能快速决策，也能交给技术复核。
- 遇到口径歧义会主动澄清，减少“看起来合理但口径错误”的结果。

## 生成的文件
- `01-assets-initializing.png`
- `02-runtime-thinking.png`
- `17-agent-trace.png`
- `03-chart-answer-analysis.png`
- `04-chart-answer-breakdown.png`
- `05-clarification.png`
- `06-database-client-sql.png`
- `07-database-client-table.png`
- `08-developer-field-exploration.png`
- `09-developer-consistency-audit.png`
- `10-settings-connections.png`
- `11-settings-models.png`
- `12-settings-resources.png`
- `13-settings-integrations.png`
- `14-backup-manager.png`
- `15-build-assets-dialog.png`
- `16-connection-dialog.png`