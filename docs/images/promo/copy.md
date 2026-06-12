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

3. `03-chart-answer-analysis.png`
   业务问题直接给出结论、证据和图表：净收入、退款率、渠道表现一屏可读。

4. `04-chart-answer-breakdown.png`
   回答不是纯文本：渠道拆解、库存风险和后续建议可以连续展示，适合业务复盘。

5. `05-clarification.png`
   当口径不唯一时先澄清：避免 AI 擅自假设财务归属、取消订单和差异阈值。

6. `06-database-client-sql.png`
   内置数据库客户端：多标签 SQL 编辑、结果表格、导出、历史和结构树在同一界面，SQL 证据可继续复核。

7. `07-database-client-table.png`
   表数据浏览与结构查看一体化：适合开发排障，也适合业务同学快速核对明细。

8. `08-developer-field-exploration.png`
   开发者专项：当字段名不存在时，Agent 会先查字段、读表结构、验证关联路径，再自动改写成可执行 SQL。

9. `09-developer-consistency-audit.png`
   开发者专项：跨 orders/payments/refunds/ledger_entries 自动对账，继续探索异常分桶和根因，而不是停在单条 SQL。

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
- `03-chart-answer-analysis.png`
- `04-chart-answer-breakdown.png`
- `05-clarification.png`
- `06-database-client-sql.png`
- `07-database-client-table.png`
- `08-developer-field-exploration.png`
- `09-developer-consistency-audit.png`