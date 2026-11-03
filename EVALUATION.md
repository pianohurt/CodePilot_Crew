# CodePilot Crew 1.0.0 验收与项目展示

CodePilot Crew 是一个面向本地工作区的终端编程 Agent。使用 Python、asyncio、Textual 和 Anthropic Messages 兼容接口实现。可展示的工程工作包括工具调用校验与审批、前后台委派的权限和回收、可恢复会话与上下文压缩、流式故障恢复，以及 MCP 和 Skill 的组合。项目采用 AI 辅助开发，设计参考与实现证据按阶段保存在本仓库。 [功能与架构](README.md#架构与设计证据) · [阶段记录](docs/PROGRESS.md)

## 快速演示

1. 从仓库根目录按 README 安装，运行 `codepilot start`。首次配置通过隐藏输入收集密钥。
2. 提交 `/skill review src/codepilot/core/runner.py`，观察本轮上下文估算、实际工具调用与最终回答。
3. 新建会话后让 planner 后台调查模块，再用 `agent_result` 查询。可在工具区看到关联的子任务状态。
4. 用固定响应演示真实终端界面和文件读取。命令会生成 SVG 与 JSON，并核对工具结果和最终界面一致。

```sh
uv run --locked --offline python scripts/verify_s2.py --provider fixed --output artifacts/s9-tui.json
```

## Windows 固定任务集

Windows 与本机 Ubuntu WSL 均通过 244 项全量回归及 9 项固定任务。独立环境安装 1.0.0 wheel 后，两个平台均可查看版本和启动帮助。Ubuntu 本机日志见 `artifacts/s9-linux.txt`。

下表来自 1.0.0 的一次本地执行。通过判据由集成断言与实际文件、连接或进程结果组成。模型请求数包含测试准备阶段的请求与流式尝试。工具派发包括被权限拒绝的请求。重复按同一 run 中完全相同的工具名和参数计数。用量为模型返回的字段，固定响应案例的模拟用量单独标识。

| 固定任务 | 结果与判据 | 模型请求 | 工具派发 | 秒 |
|---|---|---:|---:|---:|
| 读取定位 | 读取真实文件且观察进入下一请求 | 2 | 1 | 0.702 |
| 受控修改 | 模拟批准一次后文件内容正确 | 2 | 1 | 0.024 |
| 权限拒绝 | 模拟拒绝一次后文件内容不变 | 2 | 1 | 0.017 |
| 流式断线 | 重新读取模型流，文件实际写入一次 | 4 | 1 | 0.229 |
| 长历史 | 重开会话库后压缩，配对和原始记录核对通过 | 17 | 1 | 0.370 |
| 重启会话 | 新 Core 进程基于持久观察回答，恢复轮零工具 | 3 | 1 | 6.143 |
| 子任务失败 | 查询返回步数耗尽的失败状态 | 6 | 5 | 0.017 |
| MCP 错误 | 远端写后失败，动作次数为一次 | 0 | 1 | 0.313 |
| Skill 限制 | 空白名单令工具派发返回拒绝 | 2 | 1 | 0.014 |

9 项通过，重复派发均为 0，人工介入均为 0。子任务用量用例注入 input_tokens=18 与 output_tokens=12，核对父子合计。完整字段可用下述命令生成到 `artifacts/s9-offline.json`。

```sh
uv run --locked --offline python scripts/evaluate_s9.py --output artifacts/s9-offline.json
```

## 真实模型重复评测

用同一模型配置、相同的初始文件字节与目标，重复三次。每次由独立安装环境中的 `start` 启动本地 Core，实际调用 read_file 后核对路径、标识与文件状态。

```sh
uv run --locked python scripts/evaluate_s9.py --env-file .env --trials 3 --output artifacts/s9-real.json
```

首轮模型为 deepseek-flash。三次试验均在首次请求返回 provider_error，工具派发均为 0。另一次诊断复跑收到 HTTP 503。首轮报告保存在 `artifacts/s9-real-initial.json`，真实模型验收等待本机配置更新后复验。S6 至 S8 已有独立的真实模型通过记录。
