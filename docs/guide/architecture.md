# 运行时架构与核心生命周期

Cade 绝不仅是一个局限于终端命令行的 CLI 工具，而是一个拥有**统一应用运行时内核（Unified Agent Harness）**、开箱即用支持 **TUI 全屏终端**、**CLI 极速交互** 与 **Web 浏览器工作台** 的现代 Coding Agent 平台。

---

## 1. 统一运行时内核与三端工作台架构

```mermaid
flowchart TD
    subgraph Surfaces["三端一体交互形态 (Tri-Surface Interfaces)"]
        TUI["TUI 全屏沉浸式终端 (默认入口 `cade`)"]
        CLI["CLI 极速终端 REPL / Headless (`cade cli` / `exec`)"]
        Web["Web 现代浏览器工作台 (`cade web`)"]
    end

    Surfaces --> AppCore["统一应用产品层 (CadeApp)<br/>• Plan / Build / Act 模式管理<br/>• 编码工具箱调度与 NOTE.md 状态协作"]

    AppCore --> Harness["运行时安全与支撑层 (Harness)<br/>• JSONL 追加写会话账本与快照回滚 (/undo)<br/>• Linux Bubblewrap 容器沙箱隔离<br/>• 三态权限判定引擎与 Reviewer 审查<br/>• MCP 外部协议扩展、三层记忆与技能"]

    Harness --> Loop["智能驱动与上下文核心 (Agent Loop)<br/>• 95% 水位线无摘要换窗 (Summary-free Rollover)<br/>• 只读并发池与串行写屏障 (防脏写保护)"]

    Loop --> Provider["统一模型适配层 (AI Providers)<br/>• OpenAI, DeepSeek, ChatGLM, MiMo, 本地模型<br/>• 思考链 (Thinking) 流式输出与用量统计"]
```

各层级职责自上而下严格解耦，应用内核通过统一装配工厂（Assembly）注入能力：

1. **三端一体交互层 (`src/cade/cli/`, `src/cade/server/`)**：
   - **TUI 工作台**（默认入口）：提供类 IDE 的全屏分屏视图与 Diff 实时审查看板；
   - **CLI REPL**：极轻量行交互，支持快捷键、`@` 文件补全、`!` 穿透与 CI 脚本自动化；
   - **Web 工作台**：基于 FastAPI 与 WebSocket 实时双向流，支持多端可视化管理。
2. **应用产品层 (`src/cade/coding_agent/`)**：
   承载与编码任务直接绑定的业务语义。管理 Plan/Build/Act 状态机、系统 Prompt 拼装与目标验收。
3. **运行时支撑与安全层 (`src/cade/harness/`)**：
   提供底层的安全与持久化地基。通过 Linux Bubblewrap 隔离 Shell 命令执行，通过追加写 JSONL 保证全量事实留存，并集成 MCP 外部协议。
4. **智能驱动与上下文核心 (`src/cade/agent/`)**：
   负责核心事件驱动循环。当上下文占满 95% 时触发换窗重置，并负责只读工具并发派发与写操作串行排队。
5. **统一模型适配层 (`src/cade/ai/`)**：
   抹平大模型服务商协议差异，原生支持深层思考链解析与 Token 统计。

---

## 2. 一次回合（Turn）的完整生命周期

当你在终端中敲下回车发送一条指令时，Cade 内部经历以下执行链条：

```mermaid
flowchart TD
    Start["用户输入"] --> Step1["1. 写入 Session 账本 (JSONL 追加写)"]
    Step1 --> Step2["2. 收集环境与上下文 (NOTE.md, 文件树, 激活规则)"]
    Step2 --> CheckBudget{"3. 95% 上下文预算检查"}
    CheckBudget -- "超过 95%" --> Rollover["触发换窗交接至 NOTE.md<br/>开启干净上下文窗口"]
    CheckBudget -- "余量充足" --> Infer["4. 向 AI Provider 发起流式推理"]
    Rollover --> Infer
    Infer --> Gate["5. 工具门控与权限审查 (ToolGate & PermissionEngine)"]
    Gate -- "只读工具" --> ReadPool["6a. 并发池并行执行<br/>(read_file, grep_search 等)"]
    Gate -- "写操作/命令" --> WriteBarrier["6b. 串行屏障排队 + 冲突校验<br/>(edit_file, bash)"]
    ReadPool --> Result["7. 工具结果写回账本并追加上下文"]
    WriteBarrier --> Result
    Result --> Done{"8. 任务是否完成?"}
    Done -- "需要后续步骤" --> Infer
    Done -- "已完成" --> Finish["结束回合并等待用户输入"]
```

---

## 3. 核心设计原则

- **代码即真相**：所有运行时状态最终都可以由 Session JSONL 账本回放重构。
- **只读并发，写操作屏障**：只读工具大胆并行提高探索速度，写文件与 Shell 严格串行加锁并校验指纹，杜绝并发竞争与写坏文件。
- **无摘要换窗**：不使用 LLM 递归总结历史对话，避免总结丢失细节或产生幻觉；依托结构化的 `NOTE.md` 传承长任务进展。
