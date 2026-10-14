# Cade Agent — 思考与执行主循环

Agent 层是 Cade 最内层的领域无关核心，专注于解决一个核心问题：**如何将 LLM 的流式推理、工具决策与环境观测组织成一个确定性、可测试、可中断的单向循环。**

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
- **中性消息体 ([messages.py](messages.py))**：[`SystemMessage`](messages.py)、[`UserMessage`](messages.py)、[`AssistantMessage`](messages.py)、[`ToolResultMessage`](messages.py)。
- **外部事件流 ([events.py](events.py))**：文本与思考增量、工具调用与结果、用量、step 结束和换窗事件；循环、Harness 与宿主消费同一套类型。
- **工具与执行契约 ([types.py](types.py))**：[`AgentTool`](types.py)（声明 schema、只读性与并发安全等级）、[`CancellationSignal`](types.py)。
- **上下文管线 ([context.py](context.py))**：[`ContextAssembler`](context.py)、[`ContextSection`](context.py) 与 `ContextPriority`（仅用于稳定排序）；请求组装器直接收集固定 section 集合，选定块完整注入，预算策略回收历史与工具证据，无法安全组装时明确失败。

---

## 2. 架构不变量与设计禁忌

- **单向无依赖法则**：
  - `agent` 拥有算法与状态机，通过 `cade.ai` 的模型协议和本层的工具协议运行；Harness、编码产品和宿主向本层注入服务。
  - 所有工具与环境交互必须通过 [`AgentTool`](types.py) 合约注入；所有模型交互必须通过标准异步迭代器注入。
- **循环闭环稳定性**：
  - 循环逻辑（[`agent_loop.py`](agent_loop.py)）一旦稳定，不应因业务层新增工具、切换模型厂商或修改权限策略而做出任何修改。
- **确定性与无副作用调度**：
  - 工具由调度器统一并发控制：只读且标记并发安全的工具并行执行；有写操作或系统副作用的工具严格串行互斥。
- **无静默异常**：
  - 工具执行异常被统一捕获为结构化的 [`ToolResultMessage`](messages.py) 回传给模型，严禁抛出未捕获异常导致主事件循环崩溃。

---

## 3. 典型装配与调用

```python
from cade.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentLoopResult,
    UserMessage,
    run_agent_loop,
)

# 配置轻量循环上下文与预算
config = AgentLoopConfig(provider=model_provider, max_steps=10)
context = AgentContext(
    system_prompt="You are a helpful assistant.", tools=tool_registry
)

# 运行主循环并消费结构化事件流
async for event in run_agent_loop(
    [UserMessage(content="Analyze project structure")], context, config
):
    if isinstance(event, AgentLoopResult):
        print(event.termination_reason, event.metrics)
    else:
        print(event.type)
```

循环直接交付事件和最后一个 `AgentLoopResult`；上下文与工具由调用方显式提供。
关闭事件流会取消并等待当前循环任务，结果不保存在额外的 Agent 实例中。
