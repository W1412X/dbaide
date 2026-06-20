# DBAide 开发 BUG 记录

项目内已发现、已修复或需留意的缺陷台账。新条目请按编号递增，并附上回归测试位置（如有）。

---

## GUI-001 · macOS `setParent(None)` 导致幽灵顶层窗口

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 高（用户可见闪烁 / 假弹窗） |
| **平台** | macOS（其他平台也可能出现短暂浮层） |
| **发现** | 2026-06-20，trace drawer live 更新录屏 |

### 现象

在 trace drawer 打开且 agent 仍在 running 时，连续几帧出现：

1. 屏幕中央浮出白色圆角 step 卡片（与 drawer 内容重复）
2. 右侧 drawer 标题在、timeline 空白
3. 中央出现带红绿灯的小黑空窗
4. 内容又回到 drawer 内

### 根因

动态重建 UI 时，对仍可见的 `QWidget` 调用 `setParent(None)` 再 `deleteLater()`。在 macOS 上 Qt 会将其**短暂提升为独立顶层窗口**（带原生标题栏），直到事件循环销毁。

常见触发路径：layout `takeAt` / `removeWidget` 后多余地 `setParent(None)`。

### 修复

- 新增 `dbaide/desktop/components/base.py`：`discard_widget()`、`clear_layout_widgets()`
- 规范：**只** `hide()` + `deleteLater()`，**禁止**在销毁流程里 `setParent(None)`
- 已替换文件：
  - `dbaide/desktop/components/trace.py` — timeline `_clear_cards`
  - `dbaide/desktop/components/conversation.py` — agenda、stream/markdown 切换、clarification、footer、turn 清理
  - `dbaide/desktop/components/composer.py` — attachment chips
  - `dbaide/desktop/views/workbench.py` — 关闭 tab 文档
  - `dbaide/desktop/views/ask_tab.py` — 丢弃 session view

### 回归测试

- `tests/test_gui_trace_smoke.py::test_live_trace_rebuild_does_not_orphan_cards_as_windows`
- `tests/test_bug_audit_fixes.py::test_discard_widget_does_not_orphan_top_level_window`
- `tests/test_bug_audit_fixes.py::test_clear_layout_widgets_does_not_orphan_top_level_window`

### 开发约定

布局重建时统一使用：

```python
from dbaide.desktop.components.base import discard_widget, clear_layout_widgets

clear_layout_widgets(some_layout)          # 清空 layout 内所有 widget
layout.removeWidget(w); discard_widget(w)  # 单个移除
```

---

## GUI-005 · Trace timeline 与执行树结构不兼容

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 高（live 仅见 Agent loop，完成后步骤数突变） |
| **平台** | 全平台 |

### 现象

- 运行中 drawer 往往只显示 1–2 个顶层 Phase（Environment + Agent loop）
- 工具调用、决策、子 agent 都折叠在 loop 卡片内，需手动展开
- `complete_turn` 后步骤统计与 UI 感知不一致（根节点数 ≠ 实际工作量）
- Live 与 finalized 看起来像两套 UI，实为同一棵树只展示 `model.steps` 顶层

### 根因

`build_trace_timeline()` 仅映射 `TraceModel.root.children`。真实运行中工具步骤通过 `parent_id=loop` 挂在 Agent loop 容器下，timeline 只渲染容器本身。

`TraceModel` 树仍用于 `render_trace_text` / detail 面板，但 UI timeline 误用了树的顶层切片。

### 修复

- **`build_trace_timeline()`** 改为**扁平、按 `started_at` 排序**的时间线：unwrap loop 容器，每个 decision/tool/substep 独立一行，用 `depth` 缩进
- 保留 **`build_trace_tree_timeline()`** 供嵌套视图/测试
- **`count_timeline_steps()` / `step_count_from_events()`** 统一 footer、drawer summary、chip 步骤计数
- **`dbaide/step_budget.py`** 统一 agent 步数预算常量（`DEFAULT_AGENT_MAX_STEPS` 等）与子 agent 预算
- **`render_trace_text()`** 不变，完整执行日志（args/sql/llm_calls/raw）仍从树导出

### 回归测试

