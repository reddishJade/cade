# Xcode Harness Agent Runtime — 运行时调度与上下文管线

`agent_runtime` 是 Harness 的执行驱动中枢，专注于解决：**如何管理多轮 Agent 循环中的工具门控拦截、长程会话中的无摘要换窗滚动、外部异步取消以及多任务排队运行控制。**

---

## 1. 核心架构与驱动流水线

```
                   用户输入 / SessionRunController
                                │
                                ▼
                       AgentHarness.run()
                                │
                                ▼ (多轮迭代)
        ┌────────────────────────────────────────────────┐
        │  1. 检查 ContextWindowRollover (无摘要平滑换窗)   │
        │  2. 动态拼装 SystemPromptBuilder               │
        │  3. 调用 Provider.stream()                     │
        │  4. ToolGate 拦截与权限审查                     │
        │     - 前置 Hook / 审计                         │
        │     - PermissionEngine 判定                    │
        │     - HITL / AutoReviewer 审批                 │
        │     - 后置 Hook / 审计                         │
        │  5. 记录追加事件到 SessionRecorder              │
        └────────────────────────────────────────────────┘
```

### 核心文件与机制
- **通用运行时核心 ([harness.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/harness.py))**：`AgentHarness` 类提供完整的长程任务推进逻辑、事件流派发与错误恢复。
- **工具门控系统 ([tool_gate.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/tool_gate.py))**：充当所有工具调用的安全防火墙，统一执行参数合法性校验、权限判定、审批回调与前/后置 Hook。
- **无损换窗滚动 ([context_window.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/context_window.py))**：
  - `ContextWindowRollover`：当上下文 Token 逼近模型上限时，不采用容易造成信息失真或幻觉的递归摘要，而是直接开启全新、干净的工作窗口，仅保留关键事实，旧记录完全交给 `history` 工具按需查阅。
- **运行状态与排队控制 ([run_control.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/run_control.py))**：提供 `SessionRunController`，管理忙时消息通道（排队、立即打断或丢弃）与会话执行锁。
- **子代理运行时 ([subagents.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/subagents.py))**：管理子代理生命周期与会话拓扑。
- **取消机制 ([cancellation.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/cancellation.py))**：提供轻量协作式的 `CancellationToken`。

---

## 2. 架构不变量与设计禁忌

- **无失真换窗原则**：严禁在上下文滚动时强制注入模糊的“模型自生成摘要”，避免由于递归摘要产生的幻觉在后续轮次中级联放大。
- **工具执行必须经由 ToolGate**：任何工具执行均不得绕过 ToolGate 直接调用 `run()`，必须严格执行安全拦截与审计。

---

## 3. 子模块分工

- **[prompting/](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/prompting/README.md)**：提示词动态拼装器、工具使用规范编译与 Token 预算分配。
