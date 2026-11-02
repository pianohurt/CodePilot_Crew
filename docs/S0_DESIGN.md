# S0 启动设计：工程骨架与通信

日期：2026-11-02。状态：设计已整理，等待用户指定实施；本文中的结构、命令输出和案例均为计划，未实现、未验收。

## 1. 已确定的目标、命名与边界

| 项目 | 决定 |
|---|---|
| 仓库名 / 展示名 | CodePilot_Crew / CodePilot Crew |
| 独立工作目录 | G:/实习项目/CodePilot_Crew（已存在） |
| Python 包 / 命令 | codepilot / codepilot，按用户选择保留 |
| 本地发行包名 | codepilot；仅用于当前项目安装，不代表名称可在 PyPI 发布 |
| Git | 从本目录建立新历史，不导入旧 .git；当前未初始化、无提交或标签 |
| 远端 | 尚未创建，地址待定；后续创建时先私有，不在本轮发布 |

能力目标以 PROJECT_BRIEF 的四条主线为准：Agent 循环与工具调用；多 Agent 编排；上下文压缩与流式重试；MCP 与 Skills。S0 为这些能力提供启动和通信基础，本阶段完成不代表 Agent 已能执行任务。

技术方向保留 Python 3.12、asyncio、Pydantic、Anthropic 兼容接口、Textual、pytest、uv；S0 只引入实际需要的部分。运行依赖先用 Pydantic，命令解析、网络和日志优先用标准库；开发依赖为 pytest 与必要的异步测试支持。模型 SDK 在 S1、Textual 在 S2 再加入。

Windows 原生可运行是必验条件，Linux 尽量兼容。2026-11-02 的检查只确认默认 `python` 为 3.9.12、uv 为 0.12.15、Git 为 2.54.0.windows.1；尚未检查 uv 是否另有 Python 3.12。实施时选用 Python 3.12 并生成独立虚拟环境，不复用旧项目环境，不更改系统默认 Python。

本轮只更新设计与交接文档。S0 实施包括：可安装包、最小配置、日志入口、本地 TCP 通信、core/ping/version、错误处理、退出清理、离线测试及最小 CI。

S1 及以后再创建 agent、tools、tui、session、compact、subagents、mcp、skills 等模块。本轮不生成这些空目录，不实现 Provider、工具执行、事件总线、审批、持久化或多 Agent。S0 也不做自动拉起后台、系统服务安装、远程访问或自动重连。

## 2. S0 必需目录与模块职责

以下是 S0 实施后的最小结构；本轮实际新增的只有本文档，其他文档同步更新。

```text
CodePilot_Crew/
  AGENTS.md
  README.md
  pyproject.toml             # 包元数据、codepilot 入口、依赖与测试配置
  uv.lock                   # 实施时实际解析产生，不手写锁文件
  .python-version           # 3.12
  .gitignore                # 环境、缓存、密钥和运行产物不入库
  .github/workflows/ci.yml   # 最小离线检查，先覆盖 Windows 与 Linux
  src/codepilot/
    __init__.py
    cli.py                  # 参数、命令分发、输出与退出码
    core/
      __init__.py
      config.py             # 配置解析与校验，不启动服务
      app.py                # 装配、ping 处理、启动/停止和资源归属
      logging_setup.py      # 标准日志初始化，默认写 stderr
    protocol/
      __init__.py
      messages.py           # 请求、成功、错误的 Pydantic 信封与校验
    transport/
      __init__.py
      framing.py            # UTF-8、换行边界、长度与不完整帧
      client.py             # 连接、一次请求往返、超时、响应编号校验
      server.py             # 接收连接、逐帧处理、连接与任务清理
  tests/
    unit/                   # 配置、信封与分帧边界
    integration/            # 真实 TCP、CLI 子进程、断连与关闭
  docs/
    PROJECT_BRIEF.md
    ROADMAP.md
    PROGRESS.md
    S0_DESIGN.md             # 实施时在本文补充实际验证与演示记录
```

不为目录占位创建 `.gitkeep` 或预留抽象基类。测试文件按验收行为建立；具体类、函数、字段和文件拆分在 S0 实施时随需求落实，变更影响到上述职责或外部行为时说明原因。

依赖方向：

```text
ping 命令 → transport/client → protocol + framing → 本地 TCP
                                                       ↓
core 命令 → core/app → transport/server → 协议校验 → Core 的 ping 处理
version 命令 → 已安装包元数据（不需要 Core 在线）
```

CLI 可以读取配置；Core 装配配置、日志和传输。协议只描述数据，不建立连接或管理业务状态；传输不导入 CLI，也不内置未来的模型和工具逻辑。S0 的分发只需一个明确的 ping 入口，不预建通用插件系统或 ToolRegistry。

