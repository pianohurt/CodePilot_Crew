# 当前进度与会话交接

更新时间：2026-11-02。

## 当前状态

- 项目：CodePilot Crew；仓库名：CodePilot_Crew；工作目录：G:/实习项目/CodePilot_Crew。
- 用户已确认 Python 包与命令均保留 codepilot。
- 启动设计已整理于 [S0_DESIGN.md](S0_DESIGN.md)，包含目标边界、S0 最小结构、模块职责、外部接口、故障验收与提交节点。
- S0：实现、验证与讲解已完成，本机 Windows 验收通过；Linux 与远端 CI 未运行，人工键盘演示未执行；S1—S9：未开始。
- 已有最小包、独立环境、依赖锁、core/ping/version、配置模型、分帧和资源清理；尚未提供 Agent 能力。
- 本地 main 分支使用独立 Git 历史；C0=1198160、C1=aab1775、C2=028c640；stage-0 定位 S0 验收/交接提交。本文件随该提交保存；无远端或上传。
- 旧 G:/实习项目/CodePilot 保留为只读参考，新项目不导入旧 Git 历史。

## 用户已确认的方向

- 以 PROJECT_BRIEF 四条能力主线为准，参考 KamaClaude 的设计和 AemeathCode 的阶段方法。
- AI 辅助编码；Python 有基础、后端经验较少，逐阶段理解关键调用链、取舍与故障处理。
- 先确定顶层结构与边界，类和函数随阶段展开；不预建后续阶段实现。
- 后续按“设计、实现、验证、讲解、更新进度、提交”推进，阶段结束后由用户指定下一阶段。
- 固定响应 Provider、真实工具/通信结果与真实模型验证分别记录。

## 启动设计时的核对（历史）

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

## S0 已落地的设计

- S0 只建设 CLI、Core、protocol、transport；仅支持 core/ping/本地 version。
- 使用 loopback TCP + NDJSON + JSON-RPC 风格受限信封；请求 id 配对，单连接逐条处理。
- 默认地址 127.0.0.1:7438；客户端、帧等待与写入有界；Windows Ctrl+C、连接清理和端口重启是验收重点。
- 版本来自安装包元数据。模块不反向依赖 TUI；事件流和多路复用在 S2 实现。
- 提交节点为启动文档、可安装入口、Core 通信、完整验收与交接；没有导入旧历史。

## 已知限制与下一步

- 项目使用独立 CPython 3.12.14；系统默认 Python 3.9.12 未更改。环境和运行产物已忽略。
- 实际结果与故障记录见 [S0_VALIDATION.md](S0_VALIDATION.md)，调用链讲解见 [S0_WALKTHROUGH.md](S0_WALKTHROUGH.md)。
- CI 文件已配置 Windows/Linux + Python 3.12，但无远端，尚无 GitHub CI 或 Linux 实测结果。
- Windows 控制台真实 CTRL_C_EVENT 已验证；人工在键盘上按 Ctrl+C 的演示未执行。两者明确区分。
- 没有 Provider、真实模型调用或 Agent Loop；S0 不需要模型密钥，不扩展为 Agent 能力已完成的声明。
- 下一项由用户指定；若进入 S1，先设计最小 Agent Loop 与只读工具。不自动开始，也不自行创建远端或公开发布。

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
- C1 当时只验证安装与配置；随后在 C2 接入真实 TCP Core/ping，不将 C1 单独算作 S0 全体验收。

## S0 实施记录：C2

- 实现严格请求/响应模型、64 KiB NDJSON 边界、单连接串行处理、独立连接并发、请求编号配对与错误分类。
- Core 拥有监听器、连接与任务；关闭时停止接受连接、关闭客户端、取消并等待任务，最后等待监听器关闭。
- `uv run --locked --offline pytest -q`：32 passed。包含真实 TCP ping、拆包/合包、CRLF、坏请求后继续服务、半帧断开、超时/超长客户端隔离、内部错误脱敏、端口占用和同端口重启。
- 已讲解 ping 调用链：CLI → 客户端请求编号 → 完整帧 → 严格协议校验 → Core → 同编号响应 → 客户端验证。S0 保持连接内串行，S2 再引入事件与多路复用。
- C2 当时尚未验证 Windows 实际控制台退出；该项、CLI 子进程与独立安装在 C3 补齐，见下方。

## S0 实施记录：C3

- 最终 `uv run --locked --offline pytest -q`：51 passed in 8.69s。新增客户端故障对端、响应结构与 id 拒绝、CLI 子进程退出码等检查。
- 首轮 50 passed / 1 failed 的原因是 Windows 对残片连接返回重置而非 EOF。修正断开判据后完整通过，同端口重启检查继续保留。
- 独立 wheel 构建成功；安装到 `.cache/install-check` 并从该目录执行，导入确实来自其 site-packages，version 正确，真实 TCP 与退出/重启成功。
- `scripts/verify_windows_ctrl_c.py` 在可控制自身进程的 Windows 会话执行。开发环境真实控制台事件退出耗时 0.359/0.375 秒；独立安装环境同端口 50647 两次耗时均 0.360 秒、退出码均 0，活动连接已结束。
- 受限控制事件失败尝试和强制清理已记录，不作为正常退出成功证据。未修改 Core 信号处理去适配受限工具通道。
- 新增最小 CI 工作流、README 安装/演示、S0_VALIDATION 和 S0_WALKTHROUGH，更新当前阶段状态。已围绕 ping 讲解输入、数据流、编号、错误与资源归属。
- 收尾检查：`uv sync --locked --offline` 成功检查 13 个包；8 份 Markdown 的 25 个本地链接、围栏和 JSON 示例有效；已验证 wheel 中全部 Python 模块与当前源码一致；`git diff --check` 无输出。
- 文档与代码随验收提交保存，stage-0 用于定位；无 push、无远端。旧 CodePilot 工作树仍干净，HEAD 仍为 75a4fdf7a3d5ff770dfc9c136b3b1881c912ba35。
