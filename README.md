# CodePilot Crew

从文档和最小骨架逐阶段开发的本地终端编程 Agent，作为个人学习与展示仓库。仓库名为 `CodePilot_Crew`，Python 包与命令保留 `codepilot`。

工作目录：`G:/实习项目/CodePilot_Crew`。旧 `G:/实习项目/CodePilot` 仅作只读参考；新项目使用独立目录与独立 Git 历史。

## 当前状态

2026-11-03：当前版本 0.5.0，S0—S4 已完成本机 Windows 验收，195 项离线检查通过。最终独立安装的固定响应与真实 deepseek-flash 分别完成跨进程恢复：删除原文件后仍能依据保存的观察继续回答，恢复轮零工具调用。保留 F10/退出按钮调整。用户已推送 S1，本轮未 push 或查询远端 CI；Linux 尚未实测。

本项目的四条能力目标是 Agent 循环与工具调用、多 Agent 编排、上下文压缩与流式重试、MCP 与 Skills。目前已接入循环、读取、受控写入与命令执行，其他能力随阶段推进。

## 本机安装与演示（Windows PowerShell）

需要 uv 和 Python 3.12。`.python-version` 指定 3.12，uv 可使用已有解释器或下载所需版本。本轮使用独立 CPython 3.12.14，不更改系统默认 Python。

在本目录安装：

```powershell
Set-Location G:\实习项目\CodePilot_Crew
uv --cache-dir .cache/uv sync --locked
uv --cache-dir .cache/uv run --locked codepilot version
```

预期输出 `codepilot 0.5.0`。本机默认 uv 缓存初始化失败，所以示例明确使用项目内缓存；缓存和虚拟环境不提交。

第一个终端启动 Core：

```powershell
uv --cache-dir .cache/uv run --locked codepilot core
```

第二个终端进入同一目录后探活：

```powershell
Set-Location G:\实习项目\CodePilot_Crew
uv --cache-dir .cache/uv run --locked codepilot ping
```

预期输出 `pong`。在第一个终端按 Ctrl+C 停止，然后重新执行 core 和 ping，检验端口释放。S0 的 Core 在前台运行，ping 不自动拉起服务；没有系统服务或自动后台启动。

默认地址为 `127.0.0.1:7438`。端口占用时同时为两个命令指定另一个端口，例如 `core --port 17438` 与 `ping --port 17438`；程序不关闭占用端口的其他进程。

## 运行只读 Agent

先停止旧版或未配置模型的 Core。在工作区内准备本地配置，已有 .env 时保留它：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

填写 `CODEPILOT_CREW_BASE_URL`、`CODEPILOT_CREW_MODEL`、`CODEPILOT_CREW_API_KEY`，然后启动：

```powershell
uv --cache-dir .cache/uv run --locked codepilot core --workspace . --env-file .env
```

另一个位于同目录的终端执行：

```powershell
uv --cache-dir .cache/uv run --locked codepilot run "读取 README.md，概括这个项目当前能做什么。"
```

加 `--json` 可查看 run_id、停止原因、步数和工具调用回执；`--max-steps` 默认 8。调用链为 [Runner](src/codepilot/core/runner.py) → [AgentLoop](src/codepilot/agent/loop.py) → [Provider](src/codepilot/agent/provider.py) / [ToolRegistry](src/codepilot/tools/registry.py)。模型提出调用，程序先校验再执行，结果按 tool_use_id 回填。

配置仅由 Core 显式读取，不通过 TCP 传递密钥。进程环境覆盖配置文件；模板默认模型请求等待 30 秒、max_tokens=1024，整个 run 最多等待 90 秒，客户端 run 默认等待 120 秒。Base URL 支持服务根地址、/v1 或完整 /v1/messages；接口格式必须兼容 Anthropic Messages。

read_file 和 list_dir 默认允许。路径解析后检查工作范围，拒绝越界、范围外链接、.git/.venv/.cache/.env 和 Windows 备用数据流。读取 UTF-8 文件最多 128 KiB，目录最多 100 项，工具文本最多 8 KiB且截断有标记。这些是应用层路径边界。

命令行 run 保留完整回答模式，TUI 使用真实 SSE 增量。两条路径都在完整响应与整批调用校验后才执行工具；工具错误同样回传 is_error。max_steps、max_tokens、超时或无效响应都会失败。thinking/signature 只作 opaque 数据回传，不展示或解释正文。命令行 run 没有审批交互，默认策略下请求写入/命令会收到 approval_unavailable。

