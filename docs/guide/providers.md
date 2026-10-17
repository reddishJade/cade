# Models and providers

The AI adapter layer in `src/cade/ai/` handles differences in API protocols, streaming chunks, reasoning content, and tool call formats. It supports hosted providers and local services.

## Providers and transports

Set the connection protocol with `provider.connections.<provider>.transport`. Select the default model separately with `default_provider` and `default_model`.

| Transport | Adapter | Platforms and models |
| --- | --- | --- |
| `deepseek_chat` | `DeepSeekProvider` | DeepSeek API: `deepseek-flash` by default, or `deepseek-v4-pro` |
| `openai_responses` | `OpenAIResponsesProvider` | OpenAI Responses API: `gpt-6-astra`, `gpt-5.6-luna`, `gpt-6-sol`, `gpt-6-luna`, and `gpt-5.6` families |
| `openai_chat` | `OpenAIChatProvider` | OpenAI Chat Completions and compatible services |
| `openai_codex` | `OpenAICodexResponsesProvider` | ChatGPT/Codex account sessions and models |
| `chatglm_chat` | `ChatGLMProvider` | Zhipu AI: `glm-5.1`, `glm-5`, and `glm-4.7-flash` families |
| `mimo_chat` | `MiMoProvider` | Xiaomi MiMo: `mimo-v2.5-pro` and `mimo-v2.5` |
| `custom` | `OpenAIChatProvider` | Custom OpenAI-compatible gateways, vLLM, and local Ollama services |

## Defaults and connections

Account login and API key login save credentials only. Store default selections independently in `settings.json` or project configuration:

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "high"
}
```

For DeepSeek, use `default_provider: "deepseek"` and `default_model: "deepseek-flash"`. Credentials for both providers can coexist in `~/.cade/auth.json`. Changing defaults does not delete credentials.

Built-in connections do not require a transport or base URL. Define custom connections separately. For example, to use Ollama:

```json
{
  "default_provider": "ollama",
  "default_model": "qwen2.5-coder:32b",
  "default_reasoning_effort": null,
  "provider": {
    "connections": {
      "ollama": {
        "transport": "custom",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY"
      }
    },
    "options": {"thinking": false}
  }
}
```

Set `OLLAMA_API_KEY=ollama` as a placeholder for a local service without authentication. Do not store real credentials in connection configuration.

## Auxiliary model roles

The main model uses the three default fields. `provider.model_profiles` stores only auxiliary role overrides:

```json
{
  "provider": {
    "model_profiles": {
      "subagent": {"provider": "openai-codex", "model": "gpt-6-luna", "reasoning_effort": "medium"}
    }
  }
}
```

The `subagent` role runs delegated tasks. The `reviewer`, `judge`, and `refiner` roles serve their respective services. Omitted roles and fields inherit from the main model. An explicitly different provider uses its own credentials. Cade does not switch to another model on request failure; use `/model` to change models explicitly.

## Reasoning effort and selection syntax

Use `/model` with `provider/model:thinking_level` to select a provider, model, and reasoning effort:

```bash
/model gpt-6-sol                         # 切换模型，保留当前思考深度
/model deepseek/deepseek-v4-pro:high     # 选择 DeepSeek V4 Pro 和 high 深度
/model openai/gpt-6-astra:max            # 选择 max 深度
/model chatglm/glm-5.1:medium            # 选择 GLM-5.1 和 medium 深度
```

Reasoning levels depend on model support:

- `off` / `none`: disable the reasoning stream and return final text only.
- `minimal` / `low`: light reasoning for routine syntax and simple tool calls.
- `medium`: standard reasoning for everyday implementation and refactoring.
- `high` / `xhigh` / `max`: deeper reasoning for complex architecture and difficult debugging.
