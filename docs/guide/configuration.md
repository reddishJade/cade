# Configuration

[CadeRuntimeConfig](../../src/cade/harness/config.py) validates runtime configuration. Unknown fields, incorrect types, and invalid enum values are reported at startup. This page covers sources, runtime settings, and budgets. The model defines the complete field set. See [Models and providers](providers.md) for provider configuration.

## Configuration sources

At startup, Cade merges JSON in this order. Later explicit fields override earlier values. Nested objects merge recursively; arrays are replaced as a whole.

1. User settings: `~/.cade/settings.json`.
2. Project settings: `<PROJECT_ROOT>/cade.config.json`, replaced by the file specified with `cade --config PATH`.
3. Local workspace settings: `<PROJECT_ROOT>/.cade/settings.json`.
4. `CADE_APPROVAL_POLICY`: overrides `security.approval_policy`.

Omitted fields use model defaults. Python `build_app(runtime_config=...)` uses the supplied configuration; `agent_config=...` can override agent settings separately. API keys can also come from the environment and `.env` files, as described below.

Save the following example as `cade.config.json` to override these fields and keep other defaults:

```json
{
  "agent": {
    "reserve_tokens": 16384,
    "rollover_trigger_ratio": 0.95,
    "tool_workers": 4
  },
  "security": {
    "approval_policy": "on-request",
    "approval_router": "mode",
    "sandbox": {
      "mode": "workspace-write",
      "network_access": "deny"
    }
  },
  "execution_modes": {
    "default_mode": "act"
  }
}
```

## Runtime and budget fields

Budgets use tokens. The physical context window comes from model metadata, `provider.options.context_window`, or `provider.model_profiles.<name>.context_window`. The input budget subtracts the output reserve and safety margin from that window. Rollover uses predicted input and a reserve for new input. See the [context policy](../context-policy.md) for the algorithm.

| Field | Default | Description |
| --- | --- | --- |
| `agent.max_steps` | `null` | Optional loop step limit |
| `agent.max_llm_calls` | `null` | Optional model call limit for the main loop |
| `agent.automatic_rollover` | `true` | Enable automatic rollover and context overflow recovery |
| `agent.rollover_token_threshold` | `0` | A positive value adds an explicit rollover token limit; zero uses the window policy |
| `agent.rollover_trigger_ratio` | `0.95` | Trigger cap as a fraction of the physical window, bounded by the effective input budget |
| `agent.reserve_tokens` | `16384` | Output reserve, also sent as an output limit on supported transports |
| `agent.headroom_tokens` | `null` | Automatic margin: 2% of the physical window, capped at 8192; 1024 if the window is unknown |
| `agent.evidence_token_budget` | `null` | Stable per-tool-result admission limit: the smaller of 32000 and the effective input budget; 32000 if the window is unknown. This does not cap the combined tool history. |
| `agent.working_set_token_budget` | `null` | Automatic recent-interaction quota: the smaller of 4096 and one-quarter of the input budget; 4096 if the window is unknown; zero disables this quota |
| `agent.next_turn_input_tokens` | `1024` | Reserve for new calls and evidence, capped at one-eighth of the trigger budget |
| `agent.tool_workers` | `4` | Maximum concurrency for tool batches declared parallel |
| `agent.tool_timeout_seconds` | `120.0` | Per-tool execution timeout in seconds |
| `agent.watchdog_repeated_tool_limit` | `3` | Stop after this many consecutive identical tool call signatures |

`prompt.instructions[].priority` controls injection order only. Accepted values are `instruction` (default) and `context`; neither controls inclusion. The old values `critical`, `high`, `medium`, and `low` are no longer accepted. Use `instruction` for ordinary instructions and `context` for factual context.

## Permissions and execution environment

