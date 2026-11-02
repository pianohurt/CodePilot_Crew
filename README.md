# CodePilot Crew

从文档和最小骨架逐阶段开发的本地终端编程 Agent，作为个人学习与展示仓库。仓库名为 `CodePilot_Crew`，Python 包与命令保留 `codepilot`。

工作目录：`G:/实习项目/CodePilot_Crew`。旧 `G:/实习项目/CodePilot` 仅作只读参考；新项目使用独立目录与独立 Git 历史。

## 当前状态

2026-11-02：当前版本 0.2.0，S0 和 S1 已实现。105 项离线测试通过；固定响应读取链路与真实 deepseek-flash 读取随机文件标记分别验证，经 CLI/TCP/Core 的真实案例用 2 次模型请求完成。Windows Ctrl+C 和同端口重启回归通过。远端由用户关联，本轮未 push 或查询远端 CI；Linux 尚未实测。

本项目的四条能力目标是 Agent 循环与工具调用、多 Agent 编排、上下文压缩与流式重试、MCP 与 Skills。目前完成第一条中的只读循环与工具校验，其他能力随阶段推进。

## 本机安装与演示（Windows PowerShell）

需要 uv 和 Python 3.12。`.python-version` 指定 3.12，uv 可使用已有解释器或下载所需版本。本轮使用独立 CPython 3.12.14，不更改系统默认 Python。

在本目录安装：

```powershell
Set-Location G:\实习项目\CodePilot_Crew
uv --cache-dir .cache/uv sync --locked
uv --cache-dir .cache/uv run --locked codepilot version
```

预期输出 `codepilot 0.2.0`。本机默认 uv 缓存初始化失败，所以示例明确使用项目内缓存；缓存和虚拟环境不提交。

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

先停止之前未配置模型的 Core。在工作区内准备本地配置，已有 .env 时保留它：

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

本阶段工具只有 read_file 和 list_dir。路径解析后检查工作范围，拒绝越界、范围外链接、.git/.venv/.cache/.env 和 Windows 备用数据流。读取 UTF-8 文件最多 128 KiB，目录最多 100 项，工具文本最多 8 KiB且截断有标记。这些是应用层读取边界，不代表完整文件系统隔离。

使用完整响应模式，整批调用结构、停止原因和 id 校验后才执行；工具错误同样回传 is_error 结果。max_steps、max_tokens、超时或无效响应都会失败。支持将 thinking/signature 当作 opaque 数据回传兼容端点，不展示或解释其正文。没有写入、命令执行、流式界面或会话记忆。

## 接口与配置

| 命令 | 行为 |
|---|---|
| `codepilot core [--port N] [--log-level LEVEL]` | 启动 Core，日志写 stderr，Ctrl+C 清理 |
| `codepilot ping [--port N] [--timeout SECONDS]` | 一次真实 TCP 请求，校验响应编号和 pong 结构 |
| `codepilot version` | 显示本地包版本，Core 离线可用 |
| `codepilot run GOAL [--max-steps N] [--timeout SECONDS] [--json]` | 完整运行一次只读任务，成功退出 0，失败退出 1 |

环境变量为 `CODEPILOT_CREW_PORT`、`CODEPILOT_CREW_TIMEOUT`、`CODEPILOT_CREW_LOG_LEVEL`，优先级为命令参数 > 环境变量 > 默认值。请求默认等待 5 秒，连接收尾另有最多 1 秒预算。成功退出码为 0，运行/通信失败为 1，配置/参数错误为 2。

TCP 上使用 UTF-8 NDJSON 与严格的 JSON-RPC 风格受限信封。每帧最多 64 KiB，支持 `core.ping` 和 `agent.run`，不支持批量、通知或事件流。超大响应返回 -32002；未配置模型返回 -32010。CLI 的文本和 JSON 使用 UTF-8。客户端超时只停止等待，Core 另有运行预算；显式取消在 S2 推进。

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

本轮首次真实验证因兼容端点的 thinking 块而失败，未执行工具；补透传和回归后，Runner 层及 CLI/RPC 层分别通过。保留失败与成功证据，不据少量样例声称任务成功率。CI 已配置 Windows/Linux + Python 3.12，本轮只验证本机 Windows，不声称远端或 Linux 已通过。

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

每阶段按“设计、实现、验证、讲解、更新进度、提交”推进。S1 停在只读 Agent 能力，S2 由用户另行指定。阶段分析文档按当前 docs/ 忽略规则留本地；已被 Git 跟踪的交接文件仍随提交更新。

## 参考与开发方式

设计参考 [KamaClaude](https://github.com/youngyangyang04/KamaClaude)，阶段方法借鉴 [AemeathCode](https://github.com/Nijikasuki/AemeathCode)，使用 AI 辅助编码。固定参考版本与本轮查阅范围见 S0 设计中的来源记录。直接复用代码时另行记录来源并保留适用说明。

本地历史从启动文档开始，随后是安装入口、Core 通信与逐阶段验收；stage-0、stage-1 分别定位两个阶段。不沿用旧项目测试成绩，不预设效果提升数字。远端已由用户关联，本轮新增提交未 push。
