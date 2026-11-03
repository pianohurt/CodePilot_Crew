# 当前进度与会话交接

更新时间：2026-11-03。

## 当前状态

- 项目：CodePilot Crew；仓库名：CodePilot_Crew；工作目录：G:/实习项目/CodePilot_Crew。
- 用户已确认 Python 包与命令均保留 codepilot。
- 启动设计已整理于 [S0_DESIGN.md](S0_DESIGN.md)，包含目标边界、S0 最小结构、模块职责、外部接口、故障验收与提交节点。
- S0—S2 实现、验证与讲解完成，本机 Windows 验收通过；用户已推送 S1，本轮 S2 未 push；S3—S9 未开始，Linux 和远端 CI 本轮未验证。
- 当前版本 0.3.0，已有只读 Agent、真实 SSE、单连接响应/事件多路复用、启动/取消、JSONL 记录与基础 Textual TUI。
- 用户已关联 origin=https://github.com/pianohurt/CodePilot_Crew.git，起点为用户提交 b7386f0（忽略本地 docs/）。保留 stage-0=eda390d。本轮只做本地提交，未 push，也未联网查询远端 CI。
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
- CI 文件已配置 Windows/Linux + Python 3.12；远端由用户关联，本轮未查询 GitHub CI，Linux 尚无本机实测结果。
- Windows 控制台真实 CTRL_C_EVENT 已验证；人工在键盘上按 Ctrl+C 的演示未执行。两者明确区分。
- S2 已有事件流和 TUI；仍没有写入/命令、会话恢复、压缩、自动重试、多 Agent、MCP 或 Skills。取消和断连会清理连接拥有的协程等待，不保证强制中断所有底层只读 I/O。
- S2 已完成，等待用户指定 S3，不自动开始后续阶段或 push。

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

## S1 实施记录：实现与行为验证

- 保留用户新增的 docs/ 忽略规则，新增 S1_DESIGN/S1_VALIDATION/S1_WALKTHROUGH 分析文件留本地；已有跟踪文档继续更新。
- 按 S1_DESIGN 实现完整响应模式的 Provider → AgentLoop → 工具 → 观察循环，RPC agent.run 与 CLI run 只传目标和步数，不把密钥或工作根目录交给客户端。
- 配置显式读取 .env，进程环境优先；密钥使用 SecretStr，远端错误正文不回显；.env 已被忽略。
- 验证请求/结果 id 配对、错误回填、完整批次校验、重复 id、max_tokens/未知停止原因、步数/调用数预算、超时与并发上下文隔离。
- `uv run --locked --offline pytest -q`：105 passed in 13.46s（包含 S0 的 51 项）；无跳过。Windows 符号链接权限不可用时，测试实际创建目录 junction 验证越界拦截。
- 固定响应 + 真文件 + RPC/CLI：2 步通过；只证明程序链路，最终文本由脚本预先给定。
- 真实 deepseek-flash：先在 Runner 层 2 步读取随机标记并回答，再经 CLI/TCP/Core 完整链路 2 步验证。文件内容实际进入下一模型请求，最终回答含随机标记。
- 首次真实调用因未支持 thinking/signature 返回 invalid_response，且未执行工具。检查结构后补充 opaque 原样回传与回归测试；证据轨迹省略 thinking 正文/签名，未把失败尝试算通过。
- Windows 0.2.0 真实控制台事件回归：端口 52109 两次退出码均 0，耗时均 0.406 秒，活动连接结束且同端口重启成功。
- 调用链和本轮取舍已讲解；独立安装与最终文档收尾在验收节点记录。没有自动 push 或进入 S2。

## S1 验收与交接

- 实现节点为 5054632；stage-1 用于定位本次验收/交接提交，stage-0 保留。当前 main 相对本轮起点 b7386f0 增加两个本地提交，未 push。
- 独立构建 codepilot-0.2.0-py3-none-any.whl，安装到新环境 .cache/s1-install-check（13 个包），从该目录确认导入来自其 site-packages，version=0.2.0。
- 安装后的固定响应 RPC/CLI 2 步通过；安装后的真实 deepseek-flash RPC/CLI 2 步通过。均验证实际读取、下一请求观察及最终随机标记，证据为 artifacts/s1-fixed-installed.json 与 artifacts/s1-real-installed.json。
- 新增本地 S1_DESIGN、S1_VALIDATION、S1_WALKTHROUGH；README 公布安装、配置、只读运行、验证命令和限制，源码/测试/脚本均可审查；.env 和证据文件没有进入提交。
- Thinking 正文与签名只在内存中原样透传给模型，不解释或展示；演示轨迹省略它们，不能说成完整原始 API 记录。
- status=success 只表示循环正常给出最终文本，不自动判断任意任务达成。本案例通过额外文件标记核对证明读取任务完成。
- 阶段到此为止，等待用户指定 S2；无自动 push、无后续阶段实现。
- 收尾检查：11 份 Markdown、29 个本地链接、代码围栏与 JSON 示例有效；0.2.0 wheel 的全部 Python 模块与当前源码一致，归档不含缓存/配置/字节码；已跟踪内容未检出本机配置密钥。确认 .env、运行证据与新增 S1 分析文档被忽略，`git diff --check` 通过。旧 CodePilot 工作树仍干净，HEAD 不变。

