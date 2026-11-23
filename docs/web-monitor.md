# 工作区 Web 监视器

在实验工作区中运行 `scienceflow web`，或在 TUI 中输入 `/web`。启动器会打印实际
URL 和端口，并复用同一工作区中已经运行的监视器。如果默认端口被占用，会自动选择
一个空闲端口。

```bash
scienceflow web --workspace /path/to/workspace
scienceflow web --workspace /path/to/workspace --port 0 --open
scienceflow web stop --workspace /path/to/workspace
```

任务总览支持分页。点击任务进入任务页，可查看与 TUI 一致的统计信息、worker 页面、
指标曲线和阶段谱系。父子关系与恢复关系来自 stage 日志中显式的 candidate ID；
未知关系不会根据行顺序推断。跨页引用仍会显示在谱系表中。曲线展示 stage 历史；
Best 卡片使用当前 TUI 投影。

## Agent 轨迹

每个任务页都有一个 **Agent trajectory** 页签。它读取研究进程写入的持久化运行时
审计，按 worker、lineage 和 stage 展示 agent 的 LLM call。层级关系为：

```
worker → lineage → stage/session → LLM call → tool call/result
```

左侧栏用于选择 worker 和 lineage。账本按时间顺序列出该 lineage 的 call。普通
agent call 和 ephemeral call 位于同一个列表中；ephemeral call 会额外带上
`ESTRA`、`COMPACT`、`ARCHIVE`、`STAGE COMMIT` 等阶段标签。存在 stage capture 时，
stage 分组头会显示对应的指标。顶部概览条会把 call 投影到 sequence 或 wall-clock
时间轴上，并标出 stage 边界。

### 打开视图

启动工作区监视器，打开打印出的 capability URL，选择任务，然后打开
**Agent trajectory** 页签：

```bash
scienceflow web --workspace /path/to/task-workspace --port 0
# 从源码检出运行时，让子进程使用同一份代码：
PYTHONPATH=/path/to/ScienceFlow python -m scienceflow.cli web \
  --workspace /path/to/task-workspace --port 0
```

监视器服务会把 Web 服务器作为子进程启动。源码检出目录必须出现在
`PYTHONPATH` 中（或以 editable 方式安装），否则子进程会导入已安装的旧代码。
停止服务：

```bash
scienceflow web stop --workspace /path/to/task-workspace
```

选中一个 call 后会打开检查器，包含以下页签：

- **Overview（概览）** — call 元数据、标注（annotations）、工具摘要和总结。
- **Context（上下文）** — 实际发送给模型的 `system_messages` 和
  `provider_messages`；相对上一次 call 新增的消息会标记为 `NEW`。
- **Response（响应）** — assistant 内容、推理内容、tool calls、结束原因和用量。
- **Tools（工具）** — 工具参数、渲染结果、原始工具输出链接和错误信息。
- **Usage（用量）** — token、cache、耗时、上下文增量和每次尝试。
- **Raw（原始数据）** — 完整 JSON，可复制或下载。

视图读取 `agent_provider_calls.jsonl` 构建快速的 worker/lineage 索引，读取
`lhr_events.jsonl` 获取 stage、ESTRA、压缩和 stage commit 标注，读取
`agent_runtime_operations/*.jsonl` 获取完整 request/response payload。体积很大的
runtime event ledger 只通过 memory-map 扫描，用于恢复重试错误和 turn 级决策；
它不会加载到浏览器，也不会整体解析成 Python 对象。

轨迹页在索引加载完成后不会自动轮询。使用左侧栏或工具栏中的 **Reload** 重新构建
索引并重新扫描当前 lineage。

注意事项与限制：

- stage commit 有两种模式。`stage_commit_text_mode` 启用时（默认），相关轮次位于
  主 session 内，标记为 `AGENT · STAGE COMMIT`；关闭时会出现独立的 ephemeral
  session，标记为 `EPHEMERAL · STAGE COMMIT`。
- provider audit 不记录工具 schema 目录，因此检查器只展示工具名、参数和结果，
  不展示工具定义。
