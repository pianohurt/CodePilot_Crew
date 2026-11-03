# 当前进度与会话交接

更新时间：2026-11-03。

## 当前状态

- 项目：CodePilot Crew；仓库名：CodePilot_Crew；工作目录：G:/实习项目/CodePilot_Crew。
- 用户已确认 Python 包与命令均保留 codepilot。
- 启动设计已整理于 [S0_DESIGN.md](S0_DESIGN.md)，包含目标边界、S0 最小结构、模块职责、外部接口、故障验收与提交节点。
- S0—S3 本机 Windows 验收完成；S4 实现与行为验证通过，最终安装/真实验收进行中。用户已推送 S1，后续本地提交没有自动 push；S5—S9 未开始，Linux 和远端 CI 本轮未验证。
- 当前版本 0.5.0，已有会话列表/恢复、独立原始记录、会话笔记和背景文件；保留 Agent、SSE、JSONL、Textual、写入/命令、三态权限和逐次审批。
- VS Code 退出键冲突修复 c3a9318 已保留：F10 和“退出”按钮；本轮基于该干净起点实施用户指定的 S4。
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
- S4 已有保存上下文后的会话继续交互；仍没有压缩、自动重试、多 Agent、MCP 或 Skills。取消和断连会清理连接拥有的等待及普通命令进程树，不保证强制中断所有底层只读 I/O。
- 本轮由用户指定实施 S4，不自动开始 S5 或 push。命令以当前用户权限运行；会话恢复不重放工具或恢复命令进程，应用层审批与 cwd 检查不提供操作系统隔离。

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

## S3 实施记录：实现与行为验证

- 用户指定“S2 结束后执行 S3”；从 stage-2=3bda015 的干净工作树开始，仅实施 S3。设计见本地 S3_DESIGN.md，保留用户 docs/ 忽略规则，不自动 push。
- ToolRegistry 统一执行参数校验、deny 拦截、无副作用预检、allow/ask、tool.started 和受限执行；读取默认 allow，write_file/bash 默认 ask。PermissionPolicy 由 Core 注入，客户端/模型不能放宽。CLI agent.run 无审批通道时 ask 返回 approval_unavailable。
- ApprovalBroker 将逐次 Future 绑定连接，approval.respond 只接受所属连接答复；拒绝、60 秒超时、取消和断连均结束等待，旧审批失效。TUI 展示完整写入正文或实际 shell 脚本，实际点击允许/拒绝有挂载测试；工具开始事件晚于批准。
- write_file：UTF-8 最多 12 KiB、已有父目录、显式 overwrite；预检 hash，批准后复核，临时文件 + fsync + os.replace，冲突不覆盖也不重试。小文件提交不含异步让出，完成的修改不会因后续取消自动回滚。
- bash 工具在 Windows 使用实际 PowerShell，完整编码前置脚本进入审批；cwd 解析在工作区内，环境白名单不继承模型 API 配置。独立标准库 supervisor 在执行命令前完成进程归属与 ready/确认握手；Windows Job 的 KILL_ON_JOB_CLOSE 管理普通子孙进程，POSIX 用独立进程组。
- 故障定位发现 Windows venv redirector 不能当作实际 supervisor 句柄，改用基础解释器启动 worker；同时避免等待后台后代持有的管道 EOF 才触发清理。正常结束、超时、取消、Core 关闭均有真实多级进程树退出测试。
- 无法确认清理时 ToolResult 内部 stop_run 标志终止整个 run，不再执行同批后续工具或下一模型请求；该内部字段不进入模型消息。配对错误保留实际 tool_use_id。没有自动重试有副作用动作。
- 最终行为命令：`uv run --locked --offline pytest -q -p no:cacheprovider --basetemp <工作区内已核验目录>`，164 passed in 21.01s，无跳过。先前 S0—S2 的 130 项也保持通过；覆盖参数/越界、审批归属/过期、拒绝无副作用、批准后文件冲突、错误回填、输出排空截断和进程清理。
- 固定响应挂载 TUI 实例：真实读取 calc.py/check.py，检查先失败，批准修复后成功，5 步；写入/命令授权早于执行，成功检查输出进入下一模型请求。动作和最后文字预先给定，只证明程序链路。
- 初步真实 deepseek-flash 受限示例也通过，但当时提示给出具体替换规则，仅证明真实模型的受控修改链路。已加强最终脚本：不提供修复答案，必须读取两个源码、定位问题；独立安装后的真实结果在收尾记录，不把初步案例冒充完整定位证明。
- Windows 0.4.0 真实 CTRL_C_EVENT：端口 57748，两次退出码均 0，耗时 0.344/0.360 秒，同端口重启和活动连接收尾通过；这不是人工键盘演示。活动命令的关闭清理由进程树集成测试另行验证。
- 已讲解关键边界：完整调用 → 注册表预检 → 连接拥有的审批 → 获准后执行 → 同编号结果回填；清理失败停止运行。独立安装、最终真实验证、文档与阶段标签在验收节点完成。