分离 Core 和客户端是为了让未来 CLI/TUI 共用服务和运行状态。代价是增加连接、协议错误和资源关闭问题，因此 S0 先把这些问题单独验收。

## 3. 最小接口约定

### 3.1 命令与配置

| 计划命令 | 行为与可观察结果 |
|---|---|
| `uv run codepilot core` | 在前台启动 Core；成功监听后记录地址；Ctrl+C 触发有界清理 |
| `uv run codepilot ping` | 连接已有 Core，调用 core.ping；stdout 输出 `pong`；不自动启动服务 |
| `uv run codepilot version` | 从已安装包元数据输出 `codepilot 0.1.0`；Core 离线也能成功 |

S0 初始版本拟为 0.1.0，以实施时 pyproject.toml 的包元数据为唯一版本来源；ping 的 server_version 读取同一来源，避免手工维护多份版本号。新项目代码通过本目录内 `uv run` 执行，避免命中机器上其他同名命令。

Core 和 ping 支持 `--port`；ping 支持 `--timeout`；Core 支持 `--log-level`。对应环境变量拟为 `CODEPILOT_CREW_PORT`、`CODEPILOT_CREW_TIMEOUT`、`CODEPILOT_CREW_LOG_LEVEL`。优先级为显式参数 > 对应环境变量 > 默认值；不读取旧 KAMA 配置或旧 .env。配置模型只负责把最终配置校验为合法值。

监听地址在 S0 固定为 `127.0.0.1`；默认端口 7438（旧参考协议使用 7437，7438 不保证空闲）。正常端口范围 1—65535；仅测试内部允许使用 0，让操作系统分配空闲端口。客户端一次调用的总等待预算默认 5 秒，包含连接、发送和接收，超时必须是有限正数；日志级别默认 INFO。无效配置在监听或连接之前失败。

正常命令返回 0；连接、超时、远端错误或异常关闭返回 1；命令参数/配置错误返回 2。Core 经 Ctrl+C 完成正常清理返回 0。普通结果走 stdout，诊断和服务日志走 stderr，不把堆栈直接作为协议响应。

### 3.2 传输与数据边界

- 使用本地 TCP + UTF-8 NDJSON：一条消息以 `\n` 结束，发送端使用紧凑 JSON，字符串中的换行必须转义。
- 接收端按换行取完整帧，不能把一次网络读取等同于一条消息。接受 CRLF；空白行作为解析错误处理。
- 单帧上限先定为 64 KiB（包含结尾换行）。超长帧返回可发送的错误后关闭该连接；不尝试无限缓存或继续恢复这一条连接。
- 未收到换行的残片不分发；断开时丢弃残片。等待完整帧和网络写入有超时，默认 5 秒；超时关闭受影响连接。
- 同一连接逐条处理请求；不同连接互不阻塞。客户端每次命令只发一条请求并等待一条响应，完成后关闭连接。S2 再做多路复用、Future 映射、事件流与断连等待者清理。

S0 使用 **JSON-RPC 风格的受限协议**：只支持带非空字符串 id 的请求；不支持批量、notification、反向请求和事件。使用 `jsonrpc: "2.0"` 及常见错误码便于阅读，不宣称完整实现 JSON-RPC 2.0。后续扩展在对应阶段明确约定。

请求示意（实际发送时行尾增加换行）：

```json
{"jsonrpc":"2.0","id":"req-1","method":"core.ping","params":{}}
```

成功响应：

```json
{"jsonrpc":"2.0","id":"req-1","result":{"message":"pong","server_version":"0.1.0"}}
```

错误响应示意（收到未知 method 时）：

```json
{"jsonrpc":"2.0","id":"req-1","error":{"code":-32601,"message":"Unknown method"}}
```

外部约束先固定为：

| 边界 | 规则 |
|---|---|
| 请求信封 | jsonrpc 必须为 2.0；id 是非空字符串；method 是非空字符串；params 必须是对象；严格校验类型，不将数字 id 转为字符串；不接受未知顶层字段 |
| ping 参数 | S0 必须是空对象；额外参数返回参数错误 |
| 响应 | 成功和错误互斥；可识别请求的响应原样回传 id；解析/信封失败统一用 id=null |
| 客户端配对 | 先校验响应结构再核对 id；不匹配、残缺或非法响应立即报协议错误，不当作 pong |
| 当前状态标识 | 只使用请求 id 和连接信息；session_id、run_id、step、tool_use_id 随所属阶段加入 |

错误语义：