## 终端工作台

第一个终端继续运行带 --env-file 的 Core，在第二个终端进入本目录执行：

```powershell
uv --cache-dir .cache/uv run --locked codepilot tui
```

输入目标并按 Enter 或点击“开始”。界面显示当前步骤、回答草稿、工具参数与结果；流式文字持续更新同一块回答。完整步骤文本替换草稿，最终结果再次使用 Core 的完整回答；失败或取消时明确标注草稿未采纳。

write_file 和 bash 默认每次询问。核对审批区完整内容、解析后的路径、实际脚本和超时，再点“允许本次”或“拒绝本次”；长内容可以滚动查看。审批等待最多 60 秒，拒绝、超时、取消或断连均不执行等待中的动作；其他连接不能代为批准。授权不持久保存。

Ctrl+C 或“取消”发送取消请求，收到 run.finished 才显示已取消；F10 或“退出”按钮退出界面。退出会关闭连接并取消该连接拥有的运行，Core 自身继续监听，等待审批的动作不会获准执行。TUI 当前一次运行一个目标，连续提交沿用当前会话；未提供自动重连或执行中任务的原位续跑。

VS Code 集成终端可能先处理 Ctrl+Q 等快捷键，TUI 无法接收这些按键；应用内部的优先级绑定不能覆盖编辑器快捷键。Ctrl+Q 仍作为兼容绑定保留，使用时优先按 F10 或点击“退出”。若 F10 也被自定义快捷键占用，可点击按钮或用 Tab 聚焦“退出”后按 Enter。无需修改 VS Code 设置，相关机制见 [VS Code 终端快捷键文档](https://code.visualstudio.com/docs/terminal/advanced#_keyboard-shortcuts-and-the-shell)。

Core 默认将脱敏记录写入 `artifacts/runs/<run_id>/`，也可用 --record-dir 指定目录：events.jsonl 是进度事件，trace.jsonl 是模型请求、响应、API 用量、耗时、错误和取消。它们不是会话原始记录；省略 thinking 正文/签名，超长记录明确截断，不记录请求头或配置密钥。API 未返回用量时保留为空，不估造 token 数。

## 连续对话与重启恢复

TUI 启动时新建会话，页首显示完整会话编号；继续提交目标会使用已保存的消息。可点“新会话”开始独立上下文，或刷新列表、选择旧会话并点击“恢复”。恢复显示上次结果，继续输入新目标才调用模型，不自动执行旧工具。同一会话同时只允许一个 run。

重启带同一 workspace/session-dir 的 Core 后，可以直接指定会话编号：

```powershell
uv --cache-dir .cache/uv run --locked codepilot sessions
uv --cache-dir .cache/uv run --locked codepilot tui --session <会话编号>
```

CLI 也可使用 `session-new --title 名称` 创建、`resume <会话编号>` 查看上次状态，再用 `run "继续目标" --session <会话编号>` 追问。CLI 未指定 session 时仍是一次性任务，没有审批通道。查看/恢复本身不调用模型。

默认私有数据位于工作区 `.codepilot/sessions.sqlite3`，Git 和普通文件工具均排除 `.codepilot/`。服务端可用 --session-dir 指定其他目录；若在工作区内，必须位于 `.codepilot/` 下。同一数据目录只由一个 Core 打开。数据库以 session 保存快照/笔记，以 run_id+seq 保存独立原始增量、任务引用、状态与用量；消息采用和对应增量在同一事务中提交，不依赖可压缩 messages 的旧偏移。

原始记录保存领域层请求、完整模型响应和工具意图/结果，不是 HTTP/SSE 字节日志。为兼容模型续谈，私有库保留 opaque thinking/signature，不在 UI/Trace 展示；库可能含源码和对话，未加密，配置密钥会替换为 REDACTED。单条原始记录最多 2 MiB，工作快照最多 8 MiB，超限明确失败；S4 尚未压缩历史。用量按 API 实际返回汇总，未返回时为空。

工作区可选 `CODEPILOT.md` 作为项目背景（UTF-8，最多 16 KiB），每次会话运行读取；错误/越界背景会明确阻止模型请求。模型可显式调用 note_save 保存当前会话笔记：默认 ask，审批展示 key 和完整内容；同 key 替换，最多 20 条、每条 4000 字符。笔记只在此会话后续请求中使用，背景和笔记均不扩大工具权限。

取消/失败保留已配对的工作消息及独立原始记录；半批工具不伪造成完成历史。进程意外退出后，上次 running 标为 interrupted，并提示重新检查实际文件状态。已发生的修改不回滚、不重放。校验失败的会话显示 corrupt 并拒绝恢复，其他会话可用；数据库物理损坏明确报错，保留原文件，不能通过删除数据库“修复”。这保证保存上下文后的继续交互，不是任意工具事务续跑。

## 写入、命令与权限

write_file 只接受小型 UTF-8 内容，最多 12 KiB；父目录须存在，覆盖已有文件须明确 overwrite=true。预检不写文件；批准后重新核对路径和原内容 hash，再通过同目录临时文件原子替换。目标被其他人改动时返回 write_conflict；已完成的修改不因后续取消自动回滚。

bash 是工具协议名：Windows 实际使用 PowerShell，POSIX 使用 bash，审批展示实际 shell 和脚本。cwd 必须在工作区内；命令环境采用白名单，不继承模型 API 配置。默认超时 30 秒，最大 120 秒，整个 run 仍受 90 秒预算约束。stdout/stderr 持续排空、各保留前 4 KiB，截断有标记。Windows 使用独立 supervisor 和 Job 管理普通子孙进程，超时、取消、正常结束和服务关闭都会清理；无法确认清理时整次运行失败并停止后续动作。

权限由 Core 的可选 `--policy-file PATH` 指定，内容为严格 JSON；省略的工具保留默认值，未知字段或非法值导致启动失败。下面的策略允许读取、询问写入、禁止命令：

```json
{"read_file":"allow","list_dir":"allow","write_file":"ask","bash":"deny"}
```

支持 allow/deny/ask；allow 表示服务器明确授权，无需逐次询问。模型和客户端不能修改策略。有副作用动作不自动重试。应用层审批和 cwd 检查不提供操作系统隔离：获准脚本仍以当前用户权限运行，可能访问 cwd 以外的资源。不保证恶意进程逃逸或强制杀死 Core 后的完整资源回收；会话恢复也不恢复命令进程。

## 接口与配置

| 命令 | 行为 |
|---|---|
| `codepilot core [--port N] [--policy-file PATH] [--session-dir PATH]` | 启动 Core，日志写 stderr，Ctrl+C 清理 |
| `codepilot ping [--port N] [--timeout SECONDS]` | 一次真实 TCP 请求，校验响应编号和 pong 结构 |
| `codepilot version` | 显示本地包版本，Core 离线可用 |
| `codepilot run GOAL [--session ID] [--max-steps N] [--timeout SECONDS] [--json]` | 完整运行任务，无审批通道；循环正常结束退出 0，失败退出 1 |
| `codepilot tui [--session ID] [--port N] [--timeout SECONDS]` | 连接 Core，新建或恢复会话，支持审批与取消 |
| `codepilot sessions / session-new --title TITLE / resume ID` | 列表、新建或查看恢复状态，均支持 --port/--timeout |

环境变量为 `CODEPILOT_CREW_PORT`、`CODEPILOT_CREW_TIMEOUT`、`CODEPILOT_CREW_LOG_LEVEL`，优先级为命令参数 > 环境变量 > 默认值。请求默认等待 5 秒，连接收尾另有最多 1 秒预算。成功退出码为 0，运行/通信失败为 1，配置/参数错误为 2。

TCP 使用 UTF-8 NDJSON，单帧最多 64 KiB。持久连接通过 id→Future 关联并发响应，kind=event 单独分流。支持 core.ping、agent.run/start/cancel、approval.respond、session.create/list/resume；不支持 JSON-RPC 批量或通知。超大响应 -32002，未配置模型 -32010，操作其他连接的运行/审批 -32011，审批过期 -32013；会话未配置 -32020、不存在 -32021、忙碌 -32022、损坏/持久化失败 -32023。session.list 最多返回最近 50 条，不包含原始消息或 opaque 正文。请求超时关闭连接并清理全部等待者，整体运行另有 90 秒预算。

每连接最多 32 个未完成请求、写队列最多 512 帧，服务最多 8 个事件运行；队列超限明确断连并取消，不悄悄丢最终事件。只读 I/O 的取消是停止等待，不能声称强制中断了所有底层文件系统操作。没有在 S2 加入流式重试。

## 验证与学习

```powershell
uv --cache-dir .cache/uv run --locked --offline pytest -q
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_windows_ctrl_c.py
```

第二条使用独立隐藏控制台发送真实 Windows CTRL_C_EVENT，检查连接结束、退出码、清理时间及同端口重启；属于自动化控制台验证，不是人工键盘演示。执行环境需要允许向自己创建的测试进程发送控制事件。它不调用模型。

S1 的两类可复现验证分别运行：

```powershell
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_s1.py --provider fixed --via-rpc
uv --cache-dir .cache/uv run --locked python scripts/verify_s1.py --provider real --env-file .env --via-rpc
```

固定响应会实际读文件，但最终文本由脚本预先给定，只证明程序链路。真实验证让模型读取提示中未提供的随机标记，并核对工具成功、观察进入下一请求及最终答案。后者实际调用配置的模型端点。结果写入被忽略的 artifacts/；thinking 正文和签名从演示轨迹中省略，不能当作完整可重放原始 API 记录。

S1 首次真实验证因 thinking 块兼容而失败，修正后通过；保留失败与成功证据。S2 的挂载界面验证分别运行：

```powershell
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_s2.py --provider fixed
uv --cache-dir .cache/uv run --locked python scripts/verify_s2.py --provider real --env-file .env
```

两条命令都会实际挂载 Textual，读取临时随机标记文件并核对最终 widget 文本，同时生成脱敏 JSONL、结果摘要和 SVG 截图。固定流的最终回答预先给定；真实模式实际调用配置的模型服务。测试另外覆盖取消、断连、乱序响应、流截断不执行工具及不同 run/step 隔离。不据少量样例声称成功率、提速或节费。Linux 与远端 CI 本轮未验证。

S3 的修改与检查案例：

```powershell
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_s3.py --provider fixed
uv --cache-dir .cache/uv run --locked python scripts/verify_s3.py --provider real --env-file .env
```

脚本创建临时 calc.py/check.py，独立检查先失败；实际挂载 TUI，读取源码、批准修改并执行检查，最后独立复查。自动化仅点击符合示例路径、单一修复函数和唯一检查命令的本次授权，其他动作拒绝；不是人工审批演示。固定模式的动作与最终文本预先给定，真实模式由配置模型定位错误并提出动作。记录核对批准早于执行、工具成功、真实检查输出进入下一模型请求与最终回答；status=success 本身不证明任意任务完成。

S4 的跨进程恢复案例：

```powershell
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_s4.py --provider fixed
uv --cache-dir .cache/uv run --locked python scripts/verify_s4.py --provider real --env-file .env
```

启动独立 Core 子进程读取临时文件的随机标记；退出并删除文件，再启动新的 Core，列出、恢复并追问；核对新请求保留前次真实工具观察、恢复轮没有调用工具、最终回答标记一致。固定模式另有直接结束 Core 的崩溃案例，检查 interrupted 与已完成写入未重放。最终文本预设和真实模型结果分开；导出证据省略 opaque 正文，不能替代私有完整会话库。

## 从哪里开始

| 文件 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | 协作、实现与验证约定 |
| [PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) | 能力目标、技术边界与参考来源 |
| [ROADMAP.md](docs/ROADMAP.md) | S0—S9 的阶段顺序与验收要求 |
| [PROGRESS.md](docs/PROGRESS.md) | 已发生事实、实际验证与下一步 |
| [S0_DESIGN.md](docs/S0_DESIGN.md) | 首阶段的目录、职责、接口、验收案例和提交节点 |
| [S0_VALIDATION.md](docs/S0_VALIDATION.md) | 实际命令、结果、故障与未验证项 |
| [S0_WALKTHROUGH.md](docs/S0_WALKTHROUGH.md) | 以 ping 解释关键函数、状态、错误路径与取舍 |

每阶段按“设计、实现、验证、讲解、更新进度、提交”推进。本轮只做用户指定的 S4，S5 由用户另行指定。阶段分析文档按当前 docs/ 忽略规则留本地；已被 Git 跟踪的交接文件仍随提交更新。

## 参考与开发方式

设计参考 [KamaClaude](https://github.com/youngyangyang04/KamaClaude)，阶段方法借鉴 [AemeathCode](https://github.com/Nijikasuki/AemeathCode)，使用 AI 辅助编码。固定参考版本与本轮查阅范围见 S0 设计中的来源记录。直接复用代码时另行记录来源并保留适用说明。

本地历史从启动文档开始，随后是安装入口、Core 通信与逐阶段验收；stage-0 至 stage-4 用于定位已验收阶段。不沿用旧项目测试成绩，不预设效果提升数字。用户已推送 S1，后续本地提交没有自动 push。