- 原始工具输出文件采用尽力而为的链接。多个 session 共享同一个 stage 时，
  stage 前缀的原始文件可能来自另一个 session；operation 记录中的
  `rendered`/`message` 内容才是权威来源。
- 如果存在 `resource/agent_runtime_audit/` 下的资源控制审计根，会作为额外 actor
  发现。merge agent 使用独立布局，当前版本尚未纳入这棵树。

### 实施计划与状态

轨迹查看器分三个阶段实现。当前代码包含 Phase 1–3；剩余想法列为后续工作。

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| 1 | 审计根发现、worker/lineage 索引、按需 call 索引、完整 call 详情 API、账本、检查器 | 已完成 |
| 2 | ephemeral 阶段标签、stage 分组头和指标、上下文 diff、重试/错误检查、概览时间轴、搜索/过滤/折叠 | 已完成 |
| 3 | JSON 安全、路径白名单、缓存上限、文档、Python 与 DOM 测试、手动 Reload | 已完成 |
| 4 | 后续可用性与分析功能 | 未开始 |

### 已完成内容

- **数据层** — `scienceflow/interfaces/ui/trajectory/` 负责发现 worker 审计根、
  读取 `agent_provider_calls.jsonl`、提取 `lhr_events.jsonl` 事实、只为重试和决策
  行 memory-map 大型 runtime event ledger，并扫描 operation 文件以获得 byte range
  和摘要。完整 request/response payload 只在打开某个 call 时读取。
- **API** — 在现有 capability URL 下提供 index、call-list、call-detail、raw-call
  下载和白名单 raw-tool 端点。序列化前会归一化非有限浮点数；读取文件前会校验
  raw ID 和 stage 名称。
- **层级与标签** — worker 页签、lineage 侧栏、按时间排序的 call 账本、带 capture
  指标的 stage 分组头、ephemeral 阶段徽标、lineage 迁移摘要，以及重试/失败徽标。
- **检查器** — Overview、Context、Response、Tools、Usage 和 Raw 页签。Context 会
  标出相对上一次 call 新增的消息；Usage 会列出每次 attempt 和 provider 错误；
  Raw 支持复制和 JSON 下载。
- **概览条** — sequence 和 wall-clock 两种投影、stage 边界、阶段颜色和点击选中
  导航。
- **交互状态** — 搜索、stage 过滤、ephemeral 显隐、tool 行显隐、stage 折叠、
  有界内存缓存，以及重载后的选中和滚动保持。
- **质量保障** — Python 单元/HTTP 测试、jsdom 导航/检查器测试、Prettier 检查和
  文档。

### 设计与实现说明

deepseek-harness 的轨迹页是一个 React 包，绑定它自己的 session/conversation 读取
模型。ScienceFlow 监视器是一个无依赖、无构建的内联页面。因此本视图移植了它的
信息架构和交互模型——工具栏、账本、概览条、检查器页签、折叠和搜索——而不是直接
导入 React 组件。以 wheel 安装后运行不需要 Node/Vite 构建。

读取路径是有意分层的：

1. `agent_provider_calls.jsonl` 构建 worker/lineage/session 索引。
2. `lhr_events.jsonl` 标注 stage、epoch 迁移和特殊阶段。
3. 每个 lineage 的 operation 文件扫描一次，记录 byte range 和摘要。
4. 完整 request/response payload 只在选中 call 时按 offset 读取。

runtime event ledger 通过 memory-map 扫描，以恢复重试错误和 turn 决策；它不会被
整体解析成 Python 对象，也不会发送到浏览器。

### 尚未完成 / 后续工作

- **工具 schema 目录** — 审计中没有记录模型可见的工具定义。检查器只展示工具名、
  参数和结果。
- **全文 payload 搜索** — 搜索覆盖 call 摘要、stage、phase、session ID、工具名/
  预览和 annotations，不会扫描每一条 provider 消息或完整工具 payload。
- **按选择深链** — URL 只保存 task 和 `trajectory` 模式，不包含选中的 worker、
  lineage 或 call。
- **实时自动刷新** — 页面不会轮询。运行中的任务需要点击 **Reload**；这样可以避免
  检查过程中当前选择被替换。