## S3 验收与交接

- 实现节点 8deb7aa；stage-3 定位本次验收交接提交，保留 stage-0 至 stage-2。本轮 S3 两个本地逻辑提交，未 push；相对本地 origin/main=291665d，S2/S3 合计四个新提交，不代表已实时查询远端。
- 独立构建 codepilot-0.4.0-py3-none-any.whl，使用锁文件运行时约束安装到新环境 .cache/s3-install-check，共 21 个包。切换到该目录确认版本 0.4.0，包与 command_worker.py 均从其 site-packages 加载。
- 安装后的固定响应：5 步、37 条事件/10 条 Trace，真实读取、批准写入和命令执行；预设第一次命令失败并回填 is_error，修复后独立检查成功。最终文本预先给定，只证明程序链路。
- 安装后的真实 deepseek-flash：4 步、256 条事件/8 条 Trace、230 条文本增量。提示未给修复答案；模型读取 calc.py/check.py，定位加法写成减法，仅提出一次写入和一次检查。两次获准后执行；独立检查 baseline exit=1 → final exit=0，成功命令的 exit_code=0 与随机 CHECK_OK 标记进入后续模型请求，最终回答标记一致。
- 验证脚本在临时目录运行，实际挂载 TUI 并通过按钮自动批准严格限定的示例函数修复和 python check.py，其他动作拒绝。这不是人工审批演示，也不允许模型自动改本项目文件。证据 artifacts/s3-fixed-installed.json 与 artifacts/s3-real-installed.json，截图及 JSONL 同样被忽略。
- 新增本地 S3_DESIGN、S3_VALIDATION、S3_WALKTHROUGH，README 与已跟踪状态文档同步。已用真实示例解释审批 Future 的连接归属、完整预览、批准后 hash 复核、同编号错误回填、Job/supervisor 和清理失败停止。S3 到此完成，等待用户指定 S4。
- 收尾检查：17 份 Markdown、29 个本地链接、围栏和 4 个 JSON 示例有效；wheel 中 35 个 Python 模块与当前源码逐字节一致，不含配置/缓存/字节码；4 份 SVG 可解析，4 个 S3 演示共 664 条事件通过类型、seq、run_id 与最终状态检查。已跟踪内容和上述证据未检出配置密钥，.env、证据和新增阶段文档均被忽略；`git diff --check` 通过。
- 只保证正常超时/取消/关闭路径的普通命令进程树清理，不提供恶意脚本隔离、外部服务回收或强制杀死 Core 后的恢复；应用层写入检查也不保证抵抗恶意并发路径替换。Linux、远端 CI、人工键盘演示未验证。
- 旧 CodePilot 只读复核：工作树仍干净，HEAD=75a4fdf7a3d5ff770dfc9c136b3b1881c912ba35，未被修改。

## TUI 退出快捷键修复：2026-11-03

- 用户人工反馈：在 VS Code 集成终端中 Ctrl+Q 打开编辑器设置而未退出 TUI。结合 VS Code 官方终端快捷键文档，推断按键被宿主编辑器先处理；Textual 的 priority 只对已到达应用的按键生效。未在用户当前 VS Code 配置中独立复现具体命令映射。
- 页脚将退出键改为 F10，控制栏增加始终可用的“退出”按钮；Ctrl+Q 作为隐藏兼容绑定保留，Ctrl+C 仍只取消当前任务。若宿主也占用 F10，可点击按钮或用 Tab 聚焦后按 Enter。没有修改用户的 VS Code 设置。
- 两个出口共用 Textual 的 action_quit → exit → on_unmount → RpcClient.close，Core 在连接断开后取消该连接的运行并结束待审批 Future，Core 本身继续监听。没有增加审批自动通过或任务恢复逻辑，也未开始 S4。
- 实际验证命令：`uv run --locked --offline pytest -q -p no:cacheprovider --basetemp <工作区内已核验的独立目录> tests/integration/test_s2_tui.py tests/integration/test_s3_approvals.py`，18 passed in 4.41s，无跳过。覆盖输入框聚焦/连接失败时退出、流式运行中按键与按钮退出、等待写入审批时退出、任务/审批清理及退出后新连接 ping；待审批文件保持原内容，模型没有进入下一次请求。
- 这是实际挂载 Textual 的离线自动化与真实 TCP 验证，按键由 Pilot 注入；未新增真实模型调用，未执行当前 VS Code 的人工按键验证。本轮只运行上述相关测试，S3 的 164 项全套通过记录属于前次验收。
- README 已同步快捷键、备用按钮与宿主拦截原因。修复作为独立本地提交保存，保留现有阶段标签和版本 0.4.0，不 push。