| 情况 | code / 行为 |
|---|---|
| 完整帧不是合法 UTF-8 JSON | -32700；id=null；帧边界明确时允许继续接收下一帧 |
| JSON 合法但信封无效 | -32600；id=null |
| 未知 method | -32601；保留合法请求 id |
| ping 参数无效 | -32602；保留合法请求 id |
| 非预期处理异常 | -32603；只返回简短说明，详细异常记入服务日志 |
| 帧超长 | 项目错误码 -32001；id=null；尽力发送后关闭该连接 |
| EOF、连接重置、超时 | 结束该连接并清理；可能无法发送响应；其他客户端继续可用 |

本地连接不等于身份认证。S0 的接口只有探活，不提供模型、文件或命令能力；未来接入有副作用的接口时落实相应权限边界，不把 loopback 当作工具授权。

### 3.3 生命周期与日志

Core 状态为启动中 → 监听中 → 停止中 → 已停止；配置错误或端口占用则启动失败，不伪报就绪，不自动换端口，也不停止占用端口的进程。端口占用以实际绑定结果为准，避免“预先探测通过就认为一定能启动”。

Core 拥有监听 socket、客户端连接和连接处理任务。关闭顺序为：停止接受连接 → 关闭现有连接 → 取消并等待剩余处理任务 → 确认监听关闭。整体清理预算先定 5 秒，未正常清理要记为失败；不吞掉所有异常后声称退出成功。

使用跨平台的主入口取消与 finally 清理设计；不把 Unix 专用信号注册作为必要条件。Windows PowerShell 中真实按 Ctrl+C、端口释放与重启必须单独验证；自动测试调用关闭流程不能替代这项证据。S0 没有模型调用或工具子进程需要恢复。

日志至少能定位监听地址、连接建立/结束、请求 id 和 method、失败类别、关闭结果。先用标准 logging 写 stderr，不在 S0 引入事件存储、模型 Trace 或会话日志体系。

## 4. 验收案例与证据

以下均为待执行案例，不能引用旧 CodePilot 的测试成绩。实现时根据行为写测试，不以私有函数调用次数或“文件已存在”作为完成依据。

| 编号 | 触发方式 | 预期可观察结果 | 验证层次 |
|---|---|---|---|
| A1 | 在新环境选择 Python 3.12，执行 uv sync --locked | 安装成功，能调用 codepilot；不依赖旧项目目录/环境 | 独立安装 |
| A2 | Core 未启动时执行 version | 输出实际包版本，退出码 0 | CLI 子进程 |
| A3 | 启动 Core，执行 ping | 输出 pong、退出码 0；线上 id 与 server_version 正确 | 真实 TCP + CLI |
| A4 | 将一个 ping 分多次发送；再将两条请求合并发送 | 残片不提前响应；完整帧分别返回相同请求 id 对应的响应 | 真实 TCP |
| A5 | 发送无效 JSON，再在同连接发送合法 ping | 先收到 -32700 和 id=null，再收到正常 pong | 真实 TCP |
| A6 | 分别发送无效信封、未知方法、错误 ping 参数 | 返回 -32600/-32601/-32602；服务仍可用 | 协议 + TCP |
| A7 | 连接后立刻断开，或发半帧再断开 | 不分发残片；另一个客户端仍能 ping | 真实 TCP |
| A8 | 一条连接保持残帧，另一条执行 ping；再发送超长帧 | 正常连接不被阻塞；残帧超时关闭；超长连接结束，服务仍可用 | 真实 TCP |
| A9 | Core 离线、连接中断、模拟服务不回应或返回错误 id | 客户端有界失败并关闭连接，退出码 1；错误 id 不被接纳 | CLI + 受控 TCP 对端 |
| A10 | 同一端口启动第二个 Core；传入非法端口/超时 | 端口占用返回 1；配置错误返回 2；原服务仍可 ping | CLI 子进程 |
| A11 | 保留一个客户端连接，正常关闭 Core，再在同端口启动 | 连接与任务结束；端口可重新绑定；新 Core 能 ping | 生命周期集成 |
| A12 | Windows PowerShell 中启动 Core、ping、按 Ctrl+C、同端口重启并 ping | 5 秒内正常退出，退出码 0；重启成功且没有旧服务残留 | Windows 人工演示 |
| A13 | 从独立环境运行最小 CI 流程 | Python 3.12 的 Windows/Linux 离线行为测试通过 | 本地复现 + 后续远端 CI |

拟执行的核心命令为 `uv sync --locked`、`uv run --locked pytest -q`，以及上表的 core/ping/version 手动演示。锁文件先在实施中生成，再使用 --locked 校验可复现性。CI 的依赖安装可访问包源；“离线测试”指测试过程不依赖模型或外部服务。没有远端 CI 实际结果时，不以工作流文件存在宣称通过。

