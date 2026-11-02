# CodePilot Crew

从文档和最小骨架逐阶段开发的本地终端编程 Agent，作为个人学习与展示仓库。仓库名为 `CodePilot_Crew`，Python 包与命令保留 `codepilot`。

工作目录：`G:/实习项目/CodePilot_Crew`。旧 `G:/实习项目/CodePilot` 仅作只读参考；新项目使用独立目录与独立 Git 历史。

## 当前状态

2026-11-02：S0 已实现并完成本机 Windows 验收：51 项离线测试通过，独立 wheel 安装、真实 TCP 探活、Windows 控制台 Ctrl+C 事件及同端口重启通过。本地已有独立 Git 历史；尚无远端。Linux 与远端 CI 未运行。

本项目的四条能力目标是 Agent 循环与工具调用、多 Agent 编排、上下文压缩与流式重试、MCP 与 Skills。目标不代表当前已具备这些功能。

## 本机安装与演示（Windows PowerShell）

需要 uv 和 Python 3.12。`.python-version` 指定 3.12，uv 可使用已有解释器或下载所需版本。本轮使用独立 CPython 3.12.14，不更改系统默认 Python。

在本目录安装：

```powershell
Set-Location G:\实习项目\CodePilot_Crew
uv --cache-dir .cache/uv sync --locked
uv --cache-dir .cache/uv run --locked codepilot version
```

预期输出 `codepilot 0.1.0`。本机默认 uv 缓存初始化失败，所以示例明确使用项目内缓存；缓存和虚拟环境不提交。

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

## 接口与配置

| 命令 | 行为 |
|---|---|
| `codepilot core [--port N] [--log-level LEVEL]` | 启动 Core，日志写 stderr，Ctrl+C 清理 |
| `codepilot ping [--port N] [--timeout SECONDS]` | 一次真实 TCP 请求，校验响应编号和 pong 结构 |
| `codepilot version` | 显示本地包版本，Core 离线可用 |

环境变量为 `CODEPILOT_CREW_PORT`、`CODEPILOT_CREW_TIMEOUT`、`CODEPILOT_CREW_LOG_LEVEL`，优先级为命令参数 > 环境变量 > 默认值。请求默认等待 5 秒，连接收尾另有最多 1 秒预算。成功退出码为 0，运行/通信失败为 1，配置/参数错误为 2。

TCP 上使用 UTF-8 NDJSON 与严格的 JSON-RPC 风格受限信封。每帧最多 64 KiB；S0 只支持 `core.ping`，不支持批量、通知或事件流。协议细节见 S0 设计。

## 验证与学习

```powershell
uv --cache-dir .cache/uv run --locked --offline pytest -q
uv --cache-dir .cache/uv run --locked --offline python scripts/verify_windows_ctrl_c.py
```

第二条使用独立隐藏控制台发送真实 Windows CTRL_C_EVENT，检查连接结束、退出码、清理时间及同端口重启；属于自动化控制台验证，不是人工键盘演示。执行环境需要允许向自己创建的测试进程发送控制事件。它不调用模型。

离线测试含受控 TCP 故障端，不是固定响应 Provider 测试。S0 没有 Provider 或真实模型调用；两类模型验证从 S1 再单独记录。CI 已配置 Windows/Linux + Python 3.12，未上传执行，不能视为远端已通过。

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

每阶段按“设计、实现、验证、讲解、更新进度、提交”推进。S0 停在本地通信能力，S1 由用户另行指定。

## 参考与开发方式

设计参考 [KamaClaude](https://github.com/youngyangyang04/KamaClaude)，阶段方法借鉴 [AemeathCode](https://github.com/Nijikasuki/AemeathCode)，使用 AI 辅助编码。固定参考版本与本轮查阅范围见 S0 设计中的来源记录。直接复用代码时另行记录来源并保留适用说明。

本地历史从启动文档开始，随后是安装入口、Core 通信、验收与交接；`stage-0` 用于定位 S0 的本机验收提交。不沿用旧项目测试成绩，不预设效果提升数字。上传与远端创建按用户后续指令执行。
