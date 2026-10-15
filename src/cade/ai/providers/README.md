# Provider adapters

Providers translate vendor-specific request parameters, endpoints, and SSE streams into the common provider protocol and `ProviderEvent` stream.

## Assembly

```text
ModelProfileConfig / ProviderSettings
  → build_provider_bundle()
  → OpenAIChatProvider / DeepSeekProvider / ChatGLMProvider / MiMoProvider
  → Standard OpenAI-compatible or vendor-specific requests
```

### Components

- [base.py](base.py): model interaction protocols and base interfaces.
- `openai.py`: OpenAI Chat Completions.
- `deepseek.py`: DeepSeek reasoning content and parameter restrictions.
- `chatglm.py`: Zhipu GLM protocols.
- `mimo.py`: Xiaomi MiMo protocols.
- `_compat.py`: shared OpenAI-compatible communication.
- `_codec.py`: message and tool encoding.
- `_stream.py`: stream decoding and event normalization.
- `_runtime.py`: retries, rate limiting, and API error classification.
- [registry.py](registry.py): `PROVIDER_REGISTRY` constructor mapping and `build_provider_bundle` for primary and auxiliary models.

## Invariants

Keep vendor-specific parameters inside adapters, passing them through `extra` rather than adding vendor fields to generic configuration. Providers own client configuration and model metadata, not product state. Upper-layer tests should use protocol-compatible streams; do not retain obsolete test stubs in production packages.

## Example

Set `DEEPSEEK_API_KEY`. Pass message and tool-definition lists as `messages` and `tool_specs`, then consume `events` inside an async function.

```python
import os

from cade.ai.providers.registry import (
    ModelProfileConfig,
    ProviderSettings,
    build_provider_bundle,
)

# 装配 DeepSeek provider
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

# 获取事件流
events = bundle.llm.stream(messages, tools=tool_specs)
```