证据分三类保存于本文后续记录及 PROGRESS：

1. **S0 真实本地通信**：实际安装、socket、子进程和 Windows 退出结果。
2. **固定响应 Provider 测试**：从 S1 开始记录，本阶段没有 Provider；受控 TCP 对端只是在测通信故障。
3. **真实模型验证**：从 S1 开始，需要有效配置与密钥。本轮未调用模型；S0 的 pong 不是模型能力证明。

## 5. 第一阶段的提交节点

第一阶段指 S0。以下是建议节点，不是已经发生的提交；本轮未初始化 Git、commit、tag 或 push。具体提交仍遵循当次授权，不为了凑数量拆分空改动。

| 节点 | 建议提交信息 | 提交时应已有的内容与证据 |
|---|---|---|
| C0：启动设计基线 | docs: define project scope and S0 design | 当前目标、路线、协作、进度、README 与 S0 设计；从独立 git init 起步，不带旧历史 |
| C1：安装与入口 | build: bootstrap codepilot package | pyproject、锁文件、Python 版本、忽略规则；新环境可安装，version 可运行；此时不标记 S0 完成 |
| C2：最小通信 | feat: add core lifecycle and ping RPC | Core、传输、协议、配置和日志；对应成功路径、分帧和错误路径检查已有结果 |
| C3：验收与交接 | test: verify S0 transport and shutdown | 补齐故障/生命周期测试、最小 CI、经过验证的 README、实际演示与限制；讲解后更新 PROGRESS，再提交 |

每个实现节点内部仍按“设计、实现、验证、讲解、更新进度、提交”推进；末尾完整核对 S0 验收。依实际改动可合并相邻节点，不能在没有相应结果时预写通过记录。

获得相应授权且 S0 验收完成后，将 `stage-0` 指向通过验收的提交。批量 push 多条本地提交可以在后续单独进行；上传不是本地功能通过的证据。阶段结束后等待用户指定 S1，不自动开始。

## 6. 学习讲解的最小调用链

以 `codepilot ping` 为例：命令入口读取端口配置 → 客户端生成请求 id → 序列化并发送一帧 → 服务端等待完整换行 → 校验信封和 ping 参数 → Core 返回 pong 和包版本 → 响应带回原 id → 客户端校验、输出、关闭连接。

状态包括 Core 生命周期、当前连接、未完成帧和本次请求 id。错误分别在配置、网络、分帧、协议、方法参数及处理层产生；连接结束时所有路径都要走清理。Ctrl+C 处理的是整个服务的资源收尾，客户端断开只影响该连接。

实现后的讲解围绕这一个请求和实际新增函数展开，说明为什么选择按行分帧、为什么此时逐条处理、如何从错误日志定位失败，以及 S2 为什么再引入多路复用。

## 7. 来源记录与本轮核对

- KamaClaude：固定提交 [70decaf06b284b8e72b90f8369ec75bbfcf4dc81](https://github.com/youngyangyang04/KamaClaude/tree/70decaf06b284b8e72b90f8369ec75bbfcf4dc81)。本轮查阅该版本 [README](https://github.com/youngyangyang04/KamaClaude/blob/70decaf06b284b8e72b90f8369ec75bbfcf4dc81/README.md) 和 [socket_server.py 的启动、关闭及读取部分](https://github.com/youngyangyang04/KamaClaude/blob/70decaf06b284b8e72b90f8369ec75bbfcf4dc81/src/kama_claude/core/transport/socket_server.py)。参考常驻 Core、loopback TCP 和分帧设计；不直接沿用包含后续阶段职责的完整服务端。
- AemeathCode：固定提交 [cd98b83e46dba24abfd103473d4c711396c181f3](https://github.com/Nijikasuki/AemeathCode/tree/cd98b83e46dba24abfd103473d4c711396c181f3)。本轮查阅该版本 [README 的阶段表、双进程结构和 Windows 限制](https://github.com/Nijikasuki/AemeathCode/blob/cd98b83e46dba24abfd103473d4c711396c181f3/README.md)。借鉴逐阶段解决具体工程问题的方法；本项目按自己的 ROADMAP 推进，并将 Windows 原生支持列为要求。
- 旧 CodePilot：只读核对 README、WIRE_PROTOCOL 和文件结构；实际本地 HEAD 为 `75a4fdf7a3d5ff770dfc9c136b3b1881c912ba35`。本轮未运行旧测试、未复制源码/环境/密钥/记录。旧仓库成绩不进入新仓库验证记录。

本轮没有直接复用代码。上面的默认值与协议裁剪是新项目的 S0 设计选择，不宣称来自参考项目或已通过实测。