## S4 实施记录：实现与行为验证

- 起点 c3a9318，工作树干净，用户说明退出键调整后授权 S4。F10/退出按钮及原取消语义保留；只在独立新仓库实施，不进入 S5，不 push。
- 设计见本地 S4_DESIGN.md。采用 Python 标准库 SQLite，无新增依赖；sessions 保存会话元信息、工作快照和笔记，runs 保存任务引用/结果/用量，records 用 run_id+seq 保存原始领域增量。查阅 Python 3.12 sqlite3 与 SQLite transaction 官方文档，没有直接复用参考仓库代码。
- ExecutionContext.adopt 显式提交新增消息与工作快照；原始模型请求/完整响应、工具意图、结果和取消独立追加，不通过 messages 长度推算保存游标。写入及读回均检查工具配对；取消半批保留实际动作原始记录，工作快照只采用完整消息批次。
- SessionManager 在 agent.start 返回前占用会话名额，同会话并发 -32022，不同会话独立；session.create/list/resume、CLI sessions/session-new/resume、run/tui --session 与 TUI 新建/列表/恢复均接入。查看/恢复不调用模型或重放动作。
- 默认 .codepilot/ 被 Git 和普通文件工具排除；同数据目录的 owner SQLite EXCLUSIVE 事务保证一个 Core，退出释放锁。数据库事务不跨模型/审批等待。启动恢复将 running 标记 interrupted，提示重新检查实际副作用；正常取消/失败也保留提示。损坏会话显示 corrupt，其他会话可用；物理库损坏不静默重建。最终状态提交失败时暂禁该会话继续，等待重启核查。
- CODEPILOT.md 作为有限 UTF-8 背景进入每次会话请求；显式 note_save 默认 ask，保存本会话最多 20 条、每条 4000 字符，完整预览后批准。同 key 替换，不跨会话默认共享；权限不因文字放宽。
- 私有库保留完整 opaque thinking/signature 以兼容模型恢复，UI/Trace/导出演示证据省略正文。与 S1/S2 当时仅内存透传的历史边界不同；配置 API Key 在持久字段中替换，不保存 HTTP 头。数据库可能含源码和对话，未加密；领域原始记录不是 HTTP/SSE 字节日志。
- 原有 169 项（含退出修复）回归通过。新存储专项首轮 16 passed；新 RPC/TUI 首轮 2 passed/1 failed，测试误用旧 run 的完成状态，修正等待新 run_id。全套后出现同名测试模块收集冲突，分别命名 test_s4_store/test_s4_sessions；另发现 Textual Button 的 0.2 秒防连点影响瞬时重复点击，连续追问改用输入框 Enter 验证，不为自动化修改产品防连点逻辑。
- 补齐启动指定坏会话的提示与新建入口：缺失/损坏不会误报成网络失败，零模型调用即可新建恢复可用状态。最终命令 `uv run --locked --offline pytest -q -p no:cacheprovider --basetemp <工作区内已核验目录>`：195 passed in 29.44s，无跳过；证据 artifacts/s4-pytest.txt。
- 开发与初步独立安装的固定案例：第一进程读实际随机标记并完成 2 步；停止并删除源文件，第二进程列出/恢复后 1 步回答相同标记，无工具调用，实际请求包含前轮 tool_result。固定文本预设，只证明链路。额外使用 os._exit(23) 直接结束测试 Core，重启后 interrupted，已写文件内容/mtime 不变，没有重放。
- 初步独立安装的真实 deepseek-flash：第一轮 2 步读取随机标记，第二个 Core 进程恢复后 1 步回答；原文件已删除，新请求含前次观察，恢复轮工具数 0。随后只修复初始坏会话提示；最终安装回归在收尾节点记录。
- Windows 0.5.0 真实 CTRL_C_EVENT：端口 60919，两次退出均 0，耗时 0.360/0.406 秒，活动连接结束、存储锁释放后同端口重启成功。非人工键盘演示，也不作为崩溃后命令资源回收的证据。
- 已讲解 session 跨 run、工作快照与原始增量分工、审批笔记进入下轮请求以及中断不重放。安装、最终证据检查、讲解文档和 stage-4 在验收节点收尾。