- `tests/test_trace_model.py::test_build_trace_timeline_unwraps_loop_and_orders_chronologically`
- `tests/test_trace_model.py::test_render_trace_text_still_exports_full_tree_after_flat_timeline`
- `tests/test_trace_model.py::test_timeline_hides_workflow_synthetic_stages_when_tools_exist`

---

## GUI-006 · Workflow 末尾注入固定伪步骤（sql_generated / validate / interpret）

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 高（完成态 trace 与 live 不一致，末尾总是 SQL→校验→解释） |
| **平台** | 全平台 |

### 现象

Agent loop 已逐步 emit `generate_sql` / `validate_sql` / `execute_sql` 等真实 tool 事件，但 `WorkflowEngine.run` 结束后又追加：

- `sql_generated` → `sql_validation` → `execution_completed` → `result_interpreted` → `workflow_completed`

这些**只在持久化 trace 里出现**，live drawer 看不到，完成瞬间 timeline 末尾突然多出固定三步。

### 根因

`dbaide/core/workflow.py` 把 workflow 层摘要当成 trace 步骤写入，与 loop 内真实 tool 路径重复。

### 修复

- **停止**在 workflow 结束时 `_trace()` 上述伪步骤（仍保留 `validation_report` / `execution_result` 等结构化结果字段）
- `build_trace_timeline()` 过滤 `_TIMELINE_HIDDEN_STAGES`，并对旧会话做 tool-stage 去重
- `render_trace_text()` 与 UI 共用同一 flat timeline，导出顺序与展示一致，每步仍含完整 `raw`/`args`/`sql`/`llm_calls`

### 回归测试

- `tests/test_trace_model.py::test_timeline_hides_workflow_synthetic_stages_when_tools_exist`

---

## GUI-002 · WebEngine 透明背景在 macOS 上叠层 ghost

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 高 |
| **平台** | macOS |

### 现象

打开 trace drawer 后，回答区 Markdown（`QWebEngineView`）文字叠在 trace 面板上。

### 根因

透明 WebEngine 使用原生 compositor 层（`WA_AlwaysStackOnTop`），绘制在 Qt overlay 之上。

### 修复

Markdown / chart WebEngine 改用与 `Theme.BG` / `Theme.PANEL` 一致的不透明背景（`markdown_webview.py`、`chart_block.py`）。

---

## GUI-003 · Trace drawer live 更新时 `raise_()` 引发布局闪动

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 中 |
| **平台** | 全平台 |

### 现象

Live step 更新时 drawer 或内容区域闪一下。

### 根因

每次内容同步都 `raise_()` drawer；resize 无防抖。

### 修复

`TraceOverlayController` 仅在首次打开时 `raise_panel=True`；resize 48ms 防抖；内容更新 batch `setUpdatesEnabled(False)`。

---

## GUI-004 · Drawer 关闭时仍弹出 `TraceDetailPanel`

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 中 |

### 现象

Drawer 未打开时点击 step，中央或右侧弹出独立 detail 滑层。

### 修复

`show_trace_detail()` 仅在 drawer 已打开时写入 bottom detail tray；不再 fallback 到 `TraceDetailPanel`。

---

## GUI-005 · 对话内 ECharts 缩放与滚动冲突

| 字段 | 内容 |
|------|------|
| **状态** | 已修复 |
| **严重度** | 中 |
| **平台** | 全平台 |

### 现象

回答含图表时，在对话区域滚轮或拖拽易被 ECharts `dataZoom` 滑块/内置缩放捕获，无法正常滚动消息列表。

### 根因

WebEngine 内嵌图表默认启用交互式 `dataZoom`；事件在图表层消费，外层 `QScrollArea` 收不到滚轮。

### 修复

- 对话与导出 HTML 默认 `chartInteractive: false`（无滑块/滚轮缩放，保留 tooltip）。
- **更多 → 图表交互…** 在独立对话框中以 `chartInteractive: true` 打开同一回答；`setHtml(html, base_url)` 加载本地 vendor。

### 回归测试

`tests/test_chart_block.py` — `dataZoom` 仅在 `chartInteractive: true` 时出现。

---

## 模板（复制后填写）

```markdown
## GUI-XXX · 标题

| 字段 | 内容 |
|------|------|
| **状态** | 待修复 / 已修复 / 已知限制 |
| **严重度** | 高 / 中 / 低 |
| **平台** | … |

### 现象
…

### 根因
…

### 修复
…

### 回归测试
…
```
