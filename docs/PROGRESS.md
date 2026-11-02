# 当前进度与会话交接

更新时间：2026-11-02。

## 当前状态

- 项目：CodePilot Crew；仓库名：CodePilot_Crew；工作目录：G:/实习项目/CodePilot_Crew。
- 用户已确认 Python 包与命令均保留 codepilot。
- 启动设计已整理于 [S0_DESIGN.md](S0_DESIGN.md)，包含目标边界、S0 最小结构、模块职责、外部接口、故障验收与提交节点。
- S0：实施中，C1 安装入口与 C2 真实 TCP 通信已验证；CLI 故障子进程、独立安装和 Windows 实际 Ctrl+C 完整验收待补齐；S1—S9：未开始。
- 已有最小包、独立环境、依赖锁、core/ping/version、配置模型、分帧和资源清理；尚未提供 Agent 能力。
- 本地 main 分支已初始化；根提交 1198160 保存启动设计，无远端或上传。
- 旧 G:/实习项目/CodePilot 保留为只读参考，新项目不导入旧 Git 历史。

## 用户已确认的方向

- 以 PROJECT_BRIEF 四条能力主线为准，参考 KamaClaude 的设计和 AemeathCode 的阶段方法。
- AI 辅助编码；Python 有基础、后端经验较少，逐阶段理解关键调用链、取舍与故障处理。
- 先确定顶层结构与边界，类和函数随阶段展开；不预建后续阶段实现。
- 后续按“设计、实现、验证、讲解、更新进度、提交”推进，阶段结束后由用户指定下一阶段。
- 固定响应 Provider、真实工具/通信结果与真实模型验证分别记录。

## 本轮实际核对与结果

| 核对项 | 实际操作与结果 |
|---|---|
| 项目材料 | 读取 AGENTS、PROJECT_BRIEF、ROADMAP、PROGRESS、README；工作区最初只有这五份文档 |
| 独立目录 | 文件清单确认 G:/实习项目/CodePilot_Crew 存在；git status/log 返回“not a git repository”，与未初始化状态一致 |
| 命名 | 用户选择仓库 CodePilot_Crew，Python 包/命令保留 codepilot；已同步过时的待定描述 |
| 参考资料 | 通过 GitHub 只读接口读取两个固定提交下的资料；查阅范围见 S0_DESIGN 的来源记录 |
| 旧仓库基线 | 只读核对 HEAD=75a4fdf7a3d5ff770dfc9c136b3b1881c912ba35；git status --porcelain=v1 --untracked-files=all 无输出，工作树基线干净 |
| 本机工具 | python --version：3.9.12；uv --version：0.12.15；git --version：2.54.0.windows.1 |
| 源码与测试 | 未生成源码、未安装依赖、未执行 pytest；本轮为设计任务 |
| 模型验证 | 固定响应 Provider 与真实模型调用均未运行；S0 后续的 pong 属于通信验证 |

本轮文档检查与旧仓库只读复核的最终结果记录于下方，不能将其当作 S0 运行验收。

## 关键设计与待实施选择

- S0 只建设 CLI、Core、protocol、transport；仅支持 core/ping/本地 version。
- 使用 loopback TCP + NDJSON + JSON-RPC 风格受限信封；请求 id 配对，单连接逐条处理。
- 默认地址 127.0.0.1:7438；客户端、帧等待与写入有界；Windows Ctrl+C、连接清理和端口重启是验收重点。
- 版本来自安装包元数据。模块不反向依赖 TUI；事件流和多路复用在 S2 实现。
- 提交节点建议为启动文档、可安装入口、Core 通信、完整验收与交接；当前均未提交。

## 已知限制与下一步

- 当前默认 Python 为 3.9.12，低于目标 3.12；尚未检查其他已安装解释器。S0 实施时选择 3.12，不更改系统默认版本。
- 协议和默认值为设计，需通过真实 TCP、安装与 Windows 退出验证；尚无可运行能力或通过数字。
- 远端地址尚未确定；不复制旧密钥、运行日志或虚拟环境。S0 不需要模型密钥。
- 下一项任务：由用户指定开始 S0 实施；随后依 S0_DESIGN 逐个节点实现、验证、讲解并更新本文件，按当次授权提交。不自动进入 S1。

## 启动设计时的文档检查

- 使用临时只读检查核对当前 6 份 Markdown：8 个本地链接均可解析，代码围栏配对，3 个 JSON 协议示例均能解析，未发现编码替换字符。
- 使用 rg 核对过时的名称/目录待定与阶段状态描述，并人工对照 PROJECT_BRIEF、ROADMAP 与 S0_DESIGN；已同步本轮确定的命名和设计状态。
- 文件清单仍仅包含 AGENTS、README 和 docs 下四份文档；没有提前生成源码骨架、测试目录或 Git 元数据。
- 修改后复核旧 CodePilot：git status --porcelain=v1 --untracked-files=all 无输出，HEAD 与基线相同。
- 上述结果仅为文档与边界核对；S0 的 A1—A13 均未执行，功能未验收。

## S0 实施记录：C1

- 选用已有独立 CPython 3.12.14；未更改默认 Python 3.9.12，未复用旧项目环境。
- uv 默认缓存目录初始化失败，改用项目内 `.cache/uv`（已忽略）。首次受限网络下载失败，随后在获准访问包源后 `uv sync --python <已有3.12解释器>` 成功，产生 uv.lock 和 .venv。
- `uv run --locked --offline codepilot version`：codepilot 0.1.0，退出码 0。
- `uv run --locked --offline pytest -q`：9 passed，覆盖配置优先级、非法端口、非有限/非正超时及旧配置隔离。
- 调用链已讲解：CLI → 安装元数据；配置合并优先级为参数 > 项目环境变量 > 默认值，校验发生在网络操作之前。
- 当前测试只验证 C1，不代表 S0 全体验收通过。下一步接入真实 TCP Core/ping。

## S0 实施记录：C2

- 实现严格请求/响应模型、64 KiB NDJSON 边界、单连接串行处理、独立连接并发、请求编号配对与错误分类。
- Core 拥有监听器、连接与任务；关闭时停止接受连接、关闭客户端、取消并等待任务，最后等待监听器关闭。
- `uv run --locked --offline pytest -q`：32 passed。包含真实 TCP ping、拆包/合包、CRLF、坏请求后继续服务、半帧断开、超时/超长客户端隔离、内部错误脱敏、端口占用和同端口重启。
- 已讲解 ping 调用链：CLI → 客户端请求编号 → 完整帧 → 严格协议校验 → Core → 同编号响应 → 客户端验证。S0 保持连接内串行，S2 再引入事件与多路复用。
- 上述结果不代表实际 Windows Ctrl+C 已验收；该项与 CLI 子进程、独立安装留到 C3 完成。
