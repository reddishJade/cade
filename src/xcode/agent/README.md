# Xcode Agent — 思考与执行主循环

Agent 层是 Xcode 最内层的领域无关核心，专注于解决一个核心问题：**如何将 LLM 的流式推理、工具决策与环境观测组织成一个确定性、可测试、可中断的单向循环。**

---

## 1. 核心抽象与数据流

本层将循环参与者解耦为中性消息、事件流与工具契约，完全不绑定具体操作系统、特定 LLM 厂商或业务工具实现：

```
                    ┌─────────────────────────┐
                    │      run_agent_loop     │
                    └───────────┬─────────────┘
                                │ 1. 组装上下文
                                ▼
                       RequestAssembler
                                │ (SystemMessage + History + Guidelines)
                                ▼
   Provider (Stream) ◄─── Model Request
         │
         │ 2. 流式返回 TextDelta / ToolCallEvent / ReasoningDelta
         ▼
  BeforeToolCall Hook
         │
         │ 3. 调度工具执行 (并发安全只读 / 串行副作用互斥)
         ▼
    AgentTool.run() ───► ToolResultMessage
         │
         │ 4. 判定终止条件 (FinalMessage / 预算耗尽 / 取消信号)
         ▼
  AgentLoopResult (metrics, messages, termination_reason)
```

### 核心类型契约
- **中性消息体 ([messages.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py))**：[`SystemMessage`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py)、[`UserMessage`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py)、[`AssistantMessage`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py)、[`ToolResultMessage`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py)。
- **外部事件流 ([events.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/events.py))**：`TurnStart`、`TurnEnd`、`BeforeToolCall`、`AfterToolCall` 等。
- **工具与执行契约 ([types.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/types.py))**：[`AgentTool`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/types.py)（声明 schema、只读性与并发安全等级）、[`CancellationSignal`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/types.py)。
- **上下文管线 ([context.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/context.py))**：[`ContextAssembler`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/context.py)、[`ContextCollector`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/context.py) 与 [`trim_to_budget`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/context.py)（根据优先级裁剪 Token 预算）。

---

## 2. 架构不变量与设计禁忌

- **单向无依赖法则**：
  - `agent` 是纯算法与状态机，**严禁导入** `xcode.harness`、`xcode.coding_agent`、`xcode.cli` 或 `xcode.server` 的任何类型。
  - 所有工具与环境交互必须通过 [`AgentTool`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/types.py) 合约注入；所有模型交互必须通过标准异步迭代器注入。
- **循环闭环稳定性**：
  - 循环逻辑（[`agent_loop.py`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/agent_loop.py)）一旦稳定，不应因业务层新增工具、切换模型厂商或修改权限策略而做出任何修改。
- **确定性与无副作用调度**：
  - 工具由调度器统一并发控制：只读且标记并发安全的工具并行执行；有写操作或系统副作用的工具严格串行互斥。
- **无静默异常**：
  - 工具执行异常被统一捕获为结构化的 [`ToolResultMessage`](file:///C:/Users/dwei/workspace/xcode/src/xcode/agent/messages.py) 回传给模型，严禁抛出未捕获异常导致主事件循环崩溃。

---

## 3. 典型装配与调用

```python
from xcode.agent import (
    AgentContext,
    AgentLoopConfig,
    SystemMessage,
    UserMessage,
    run_agent_loop,
)

# 配置轻量循环上下文与预算
config = AgentLoopConfig(max_turns=10, token_budget=100_000)
context = AgentContext(system_prompt=SystemMessage("You are a helpful assistant."))

# 运行主循环并消费结构化事件流
async for event in run_agent_loop(
    context=context,
    user_message=UserMessage("Analyze project structure"),
    provider=model_provider,
    tools=tool_registry,
    config=config,
):
    print(event)
```
