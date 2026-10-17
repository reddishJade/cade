# Agent loop

The Agent layer organizes LLM streaming, tool decisions, and environment observations into a testable, interruptible loop. It uses neutral messages, events, and tool contracts rather than depending on an OS, model vendor, or product tool implementation.

## Data flow

```text
run_agent_loop
  → RequestAssembler: system prefix, history, guidelines
  → Provider stream: TextDelta, ToolCallEvent, ReasoningDelta
  → BeforeToolCall hook
  → AgentTool.run(): parallel batches or sequential barriers
  → ToolResultMessage
  → Completion, budget, or cancellation checks
  → AgentLoopResult: metrics, messages, termination reason
```

### Contracts

- [messages.py](messages.py): `SystemMessage`, `UserMessage`, `AssistantMessage`, and `ToolResultMessage`.
- [events.py](events.py): `TurnStart`, `TurnEnd`, `BeforeToolCall`, and `AfterToolCall`.
- [types.py](types.py): `AgentTool` schemas and execution declarations, plus `CancellationSignal`.
- [context.py](context.py): `ContextAssembler`, `ContextCollector`, and ordering-only `ContextPriority`. Selected blocks are injected in full. Budget policies reclaim history and evidence or fail explicitly if assembly is unsafe.

## Invariants

Agent owns algorithms and state machines. It consumes `cade.ai` model protocols and local tool protocols. Harness, products, and hosts inject services. All tool interaction uses `AgentTool`; model streams use standard async iterators.

A stable [agent_loop.py](agent_loop.py) should not need changes for new business tools, vendors, or permission policies. Scheduling follows explicit tool declarations: parallel-safe reads can run concurrently, while sequential tools form barriers. Tool exceptions become structured `ToolResultMessage` values rather than uncaught loop failures.

## Example

```python
from cade.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentLoopResult,
    UserMessage,
    run_agent_loop,
)

# 配置循环上下文和预算
config = AgentLoopConfig(provider=model_provider, max_steps=10)
context = AgentContext(
    system_prompt="You are a helpful assistant.", tools=tool_registry
)

# 运行循环并消费事件
async for event in run_agent_loop(
    [UserMessage(content="Analyze project structure")], context, config
):
    if isinstance(event, AgentLoopResult):
        print(event.termination_reason, event.metrics)
    else:
        print(event.type)
```

The loop yields events and a final `AgentLoopResult`. Callers supply context and tools explicitly. Closing the stream cancels and waits for the current loop task. Results are not stored on a separate Agent instance.