## S2 实施记录：功能与行为验证

- 起点：291665d，main 与 origin/main 的本地跟踪记录一致，用户确认已推送 S1；没有改动用户 docs/ 忽略规则。
- 设计记录在本地 S2_DESIGN.md。实现 EventBus/RunEvent、按 run_id 的 JSONL events 与模型 Trace，包含请求、响应、API usage、耗时、错误和取消；省略 thinking 正文/签名并替换配置密钥，过长记录明确截断，不声称是会话原始记录。
- 持久 TCP 连接用独立读循环、并发请求、单一写队列；客户端用 id→Future 分流响应与事件。空闲连接保留，残帧超时；断连结束全部等待者并取消本连接拥有的运行。
- agent.start 返回 run_id；agent.cancel 限制连接归属。取消是请求→Core 清理→run.finished 确认，处理了开始前取消竞态。队列和并发有上限，超限明确断连，不悄悄丢最终事件。
- 真实 SSE 重建完整响应后才执行工具；半截输入、缺少 message_stop、NaN、错误响应均不会执行工具。没有自动重试。
- TUI 用 run_id+step 分开草稿，model.completed 替换步骤文本，最终回答以 run.finished 为准；失败/取消不把草稿当完成，工具结果可查看。
- 首轮回归 6 failed/99 passed：事件参数重名导致工具事件失败，首字节处理导致刚好超长的无换行消息未报帧超限。修正后原 S0/S1 的 105 项通过。
- 新 TUI 测试首轮 14 passed/1 failed：Ctrl+C 被控件快捷键处理；改为优先级绑定后，通过真实挂载的取消检查。后续补齐边界，最终 `uv run --locked --offline pytest -q` 为 130 passed in 12.39s，无跳过。
- 固定流真实挂载演示通过：4 条文本增量、真实文件读取、下一请求观察与最终显示一致。真实 deepseek-flash 挂载演示通过：96 条增量事件、真实随机标记读取与最终显示一致；两类结果不混用。
- Windows 0.3.0 真实 CTRL_C_EVENT 回归：端口 64294 两次退出均 0、耗时均 0.359 秒，同端口重启与活动连接清理通过；不是人工键盘演示。
- 已讲解增量/步骤完成/最终回答、取消确认与连接归属。独立安装、最终说明和提交交接在收尾节点记录；不自动 push。

## S2 验收与交接

- 实现节点 5167bc9；stage-2 定位验收交接提交。本轮相对已推送的 291665d 新增两个本地提交，未 push，保留 stage-0/stage-1。
- 独立 wheel 为 0.3.0，安装到新环境 .cache/s2-install-check（21 个包），从其 site-packages 加载；固定流与真实模型均实际挂载 TUI。
- 安装后的固定流：2 步，4 条文本增量，14 条事件/4 条 Trace；真实 deepseek-flash：2 步，84 条增量，94 条事件/4 条 Trace。随机文件读取、下一请求观察及最终 widget 与完整回答一致。
- 最后一个完整测试命令使用关闭缓存插件和工作区内 basetemp：130 passed in 13.60s，无跳过。额外跨权限测试曾因缓存/临时目录权限失败，未计入通过；已隔离测试与安装流程。
- 新增本地 S2_DESIGN、S2_VALIDATION、S2_WALKTHROUGH，README 更新 TUI 启动/取消/记录与验证命令。证据与 SVG 截图保存在 artifacts/，密钥、运行记录和新增分析文档不进入提交。
- 已围绕一次读取讲解请求 id/Future、run_id/step、增量与最终替换、取消确认及日志/事件/Trace 的区别。阶段到此，等待用户指定 S3。
- 收尾检查：14 份 Markdown、29 个本地链接、围栏与 JSON 示例有效；0.3.0 wheel 全部 Python 模块与当前源码一致，不含配置/缓存/字节码；4 份 SVG 可解析，4 个演示的 228 条事件通过类型、seq 和最终状态检查；已跟踪内容及证据未检出配置密钥。新阶段分析、.env、运行记录均被忽略，`git diff --check` 通过。旧 CodePilot 工作树干净、HEAD 仍为 75a4fdf7a3d5ff770dfc9c136b3b1881c912ba35。
