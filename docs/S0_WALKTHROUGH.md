# S0 调用链与设计讲解

这一阶段完成的是“两个进程可靠说话”，还没有模型和工具。先把入口、消息和资源所有者讲清楚，后续才能把 Agent 运行放入 Core。

## 用一次 ping 阅读代码

```text
终端 A：codepilot core → Core 装配并监听
终端 B：codepilot ping → 请求 id → 一行 JSON → TCP
                                              ↓
Core：完整帧 → 严格校验 → core.ping → 同 id 响应
                                              ↓
客户端：校验信封和 id → 校验 pong 内容 → 输出 → 关闭连接
```

按这个顺序读关键函数：

| 函数与文件 | 输入、输出及它承担的边界 |
|---|---|
| main：[cli.py](../src/codepilot/cli.py) | 解析 core/ping/version；读取配置；将成功、通信失败、配置错误映射为退出码 0/1/2 |
| load_settings：[config.py](../src/codepilot/core/config.py) | 合并参数与项目环境变量，拒绝非法端口和超时；此时还没有连接或监听 |
| ping：[cli.py](../src/codepilot/cli.py) | 发出 core.ping，再用 PingResult 检查消息确实为 pong，最后输出 |
| request：[client.py](../src/codepilot/transport/client.py) | 生成本次请求的 UUID，连接、发送、等待并校验同 id 响应；总请求等待有超时 |
| read_frame / write_frame：[framing.py](../src/codepilot/transport/framing.py) | 处理 UTF-8 JSON 的换行与长度。TCP 是字节流，一次读取不保证恰好是一条请求 |
| _handle_connection / _process：[server.py](../src/codepilot/transport/server.py) | 每个连接独立处理，连接内逐条读取；区分解析、信封与业务错误 |
| dispatch：[app.py](../src/codepilot/core/app.py) | 只认识 core.ping，要求空参数，返回 pong 和安装包版本；不负责网络读写 |
| close：[server.py](../src/codepilot/transport/server.py) | 停止接受连接、关闭客户端、取消并等待任务、等待监听器关闭，防止端口或任务残留 |

请求 id 只回答“这条响应属于哪条请求”。它还不是 session_id 或 run_id；后续有多轮对话和 Agent 执行时再引入那些标识，不提前把概念混在一起。

## 三个需要理解的状态

1. Core 生命周期：未监听 → 正在监听 → 正在关闭 → 已关闭。端口绑定失败时直接报错，不假装就绪。
2. 一个连接的状态：读取完整帧 → 校验与处理 → 写回 → 读取下一帧；断开或超时只关闭这个连接。
3. 客户端请求状态：尚未连接 → 等响应 → 已验证或失败 → 关闭。S0 只有一次请求，不需要维护多请求 Future 表。

Core 拥有监听器、连接集合与连接任务集合；客户端只拥有自己的连接。资源由创建和使用它的一侧负责关闭，不能把清理寄托于进程退出时操作系统顺手回收。

## 为什么这样取舍

- **Core 与客户端分离**：未来 CLI 和 TUI 可共用后台状态；代价是协议、错误和关闭逻辑，因此先单独验证它们。
- **NDJSON**：一行一条消息便于观察和复现；字符串中的换行必须转义，并限制单帧 64 KiB，避免无限缓存残片。
- **连接内串行**：S0 只有很短的 ping，无需现在引入调度和响应队列；S2 有事件流与长期任务时再扩展多路复用。
- **严格模型**：数字 id 不自动转成字符串，成功和错误不能同时存在，避免客户端把结构不完整的消息误认成成功。
- **版本只维护一处**：本地 version 和远端 server_version 都读取安装元数据，避免两个进程报出不同版本。

## 遇到故障时沿哪条路径找

| 现象 | 首先检查 | 程序如何处理 |
|---|---|---|
| 端口为 0 或超时为 NaN | 配置错误输出 | 网络动作前返回退出码 2 |
| Core 未启动或端口写错 | CLI 报错中的地址 | 连接失败返回 1，不偷偷拉起后台 |
| 不合法 JSON | 协议错误码 -32700 | 返回 id=null 的错误；完整帧边界已知，可继续处理后续消息 |
| 参数不对 | -32602 与请求 id | 保留原 id，拒绝本次请求，不拖垮服务 |
| 半条消息一直不补完 | 连接超时日志 | 超时关闭这一连接，其他客户端继续服务 |
| 服务内部异常 | 服务日志中的请求 id | 远端只收到简短 -32603，不把内部堆栈发给客户端 |
| Ctrl+C 后端口仍占用 | 清理日志、实际进程与端口 | 应进入 finally，清理连接与任务；不能只看“进程退出”就认定正确 |

本轮真实遇到的 Windows 连接重置和进程控制限制见 [S0_VALIDATION.md](S0_VALIDATION.md)，包含失败尝试和最终验证方法。

## Ctrl+C 为什么能进入清理

Core 通过 asyncio.run 执行主协程；收到正常 SIGINT 时，asyncio 的主任务取消机制让 finally 有机会执行，再由命令入口处理 KeyboardInterrupt。Core 空闲时每 0.2 秒让出并唤醒事件循环，使 Windows 下空闲的事件循环能及时处理控制事件。这里没有使用 Windows 不支持的 Unix 信号注册。

这条设计结合了 [Python 3.12 Runner 的中断机制](https://docs.python.org/3.12/library/asyncio-runner.html#handling-keyboard-interruption)，并由实际运行验证；详细的 stream 读写与关闭语义可查 [asyncio streams](https://docs.python.org/3.12/library/asyncio-stream.html)。验证脚本使用 [Windows GenerateConsoleCtrlEvent](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent) 向独立控制台发送真实事件，不是调用 Core 的关闭方法代替信号。控制台是否忽略 Ctrl+C 也受 [SetConsoleCtrlHandler 的继承属性](https://learn.microsoft.com/en-us/windows/console/setconsolectrlhandler)影响，不能把写入一个控制字符等同于送达事件。

## 可以自己演练的三个问题

1. Core 离线时，为什么 version 可以成功，ping 却返回 1？沿两个命令分支读一遍。
2. 一个请求分两次发送，为什么第一次没有响应？在 read_frame 找到“完整帧”的判断依据。
3. 关闭 Core 时只关闭监听器，现有客户端和连接任务会怎样？对照 close 的资源顺序解释。

演练不涉及模型密钥。阶段结束后仍由用户决定是否进入 S1。
