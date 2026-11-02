# S0 实际验收记录

日期：2026-11-02。范围：独立新仓库 CodePilot_Crew 的 S0，本机 Windows 原生运行。

结论：本机安装、真实通信、故障处理和控制台退出/重启已验证；S0 本机验收完成。Linux、远端 CI、人工键盘演示未执行。模型能力不属于本阶段验证范围。

## 环境与实际命令

- CPython 3.12.14，uv 0.12.15，Git 2.54.0.windows.1。
- 开发环境为本项目 `.venv`；另创建 `.cache/install-check`，用于独立 wheel 安装。未复用旧 CodePilot 的环境、源码、Git 或记录。
- uv 使用项目内 `.cache/uv`。下方除构建和全新安装外，测试过程不依赖外部服务。

```powershell
uv --cache-dir .cache/uv sync --python C:/Users/PianoHurt/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe
uv --cache-dir .cache/uv run --locked --offline codepilot version
uv --cache-dir .cache/uv run --locked --offline pytest -q
uv --cache-dir .cache/uv build --wheel --offline --out-dir artifacts/dist
uv --cache-dir .cache/uv venv .cache/install-check --python C:/Users/PianoHurt/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe
uv --cache-dir .cache/uv export --locked --no-dev --format requirements-txt --no-emit-project --no-hashes --output-file artifacts/s0-constraints.txt
uv --cache-dir .cache/uv pip install --python .cache/install-check/Scripts/python.exe --constraint artifacts/s0-constraints.txt artifacts/dist/codepilot-0.1.0-py3-none-any.whl
```

实际过程中的版本/测试结果：

| 节点 | 命令结果 |
|---|---|
| C1 | version 输出 codepilot 0.1.0；配置相关 9 passed |
| C2 | 真实 TCP 等行为检查 32 passed |
| C3 首轮 | 50 passed、1 failed：关闭残帧连接时 Windows 返回 ConnectionResetError，原测试只接受 EOF |
| C3 修正后 | 51 passed in 8.69s；允许 EOF 或明确的 ConnectionResetError，仍要求同端口重启探活成功 |
| 构建 | codepilot-0.1.0-py3-none-any.whl 成功；16 个归档条目，只有 codepilot 包与 dist-info，无缓存/密钥/字节码 |
| 全新环境安装 | 安装 codepilot 与 5 项运行依赖成功；导入来自 install-check/Lib/site-packages/codepilot，不是 src 或开发环境 |
| 收尾检查 | uv sync --locked --offline 检查 13 个包成功；8 份 Markdown/25 个本地链接有效；wheel 中全部 Python 模块与当前源码逐字节一致；git diff --check 通过 |

## A1—A13 对照

| 案例 | 实际证据 | 状态 |
|---|---|---|
| A1 安装 | 项目环境锁定安装成功，另验证 wheel 在全新环境中的独立导入、version、探活与退出 | 本机通过 |
| A2 本地版本 | 安装后的 console entry 在无 Core 情况输出 codepilot 0.1.0，退出 0 | 通过 |
| A3 探活 | 真实 TCP 返回 pong、同请求 id、server_version=0.1.0；CLI 输出 pong，退出 0 | 通过 |
| A4 拆包/合包 | 残片不提前响应；补全换行并追加下一请求后，两次 id 正确 | 通过 |
| A5 坏 JSON | 同一连接先返回 -32700/id=null，下一条合法 ping 成功 | 通过 |
| A6 信封/方法/参数错误 | -32600/-32601/-32602 及 id 规则正确；同连接仍能 ping | 通过 |
| A7 断开/残片 | 部分请求断开后没有分发；完整请求继续成功 | 通过 |
| A8 超时/超长隔离 | 慢连接不阻塞正常连接；残片超时关闭；超长帧 -32001 并断开 | 通过 |
| A9 客户端故障 | 离线、无响应、断开、错误 id、无效信封、半帧及远端失败均有界失败；CLI 不输出 pong，退出 1 | 通过 |
| A10 启动失败 | 第二个 Core 因端口占用失败，原服务仍可用；非法端口和超时退出 2 | 通过 |
| A11 资源关闭 | 保留残片客户端，关闭后连接结束；同端口新服务能 ping | 通过 |
| A12 Windows 控制台 | 真实 CTRL_C_EVENT → 清理日志、退出 0、端口释放；同端口重启与再次退出通过 | 自动控制台通过；人工键盘未执行 |
| A13 CI | 已配置 Windows/Linux + Python 3.12；本机 Windows 相同 pytest 检查通过 | Windows 本机通过；Linux/远端未执行 |

附加行为包括严格类型、不接纳数字 id 或额外顶层字段、CRLF、非法 UTF-8、NaN/Infinity、响应成功/错误互斥、内部异常脱敏、版本离线可用。

## Windows 退出的独立证据

`scripts/verify_windows_ctrl_c.py` 创建独立隐藏控制台中的真实 Core 进程，先 TCP ping，再保留一个残片连接，由辅助进程发送 Windows CTRL_C_EVENT。它没有直接调用 SocketServer.close，也没有给业务函数打桩。

开发环境一次结果：端口 50073，两次退出码均为 0，清理耗时分别 0.359 秒、0.375 秒，同端口重启成功。

从独立 wheel 环境的工作目录运行同一脚本，结果保存在本机已忽略的 `artifacts/windows-install-check.json`：

| 轮次 | 端口 | 结果 | 清理耗时 |
|---|---|---|---|
| 首次启动 | 50647 | pong → 真实 Ctrl+C 事件 → 清理日志 → 退出 0 | 0.360 秒 |
| 同端口重启 | 50647 | pong → 真实 Ctrl+C 事件 → 清理日志 → 退出 0 | 0.360 秒 |

两次保留的客户端连接均已结束。以上是指定环境下的两次观测，不是性能提升或广泛可靠性指标；人工按键演示未做。

## 实际遇到的故障与处理

1. uv 默认缓存初始化失败，改用项目缓存。受限网络首次下载失败，获准访问包源后安装成功。全新环境最初的离线依赖解析缺少包，随后按锁文件导出的约束完成安装；不将安装失败的尝试计为通过。
2. Git 在执行环境中提示目录所有者不同；所有 Git 操作按命令指定本项目 safe.directory，没有修改全局配置或旧仓库历史。
3. Windows 残片连接关闭可能报告重置而非 EOF；测试接受明确的连接结束信号，并继续验证端口重启，未以宽泛忽略异常代替验收。
4. 终端通道发送 Ctrl+C 字符、以及受限进程控制环境中的事件尝试没有令测试进程退出；这些尝试未算通过，遗留测试进程已按核对后的 PID 和项目路径清理。改为同权限独立控制台真实 CTRL_C_EVENT 后，开发和独立安装环境均通过。强制清理本身不算正常关闭证据。

## 验证类型与保留限制

- 本轮的 51 项测试使用真实本地网络，部分使用受控 TCP 故障端；不是固定响应模型测试。
- 固定响应 Provider：未实现、未运行。真实模型：未接入、未调用。没有模型任务成功率或 token/成本结果。
- 没有 Agent Loop、工具执行、TUI、会话、MCP、Skills 或多 Agent 实现。
- S0 每个连接按顺序处理，只暴露探活；loopback 不代表身份认证，不以此保证未来工具权限。
- GitHub 远端尚未创建，CI 未上传运行；Linux 兼容性尚无实测结论。
- 后续阶段由用户指定，不自动进入 S1。