- **时间轴框选、缩放和平移** — 概览条支持模式切换和点击选中，不支持拖动过滤或
  滚轮缩放。
- **虚拟化账本** — 当前 lineage 会整体渲染（参考 run 最多约 325 个 call），并使用
  `content-visibility`；几千个 call 的 lineage 需要窗口化。
- **Merge 和其他控制 agent** — 资源控制审计根会被发现，但 merge agent 和其他独立
  布局尚未合并进这棵树。
- **Review 流程** — 还不支持对单个 call 写备注、打标记或导出 review 结论。
- **成本投影** — 展示 token、cache 和耗时，但不展示单个 call 的成本。
- **跨任务轨迹总览** — 查看器以任务为粒度，没有跨任务页面。
- **旧数据回退** — 如果缺少 `agent_provider_calls.jsonl`，即使 operation 文件存在，
  该 worker 也会被跳过。

### 已知限制

**数据与审计：**

- provider audit 不记录工具 schema 定义或流式 chunk。检查器展示的是最终确定的
  request 和 response，而不是逐 token 生成过程。
- 原始工具输出采用尽力而为的方式。stage 前缀文件可能属于共享同一 stage 的另一个
  session；operation 中的 `message`/`rendered` 内容才是权威来源。
- stage 指标来自 `stage_captured` 事件，不会重新从 evaluator 计算。同一 stage
  后续的 capture 可能改变展示值；lineage 的 “best” 使用最佳有限 capture。
- worker 索引由 provider call 驱动。只有 operation 文件、没有
  `agent_provider_calls.jsonl` 的 worker 当前会被跳过。
- 资源控制审计根在存在时会被发现；merge agent 和其他布局不属于这棵树。
- 不展示单 call 成本，只展示 token、cache 和耗时。

**查看器与规模：**

- 页面不会自动轮询。手动 Reload 会保留当前选择，但任务仍在运行时页面可能过期。
- URL 不包含 worker、lineage 或 call 选择。
- 搜索是摘要/分面搜索，不是对每条 provider 消息或工具 payload 的全文搜索。
- 概览条没有拖动框选、滚轮缩放或平移。
- 当前 lineage 会整体渲染；几千个 call 的 lineage 需要虚拟化。
- 没有 review 备注、标记、导出或跨任务轨迹页。

**运行与访问：**

- 查看器是只读的：不会写入 session event、projection 或研究状态。
- 访问依赖现有 capability URL。除该 token 外没有额外认证，请勿公开该 URL。

### 验证

在仓库根目录运行：

```bash
python -m pytest tests/test_web_monitor.py tests/test_trajectory_viewer.py \
  tests/test_monitor_trace_html.py tests/test_inquirycraft_cli_composition.py \
  tests/test_monitor_ui_parallel.py -q
npm --prefix tests/web_monitor test
npm --prefix tests/web_monitor run format:check
```

参考 run 的实测观察（两个 worker，12 小时任务）：

- Worker/lineage 索引：约 0.4 s。
- 首次 lineage 扫描：约 1.5–2 s；之后走缓存。
- 首次 call 详情：约 1 s（需要先构建 runtime event ledger 索引）；后续 call 为
  毫秒级。
- 索引总量：2 个 worker、20 个 lineage、2975 个逻辑 call、2988 次 attempt、
  35 个 ephemeral call。
- 重试 call 会展示两次 attempt 和 provider 错误信息；text-mode stage commit 轮次
  标记为 `AGENT · STAGE COMMIT`，不会给后续粘滞 route 的 turn 误加标签。

## 远程访问

默认监听地址是 `127.0.0.1`。在 SSH 会话中，复制启动器打印的端口转发命令，在本地
计算机上运行，把 `user@server` 替换为你的 SSH 登录信息，然后在本地打开打印出的
URL。使用 VS Code Remote 的用户可以在 Ports 面板中转发打印出的端口。`--open`
在 SSH 会话中会跳过自动打开浏览器。显式指定 `--host 0.0.0.0` 可监听所有 IPv4
接口。

URL 中包含随机访问 token。服务器只提供监视器页面和缓存 JSON，不提供任意工作区
文件。请勿公开该访问 URL。

