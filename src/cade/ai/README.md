# AI protocols and streaming events

The AI layer translates vendor protocols and normalizes streaming chunks, reasoning, and tool calls into a typed `ProviderEvent` stream for Agent.

## Data flow

```text
Model request: messages and tools
  → HTTP POST / SSE
  → Provider adapter: OpenAI, DeepSeek, ChatGLM, MiMo
  → Stream decoding and normalization
  → ReasoningDelta / TextDelta / ToolCallEvent / FinalMessage
  → UsageUpdate
```

### Contracts

[events.py](events.py) defines:

- `ReasoningDelta`: reasoning text increments.
- `TextDelta`: answer text increments.
- `ToolCallEvent`: tool names and JSON arguments.
- `FinalMessage`: termination with reasons such as `end_turn`, `tool_use`, or `max_tokens`.
- `ProviderFailure`: structured network or HTTP failures.

[types.py](types.py) defines `ProviderConfig`, including `api_key`, `model`, `base_url`, `thinking`, and `reasoning_effort`. [models.py](models.py) resolves aliases and window sizes; [cache.py](cache.py) tracks prompt cache statistics.

## Invariants

AI owns communication, parameter encoding, and stream decoding. It must not depend on Agent steps, rollover, session persistence, or OS tools.

Normalize vendor reasoning fields or tags to `ReasoningDelta` for presentation. Parsing failures and timeouts must produce an explicit failure or exception, not silently drop content.

## Example

Set `DEEPSEEK_API_KEY` before running this example. `messages` and `tools` are the request's message and tool-definition lists. Consume the stream inside an async function.

```python
import os

from cade.ai.events import FinalMessage, ReasoningDelta, TextDelta, ToolCallEvent
from cade.ai.providers.registry import (
    ModelProfileConfig,
    ProviderSettings,
    build_provider_bundle,
)

# 根据模型配置构建 provider 组合
bundle = build_provider_bundle(
    ProviderSettings(
        env_files=(),
        model_profiles={
            "main": ModelProfileConfig(
                provider="deepseek",
                transport="deepseek_chat",
                chat_model="deepseek-reasoner",
                api_key=os.environ["DEEPSEEK_API_KEY"],
            )
        },
    )
)

# 消费归一化事件
async for event in bundle.llm.stream(messages, tools=tools):
    match event:
        case ReasoningDelta(chunk=thought):
            print(f"[Thinking] {thought}", end="")
        case TextDelta(chunk=text):
            print(text, end="")
        case ToolCallEvent(calls=calls):
            print(f"[Tool Call] {calls}")
        case FinalMessage(stop_reason=reason):
            print(f"\n[Finished: {reason}]")
```

## Submodules

- [providers/](providers/README.md): vendor adapters using shared base classes and codecs.
- [auth/](auth/README.md): authentication contracts, OAuth and refresh, and pre-request credential resolution. Hosts implement `CredentialStore`; AI does not know authentication file paths or CLI interactions.
