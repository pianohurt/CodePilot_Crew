# 项目目标与设计边界

S0—S7 已完成本机 Windows 验收，版本 0.8.0，228 项离线检查通过。S7 实现 MCP stdio 工具发现、参数校验与执行，官方 filesystem 服务通过固定响应和真实模型验证。具体结果与限制见 PROGRESS。仓库名为 CodePilot_Crew，工作目录为 G:/实习项目/CodePilot_Crew。Python 包与命令均为 codepilot，展示名为 CodePilot Crew。

## 用户与目的

开发者目标是 AI Agent 开发/应用实习。希望亲身经历展示仓库从目标文档、初始骨架、阶段实现、测试到发布的过程。使用 AI 辅助编码，并逐阶段掌握执行链路、设计取舍和验证方法。

产品是本地终端编程助手，主要完成代码阅读、问题定位、受控修改、命令验证和复杂任务委派。

## 必须覆盖的四条能力

1. Agent 循环与工具调用：模型决策、工具执行、结果观察的循环；ToolRegistry；参数校验；调用标识配对；错误反馈；权限和步数上限。
2. 多 Agent 编排：子任务作为工具；独立上下文；前台等待与后台异步；父子事件关联；按 run_id 查询实际结果。
3. 上下文压缩与流式重试：摘要保留目标、约束、文件状态和待办；长任务继续执行；连接与读取错误有限次退避；完整回答与界面一致。
4. MCP 与 Skills：发现和调用外部工具；统一注册与服务名前缀；Markdown 提示模板及工具白名单；项目 > 用户 > 内置的三级加载。

简历的项目描述提供能力目标，不代表新仓库的功能已经实现。实际完成情况由 PROGRESS.md 和代码验证结果记录。

## 初步技术选择

Python 3.12、asyncio、Pydantic、Anthropic Messages API 兼容接口、Textual、pytest、uv。保留常驻 Core 与客户端分开的总体设计。Windows 原生可运行，并尽量兼容 Linux。

## 目录如何确定

最开始确定顶层组织、模块职责、依赖方向和关键数据边界。当前阶段只创建必要目录与文件；具体类、函数、字段随实现逐步确定。

S0 的目录轮廓如下，文件职责、接口和验收案例见 [S0_DESIGN.md](S0_DESIGN.md)。仅创建当前需要的模块，实际结果见 [S0_VALIDATION.md](S0_VALIDATION.md)：

```text
CodePilot_Crew/
  AGENTS.md
  README.md
  pyproject.toml
  src/codepilot/
    __init__.py
    cli.py
    core/                 # 服务生命周期与配置
    protocol/             # 请求、响应与错误模型
    transport/            # 本地 TCP 连接与分帧
  tests/
  docs/
    PROJECT_BRIEF.md
    ROADMAP.md
    PROGRESS.md
    S0_DESIGN.md
```

S1 建立 agent、tools、context 和 runner。S2 增加 TUI、事件、记录、SSE 与持久 RPC。S3 增加权限、审批及写入和命令工具，Windows 命令由独立 supervisor 和 Job 管理生命周期。S4 增加会话存储和 note_save。S5 增加压缩与流式重试。S6 在 Core 增加委派生命周期，并通过内建工具接入。S7 的 mcp 模块负责 stdio 连接、发现与工具适配。下一阶段接入 Skills。

基本依赖方向：界面和客户端通过协议调用 Core；Runner 装配执行上下文与服务；Agent Loop 通过 Provider 和工具接口执行；具体工具不反向依赖 TUI。

## 四项已有经验必须保留

- 压缩工作消息不能造成原始记录漏存。
- 重试成功后完整回答应替换界面草稿，并区分不同 run 和 step。
- MCP isError 必须保留，不把远端失败当成功。
- Skill 参数必须进入实际请求；空白名单与缺省分开；限制传递给子 Agent。

## 参考资料

- KamaClaude：https://github.com/youngyangyang04/KamaClaude，已有分析参考提交 70decaf06b284b8e72b90f8369ec75bbfcf4dc81。
- AemeathCode：https://github.com/Nijikasuki/AemeathCode，阶段方法与实现分析参考 cd98b83e46dba24abfd103473d4c711396c181f3。
- 旧 CodePilot：https://github.com/pianohurt/CodePilot；本地 G:/实习项目/CodePilot，参考实现及四项回归案例，不是新仓库工作区。

后续查看更新版本时记录实际引用版本，区分旧分析与新发现。直接复用代码时保留来源及适用说明，不以改名替代贡献说明。

## 完成的含义

新环境可按 README 安装与启动；四条能力有实际演示；异常路径有复现和测试；日志可解释；每阶段有真实提交和说明；开发者能够讲清关键调用链及保留的限制。

不预设成功率、提速或节费数字。固定模型测试、真实模型测试和整体效果对照分别记录。
