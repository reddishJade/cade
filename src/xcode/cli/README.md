# Xcode CLI — 命令行交互与终端界面

CLI 层是面向终端用户的核心交互层，专注于解决：**如何为开发者提供响应迅速、直观可视、支持复杂多轮会话控制与人工审批交互的终端工作台。**

---

## 1. 核心抽象与数据流

CLI 层基于 `prompt-toolkit` 驱动，单向消费底层的 `AgentHarnessEvent` 事件流，并向用户提供交互控制：

```
                    ┌─────────────────────────┐
                    │      终端用户输入        │
                    │   (/slash, 消息, @file) │
                    └────────────┬────────────┘
                                 │
                                 ▼
                     Prompt-Toolkit REPL / TUI
                                 │
            ┌────────────────────┴────────────────────┐
            ▼                                         ▼
   Slash 命令分发器                           Turn 轮次调度器
   (/plan, /fork, /undo...)                  (repl_turn_handler.py)
            │                                         │
            │ 执行会话/配置分支                       │ 驱动底层应用
            ▼                                         ▼
      SessionStore / Config                     XcodeApp.ask()
                                                      │
                                                      ▼
                                            AgentHarnessEvent 流
                                                      │
                                                      ▼
                                              终端渲染引擎
                                        (Thinking折叠 / 工具卡片 / Diff)
                                                      │
                                                      ▼ (遇需审批动作)
                                               HITL 交互确认弹窗
```

### 核心模块职责
- **REPL 主循环 ([repl.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/repl.py))**：多轮对话生命周期控制、快捷键与终端事件驱动。
- **Slash 命令体系 ([repl_commands.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/repl_commands.py))**：
  - 模式控制：`/plan`、`/build`、`/act`。
  - 会话分支与历史：`/fork`、`/resume`、`/clear`、`/undo`、`/rewind`。
  - 运行时调整：`/model`、`/effort`、`/thinking`、`/config`、`/tool`。
- **人机审批交互 ([repl_hitl.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/repl_hitl.py))**：在敏感工具调用或跨边界写操作时中断，弹出选择菜单（单次允许、拒绝、会话持久记忆）。
- **实时渲染引擎 ([repl_rendering.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/repl_rendering.py) / [tool_rendering.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/tool_rendering.py))**：流式 Markdown 渲染、思考流折叠、语法高亮与执行状态指示。
- **智能补全与辅助 ([completion.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/completion.py) / [file_refs.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/file_refs.py))**：命令自动补全、模型选项过滤与 `@path` 语法文件内容即时内联。
- **首次配置向导 ([setup_wizard.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/setup_wizard.py))**：提供引导式 Provider 与 API Key 设置流程。

---

## 2. 架构不变量与设计禁忌

- **表现层单向依赖**：CLI 属于最外层呈现层，只能调用 `harness` 与 `coding_agent` 的公开 API，严禁直接篡改底层会话文件或绕过权限引擎直接执行命令。
- **非阻塞主事件循环**：所有长时间运行的 Agent 任务必须异步执行，保证终端界面在模型生成或工具执行期间依然能够响应用户中断信号（Ctrl+C）。
- **HITL 审批安全**：审批逻辑必须严密处理终端尺寸异常、按键逃逸或非法字符输入，任何意外中断均应视为拒绝执行，不得默认自动放行。

---

## 3. 子模块分工

- **[shared/](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/shared/README.md)**：REPL 与 TUI 共享的数据累加与思考流追踪组件（`ReasoningCore`）。
- **[tui/](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/tui/README.md)**：基于 `prompt-toolkit` 构建的全屏类 IDE 终端工作台。