| Field | Default | Values or purpose |
| --- | --- | --- |
| `execution_modes.default_mode` | `act` | `plan`, `build`, or `act` |
| `security.approval_policy` | `on-request` | `on-request` requests approval as needed; `never` denies new approval requests |
| `security.approval_router` | `mode` | `mode` follows the execution mode; `user` or `auto` fixes the approval route |
| `security.sandbox.mode` | `workspace-write` | `read-only`, `workspace-write`, or `danger-full-access` |
| `security.sandbox.network_access` | `deny` | `deny` or `allow` for Linux agent shell networking |
| `skills.trust_project_skills` | `false` | Trust and discover project skills |
| `paths.sessions_dir` | `null` | Override the default project `.cade/sessions/` location |
| `paths.skills_dir` | `null` | Set a skills directory |
| `observability.audit_path` | `null` | Set an additional audit log path |

See [Modes, permissions, and execution environment](../architecture.md#modes-permissions-and-execution-environment) for Linux sandbox, structured file tool, and external service boundaries.

## Default models, connections, and credentials

Store these settings separately:

| Content | Location | Responsibility |
| --- | --- | --- |
| Default selection | `default_provider`, `default_model`, and `default_reasoning_effort` in settings | Select the provider, model, and effort for new sessions |
| Connection parameters | `provider.connections.<provider>` | Store transport, custom endpoints, and optional `api_key_env`; changing model defaults does not remove connections |
| Credentials | `~/.cade/auth.json`, keyed by provider | Keep API keys and OAuth credentials together; login does not change the default model |

For example, add these fields to `~/.cade/settings.json`, keeping other fields:

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "high"
}
```

Built-in defaults are `openai-codex` / `gpt-5.6-luna` / `high`. Built-in providers are `openai-codex` (ChatGPT login), `openai` (OpenAI API), `deepseek`, `chatglm`, `mimo`, and `custom`. OpenAI API and Codex accounts have separate provider identities. Logging in to one does not change how the other is called. Model selection precedes credential resolution. Missing credentials for the selected provider cause an explicit error, not a model change based on another account.

`cade login --method api_key` saves an API key for the selected provider. Account login through `cade login` saves OAuth credentials. Neither changes startup defaults. `cade setup` explicitly selects a default model and stores credentials separately. `cade auth status` shows authentication type and status, not available account credit.

`provider.options` stores shared request options: `context_window`, `thinking`, `clear_thinking`, `tool_stream`, and `response_format`. `provider.model_profiles` configures the auxiliary roles `subagent`, `reviewer`, `judge`, and `refiner`. Omitted roles inherit the main model; omitted fields inherit main-model options. For example:

```json
{
  "provider": {
    "model_profiles": {
      "subagent": {
        "provider": "openai-codex",
        "model": "gpt-6-luna",
        "reasoning_effort": "medium"
      }
    }
  }
}
```

API credential resolution first checks the selected provider's stored API key, then its dedicated environment variable or `.env` entry. Variables include `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `MIMO_API_KEY`, and `CHATGLM_API_KEY` (also `ZHIPUAI_API_KEY` and `BIGMODEL_API_KEY`). Custom connections can specify `api_key_env`; the default custom provider uses `API_KEY`. Cade does not reuse an OpenAI key for DeepSeek.

For each variable, lookup checks the process environment, then `.env` in the package directory, project root, and project `cade/` directory. Authentication is not injected during configuration discovery. `settings.json` does not store `api_key`, OAuth tokens, or `account_id`. The old `provider.model_profiles.main` field and connection or credential fields inside profiles are no longer accepted.

`cade exec --model provider/model --reasoning-effort high` temporarily overrides defaults before assembly. Auxiliary roles without explicit overrides inherit from the main model. Use `--transport` to select a protocol explicitly.

`/model`, `/effort`, and `/context-window` affect the current runtime. Use configuration fields to save startup defaults; switching sessions does not write global settings. `provider.options.context_window` can restrict a model to a smaller window, such as `200k`. When switching models, an inherited cap larger than the new model's window is discarded.

## Inspect and edit settings

`cade config` opens the configuration browser and writes project settings. Use `cade config --config PATH` to select the target. In the interface, `/config` edits the project's `cade.config.json` using the same field catalog. Saved settings are read at the next application assembly. Dedicated runtime commands, such as model switching, update supported capabilities during a run. Model and static permission policy replacement require an idle agent. See [Components and assembly](../architecture.md#components-and-assembly) for publication boundaries.