## 刷新与生命周期

Web 在独立进程中运行，并降低调度优先级。日志扫描在后台线程中异步执行；扫描期间
HTTP 请求会拿到上一份缓存快照。Web 不会发起 LLM 请求，也不会修改研究状态。扫描
会消耗少量 CPU 和磁盘 I/O。

总览刷新默认每五秒一次；当扫描耗时增加时会自动放慢。只有浏览器请求的任务页才会
加载曲线/谱系详情；这些详情至少缓存 15 秒，未变化的源文件不会重复读取。后台浏览器
页签会暂停轮询。关闭 TUI 不会停止 Web；`/web stop` 或 `scienceflow web stop`
只会停止 Web。页面会显示扫描耗时和最近更新时间。

现有的 `scienceflow monitor-trace --manifest ... --output ...` 导出器仍可用于基于
manifest 的离线报告。

## 前端结构与开发检查

`web_monitor/page.py` 把打包的 HTML、CSS 和 JavaScript 组装成一个内联页面。以
wheel 安装后运行不需要 CDN 或浏览器构建步骤。资产按职责拆分：

- `core.js`：DOM 辅助函数和共享显示格式化。
- `metric.js`：耗时指标曲线。
- `lineage_layout.js`：与源数据对齐的图谱坐标，不访问 DOM。
- `lineage_viewport.js`：worker 视口状态、滚轮缩放和指针拖动。
- `lineage.js`：worker 选择和谱系渲染。
- `trajectory_model.js`：轨迹状态、格式化、安全 Markdown 和 JSON 树。
- `trajectory_nav.js`：worker 和 lineage 导航。
- `trajectory_timeline.js`：sequence/time 两种模式的概览。
- `trajectory_ledger.js`：call 账本、stage 分组头和 tool 行。
- `trajectory_inspector.js`：检查器的上下文、响应、工具、用量和原始数据页签。
- `trajectory_view.js`：轨迹外壳、按需 call 加载和 Reload 行为。
- `view.js`：任务区块以及滚动/展开记录的保持。
- `refresh.js`：单请求链、自适应轮询和交互保护。

Python `data.py` 负责读取/缓存日志；`projection.py` 负责转换记录的时间戳和祖先
关系。`trajectory/` 负责读取 agent runtime audit、构建 worker/lineage 索引，并按需
读取完整 operation payload。缺失或非有限时间戳保持为未知。`TaskIndex.snapshot()`
是只读 API，不会同步或创建研究记录。

使用 Node 24 运行浏览器 DOM 回归测试（仅开发时需要）：

```bash
npm --prefix tests/web_monitor ci
npm --prefix tests/web_monitor test
npm --prefix tests/web_monitor run format:check
python -m pytest tests/test_web_monitor.py tests/test_trajectory_viewer.py tests/test_monitor_trace_html.py
```

DOM 测试覆盖 worker 导航、源数据对齐、滚轮锚定、拖动生命周期、滚动保持、时间戳
轴、合并重复刷新请求、轨迹导航和轨迹检查器。它不能替代真实浏览器中的视觉检查。
修改打包资产后，需要重新构建 wheel 并重启 Web 服务。

## Worker 资源利用率

Worker 行与单行主机资源摘要共享一个缓存采样器。每个监视器进程的进程/CPU 快照
最多每五秒刷新一次，GPU 查询最多每十秒刷新一次。CPU 指标反映工作目录属于该
worker 的进程及其子孙进程，并按观测到的 CPU 亲和性（affinity）归一化；共享的
coordinator 开销无法归因到单个 worker。首次采样和不可访问的进程显示 `—`。

GPU ID 是物理设备索引，来自活跃租约（lease）或 worker 进程映射。标记为 `dev` 的
利用率和 VRAM 是设备级读数，不是对 worker 独占使用的估算。`shared` 表示该设备上
还观测到其他 GPU 进程；无法归因进程并不意味着独占。设备较多时会用 ID 范围均值和
范围摘要。在展开的 TUI 任务卡片中点击 worker 行可查看完整列表，或使用 `/status N`。
