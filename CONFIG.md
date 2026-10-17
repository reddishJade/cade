# Cade configuration reference

`cade.config.json` lives in the project root. `python -m cade.main` and `build_app()` read it automatically. Use `--config` to select another file. Relative paths resolve against `--project-root`.

Configuration precedence, lowest to highest: global `~/.cade/settings.json` → project `cade.config.json` → local `.cade/settings.json` → `CADE_APPROVAL_POLICY`.

Without a configuration file, built-in capabilities are enabled: `core`, `subagent`, `memory`, and `skills` when visible skills exist. Configuration layers override earlier values.

## Default model and provider

Defaults are stored separately from authentication:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `default_provider` | string | `openai-codex` | Provider for new sessions |
| `default_model` | string | `gpt-5.6-luna` | Model ID for the selected provider |
| `default_reasoning_effort` | string/null | `high` | Startup effort supported by the model |

`~/.cade/auth.json` stores API keys or OAuth credentials by provider. Both can coexist. `cade login` stores credentials without changing defaults. `cade setup` and `cade config` explicitly select defaults. Configuration discovery does not read authentication; assembly selects the provider before resolving credentials.

### Connections

`provider.connections.<provider>` stores `transport`, optional `base_url`, and `api_key_env`, not keys or models. Built-in providers need no connection settings. Custom providers must declare a transport. Built-in identities are `openai-codex`, `openai`, `deepseek`, `chatglm`, `mimo`, and `custom`. `openai` defaults to the Responses API.

### Options and model profiles

`provider.options` stores shared request options:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `context_window` | int/null | `null` | Override model window size |
| `thinking` | bool | `true` | Request reasoning |
| `clear_thinking` | bool | `false` | ChatGLM retained-reasoning setting |
| `tool_stream` | bool | `true` | Stream tool calls |
| `response_format` | object/null | `null` | Structured response format |

`provider.model_profiles` supports `subagent`, `reviewer`, `judge`, and `refiner`. Role objects can override `provider`, `model`, `reasoning_effort`, and request options. Omitted fields inherit the main model. String-valued role settings and a `main` role are not accepted. The main model uses the default fields.

Credentials first use the selected provider's stored authentication, then its dedicated environment variable or `.env`. Keys are not borrowed from other providers. See the [configuration guide](docs/guide/configuration.md) for complete examples.

#### DeepSeek

- Default `base_url`: `https://api.deepseek.com`.
- Built-in models: `deepseek-flash` (DeepSeek-V4.1-Flash) and `deepseek-v4-pro` (DeepSeek-V4-Pro).
- Retired IDs: `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are still accepted by the API but normalized to `deepseek-flash` by Cade.
- Thinking is enabled by default through `extra_body={"thinking": {"type": "enabled"}}`.
- `reasoning_effort` defaults to `"high"`; complex agent requests use `"max"` automatically.
- Cache statistics use native `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`.

#### MiMo

- Default `base_url`: `https://api.xiaomimimo.com/v1`.
- Thinking defaults to on for mimo-v2.5-pro/mimo-v2.5 and off for mimo-v2-flash.
- Cache statistics use `prompt_tokens_details.cached_tokens`.

#### ChatGLM

- Default `base_url`: `https://open.bigmodel.cn/api/paas/v4/`.
- `tool_stream` is supported only by `glm-4.6` and `glm-4.7`.
- Cache statistics use `prompt_tokens_details.cached_tokens`.

### Runtime model switching

`/model` opens the authenticated model picker. Explicit provider prefixes distinguish API and account access:

```text
/model openai-codex/gpt-5.6-luna:high
/model deepseek/deepseek-flash:high
```

`/effort high` changes the current main-model effort. `/model` does not save startup defaults; use `cade config` or edit settings. `cade exec --model provider/model` temporarily overrides selection before assembly without writing settings. Auxiliary roles without explicit overrides inherit the main model.

### Interactive configuration

`/config` (REPL/TUI) and `cade config` (CLI) open the same settings browser. It includes default provider/model/effort, execution mode, approval policy, and shell. Edit other fields directly in `cade.config.json`, including agent tuning, request hygiene, paths, security details, hooks, and prompts. `cade login` manages authentication. Connections and role settings remain separate.

```text
> Default Provider       openai-codex
  Default Model          gpt-5.6-luna
  Default Reasoning Effort high
  Default Mode           act
  Approval Policy        asks for review
  Non-Workspace Access   on
  Shell                  auto

  'asks for review': always waits for explicit user approval.
```

Approval Policy choices map as follows:

- **always proceeds** → `never`.
- **agent decides** → `on-request` with `approval_router=auto`.
- **asks for review** → `on-request` with `approval_router=user`.

The highlighted setting has a description below the list. Enum choices explain their tradeoffs. Press Enter to edit; the menu marks the current value with `(current)`. Press Esc to return. `CadeRuntimeConfig` validates values before saving. REPL supports direct selection with commands such as `/config shell`.

```text
/config                         # 打开设置浏览器
/config default mode            # 跳转到 Default Mode
cade config                     # 从 CLI 打开同一浏览器
```

## Agent

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `max_steps` | optional positive int | unset | Maximum loop steps per task; unlimited by default |
| `rollover_message_threshold` | int | `0` | Optional message-count threshold; zero adds no limit |
| `rollover_token_threshold` | int | `0` | Optional absolute token threshold; zero adds no limit |
| `automatic_rollover` | bool | `true` | Roll over automatically according to the current model window |
| `fallback_recent_messages` | int | `10` | Message fallback when the current user turn cannot be found |
| `fallback_recent_tokens` | int | `20000` | Token fallback when the current user turn cannot be found |
| `reserve_tokens` | int | `16384` | Reserved tokens for model output and tool interaction |
| `rollover_trigger_ratio` | float | `0.95` | Automatic rollover ratio relative to the model window |
| `tool_workers` | int | `4` | Active tools per parallel batch; values below one run as one |
| `tool_timeout_seconds` | float | `120.0` | Per-tool timeout |
| `watchdog_repeated_tool_limit` | int | `3` | Consecutive repeated-tool threshold |

See [Runtime and budget fields](docs/guide/configuration.md#runtime-and-budget-fields) for current budget details.

## Execution modes

Modes are switchable permission profiles on the same agent. New sessions use `execution_modes.default_mode` (`act` by default). Recovery restores the saved mode. Runtime switching preserves session context.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `default_mode` | string | `act` | `plan`, `build`, or `act` for new sessions; recovered sessions use their saved mode |

| Mode | Tool visibility | Built-in rules and fallback |
| --- | --- | --- |
| `plan` | Exploration with `read` and `bash`, plus restricted `write` and `edit` | Confirmed read-only shell runs directly; analyzer-confirmed mutations are denied; unresolved shell goes to automatic review; structured writes are limited to plan files; fallback is `deny` |
| `build` | Normal coding tools | Structured reads/writes and confirmed read-only shell run directly; unresolved or mutating shell goes to automatic review; fallback is `ask` |
| `act` | Normal coding tools | Reads and confirmed read-only shell run directly; structured writes and unresolved or mutating shell require user approval; fallback is `ask` |

Each mode accepts a `rules` array. User rules override built-in defaults. See the [architecture](docs/architecture.md#modes-permissions-and-execution-environment) for decision ordering and constraints. Fallback is not stored as a catch-all `*` rule.

```json
{
  "execution_modes": {
    "default_mode": "build",
    "build": {
      "rules": [
        {"action": "bash", "effect": "ask", "command": "git", "subcommand": "push"},
        {"action": "write", "effect": "deny", "resource_pattern": "secrets/**"}
      ]
    },
    "act": {
      "rules": [
        {"action": "bash", "effect": "allow", "command": "git", "subcommand_in": ["status", "diff"]}
      ]
    }
  }
}
```

### Rule fields

| Field | Type | Description |
| --- | --- | --- |
| `action` | string | Required tool name or wildcard, such as `bash`, `write`, or `*` |
| `effect` | string | Required: `allow`, `ask`, or `deny` |
| `command` | string/null | Main shell command; wildcards supported |
| `subcommand` | string/null | Exact shell subcommand |
| `subcommand_in` | string[]/null | Match any listed subcommand |
| `flags_any` | string[]/null | Require at least one listed flag |
| `flags_all` | string[]/null | Require all listed flags |
| `resource_pattern` | string/null | Target path pattern for nonshell actions; additional resource constraint for shell |

Structured conditions do not match if corresponding command information is missing. Short flags are sorted and normalized, so `-rf` and `-fr` are equivalent. Compound shell commands currently extract structured fields from the first segment only; configure rules conservatively.

## Request hygiene

These settings control model-facing history compression without changing complete disk history.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | Enable compression |
| `max_tool_result_bytes` | int | `8000` | Maximum tool-result bytes |
| `max_tool_arg_length` | int | `1000` | Maximum argument string length for completed calls |
| `keep_head_lines` | int | `50` | Retained leading tool-result lines |
| `keep_tail_lines` | int | `50` | Retained trailing tool-result lines |

## Paths

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `sessions_dir` | string/null | `null` | Session directory; CLI defaults to `.cade/sessions` |
| `skills_dir` | string/null | `null` | Highest-priority skill directory; relative to the project root |

Fixed local paths: `.cade/session_index.json`, `.cade/session_artifacts/`, `.cade/mcp_cache.json`, and `.cade/mcp_config.json`.

## Observability

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `audit_path` | string/null | `null` | Audit log path |

## Hooks

`hooks.entries` declares trusted external command hooks. Each configuration layer replaces the entire lower-priority array. Commands must be argv arrays. They do not run through a shell and do not support in-process Python callbacks.

Commands run in event order with redacted `HookRecord` JSON on stdin. They can return one JSON object on stdout; empty stdout means `{}`. `pre_tool` accepts only `decision` (`allow`, `deny`, or `ask`) and `arguments` (a complete object). Rewritten arguments pass schema validation and PermissionEngine again. Hooks cannot relax existing denial. `/hooks` shows sources, enabled status, run counts, and recent errors.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `event` | string | required | `pre_tool`, `post_tool`, `on_error`, `on_context_window_reset`, `before_agent_start`, or `before_provider_request` |
| `command` | string[] | required | Nonempty argv array |
| `matcher` | string/null | `null` | Optional event matching expression |
| `timeout` | number | `10.0` | Positive timeout in seconds |
| `enabled` | bool | `true` | Enable this hook |
| `failure_policy` | string | `"warn"` | `ignore`, `warn`, or `fail` |
| `inherit_to_subagents` | bool | `false` | Explicitly propagate to subagents |

```json
{
  "hooks": {
    "entries": [
      {
        "event": "pre_tool",
        "matcher": "bash",
        "command": ["python", "hooks/check_shell.py"],
        "timeout": 5,
        "failure_policy": "fail",
        "inherit_to_subagents": false
      }
    ]
  }
}
```

## Security

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `approval_policy` | string | `"on-request"` | `on-request` handles rule-generated `ask`; `never` denies requests without an existing grant |
| `approval_router` | string | `"mode"` | `mode` uses automatic review in Plan/Build and user review in Act; `user` and `auto` force a route |
| `non_workspace_access` | bool | `true` | When false, deny all outside-workspace access regardless of directory allowlists |
| `auto_review_timeout_seconds` | number | `90` | Total automatic review deadline, greater than zero and at most 300 seconds |
| `sandbox` | object | see below | Linux Agent Shell filesystem and network isolation |
| `restricted_dirs` | array | `[]` | Denied directories |
| `permissions` | object | `{}` | Decisions for groups: `read`, `edit`, `shell`, `web`, `subagent`, `skill` |
| `tools` | object | `{}` | Tool-specific decisions overriding group expansion |
| `global_default` | string/null | `null` | Default static decision: `allow`, `ask`, or `deny` |
| `external_directories` | array | `[]` | Entries with required `path` and optional `access`, default `read`; values: `read`, `write`, `read_write` |
| `sensitive_path_overrides` | array | `[]` | Exact sensitive-path exceptions; no wildcards |

### Linux shell sandbox

```json
{
  "security": {
    "sandbox": {
      "mode": "workspace-write",
      "network_access": "deny"
    }
  }
}
```

| Field | Values | Default | Description |
| --- | --- | --- | --- |
| `sandbox.mode` | `workspace-write`, `read-only`, `danger-full-access` | `workspace-write` | Project write boundary; `danger-full-access` permits host writes with current-user permissions |
| `sandbox.network_access` | `deny`, `allow` | `deny` | `deny` creates a separate network namespace; `allow` retains host networking |

`workspace-write` mounts the host root read-only and permits writes to the project, `/tmp`, and existing external directories with `write` or `read_write` access. Existing project `.git`, `.agents`, and `.cade` paths are remounted read-only. Missing names get read-only placeholders for the command's duration to prevent initial creation. Known credential directories, key files, and `.env*` files without an applicable read override are masked.

Linux defaults require `bwrap` on `PATH` and fail closed if it is missing. Only `danger-full-access` combined with `allow` bypasses bubblewrap entirely. Other combinations retain filesystem or network boundaries. Non-Linux platforms retain host shell behavior.

### Static permissions

The `edit` group covers `write`, `edit`, `patch`, and `save_memory`. Memory saving cannot bypass disabled writes.

```json
{
  "security": {
    "permissions": {
      "read": "allow",
      "edit": "ask",
      "shell": "ask"
    },
    "tools": {
      "webfetch": "deny"
    },
    "global_default": "ask"
  }
}
```

Groups expand to tools first, followed by tool-specific overrides. Unmatched static rules use `global_default`; unmatched mode rules use the mode fallback. Path boundaries, mode policy, static policy, shell parseability, and mode rules share a resolver. Stricter results take precedence.

An internal approval request does not necessarily ask the user. Plan and Build route to the independent `reviewer` profile by default; Act routes to the user. Reviewer requests include execution mode authority. `approval_policy=never` prohibits new approval requests. Only `on-request` and `never` are accepted; no legacy aliases are migrated.

Automatic review receives a bounded transcript with roles and trust labels plus complete action arguments. It has no tools and cannot modify local state. The output contract is `outcome / risk_level / user_authorization / rationale`. Low-risk and bounded reversible medium-risk actions can pass automatically. High-risk actions require at least medium authorization and explicit scope. Critical risk is denied.

Review grants one execution, tries at most three times, and shares a 90-second deadline. Timeouts, provider errors, and invalid output fail closed. Review cannot override restricted directories, path boundaries, dangerous commands, or explicit denial.

An approval notice includes risk, authorization, and rationale:

```text
Automatic approval review approved (risk: low, authorization: high):
Running the specified local focused tests is routine validation for the authorized implementation. It does not involve networking, sensitive data, or destructive actions.
```

```json
{
  "provider": {
    "model_profiles": {
      "reviewer": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high"}
    }
  },
  "security": {
    "approval_policy": "on-request",
    "auto_review_timeout_seconds": 90
  }
}
```

Permission prompts, automatic review, and shell effect analysis decide whether calls are authorized; they are not OS sandboxing. Bubblewrap separately enforces Linux Agent Shell filesystem and network boundaries. Confirmed read-only shell commands can run directly. Unparsed or potentially mutating commands use the mode's reviewer. Review cannot override dangerous commands or hard boundaries.

### External directories

```json
{"path": "../reference", "access": "read"}
{"path": "../shared/templates", "access": "read_write"}
```

- `read`: reading only.
- `write`: writing only.
- `read_write`: reading and writing.
- Sensitive `.env`, `.env.*`, `.git`, and credential paths remain protected in external directories.
- `.env.example` can be read but not written.

## Tools

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `subagent_extra_tools` | string[] | `[]` | Additional main-agent tools available to subagents; `todowrite` is not inherited by default |
| `shell` | string | `"auto"` | `auto`, `pwsh`, `powershell`, `cmd`, `bash`, `zsh`, `sh`, or `fish` |

`todowrite` is a default main-agent session tool. It replaces the complete list, with at most one `in_progress` item. The list persists in the transcript and `RunState` and is reinjected in dynamic context after rollover. Subagents share it only when `todowrite` appears in `subagent_extra_tools`.

Input uses a `todos` array. Each item requires `id`, `content`, and `status`, with optional `priority` (`high`, `medium`, `low`). Status values are `pending`, `in_progress`, `completed`, and `cancelled`. Unlike the top-level TypeScript reference implementation, Cade requires IDs for recovery and event correlation.

## Skills

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `trust_project_skills` | bool | `false` | Trust and expose project `.cade/skills/` and `.agents/skills/`; otherwise discover only user skills |

Without visible skills, `load_skill` is not registered and no empty catalog is injected. Discovery uses first-wins ordering:

1. Explicit `paths.skills_dir` or `build_app(skills_dir=...)`.
2. Project `.cade/skills/`.
3. Project `.agents/skills/`.
4. User `~/.cade/skills/`.
5. User `~/.agents/skills/`.

Fixed project directories require `trust_project_skills`. An explicit directory indicates caller trust. A missing explicit directory produces a warning.

First activation returns the skill root, body, and relative metadata for `scripts/`, `references/`, and `assets/`, without reading or executing resources. Repeated activation in one session returns a short status. Activation recovers from history and survives context compression.

### Tool registration

Product tools register as `read`, `write`, `edit`, `patch`, `glob`, `find`, `ls`, `grep`, `websearch`, `webfetch`, `question`, and `bash`. Runtime capabilities add `delegate`, `save_memory`, `history`, and `rollover`. Skills add `load_skill` when discovered. MCP tools register complete schemas at startup through discovery or valid cache; persistent connections open on first call. Restart after changing MCP configuration.

Build/Act do not expose `grep`, `glob`, `find`, and `ls` to models by default. Normal search uses native commands such as `rg`, `fd`, and `git` through `bash`.

`websearch` uses Exa by default or Parallel MCP. Parameters include `query`, `numResults`, `type` (`auto`, `fast`, `deep`), `livecrawl` (`fallback`, `preferred`), and `timeout`. Environment controls include `EXA_API_KEY`, `PARALLEL_API_KEY`, and `OPENCODE_EXPERIMENTAL_PARALLEL`. `webfetch` supports `markdown`, `text`, and `html`, decompresses gzip/deflate, reads at most 5 MB, and marks truncation.

Memory entries are editable Markdown in `.cade/memory/`. A bounded catalog exposes retrieval metadata; the agent searches explicitly, reads content and History sources, and checks current code. `save_memory` accepts `path`, `markdown`, and explicit `sources`; updates require complete old `expected_content`. The host validates references and replaces the source footer without interpreting conclusions.

History `read`/`around` retrieves original workspace records by `session_id + entry_id` without switching session/head or relying on navigation caches. Explicit-session `around` accepts only `before`. Default local session storage establishes ownership by physical location, permitting workspace moves without changing old `project_path`. Custom/shared directories validate persisted workspace binding. See [Memory](docs/guide/memory.md).

MCP schema caches record configuration hash, negotiated protocol version, and server identity. Older caches without negotiation metadata trigger rediscovery.

## Prompt

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `modules` | array | seven modules | Modules used to assemble the prompt |
| `instructions` | array | `[]` | Instruction sources |

Default order: `identity`, `tool_discipline`, `citations`, `tools`, `search_strategy`, `environment`, `cwd`.

Identity, tool discipline, citations, and search strategy are fixed at assembly. Environment information is captured in that assembly; directory entries are read per request. Tool guidance uses enabled tools; API schemas declare names, descriptions, and parameters. Prefix boundary markers remain. Harness injects session notices at request boundaries. File and tool history comes from session messages.

### Instruction format

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `type` | string | yes | `file` or `inline` |
| `path` | string | file only | Project-relative path; absolute paths, `~`, and `..` traversal are prohibited |
| `content` | string | inline only | Instruction text |
| `priority` | string | no | `instruction` (default) or `context`; controls ordering only |

```json
{"type": "file", "path": "AGENTS.md", "priority": "instruction"}
{"type": "inline", "content": "No external dependencies without approval.", "priority": "instruction"}
```

Without configured instructions, collection falls back to `AGENTS.md`. With explicit sources, configured sources precede fallback files. Paths are deduplicated with configured sources taking precedence.

All instructions share a 32 KB UTF-8 budget, accumulated in source order. If a source exceeds the remainder, its prefix is retained and later sources are omitted. Collection does not parse headings, distinguish languages, or remove code blocks.

## Cache optimization

### Statistics

1. Prefer DeepSeek's native `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`.
2. Fall back to `prompt_tokens_details.cached_tokens` for OpenAI, ChatGLM, and MiMo.
3. Compute hit rate as `hit / (hit + miss)`.

### Stable tools

1. Sort tools alphabetically by `name`.
2. Sort schema keys recursively with `sort_keys=True`.
3. Use the first 16 characters of a SHA256 fingerprint.

See `src/cade/ai/cache.py` for cache statistics.

### Token efficiency

Keep stable cacheable prefixes, a replaceable working window, bounded tool output, progressive tool discovery, model-aware rollover, and repeat suppression.

`ContextWindowRollover` in `src/cade/harness/agent_runtime/context_window.py`:

- Closes the old working window without generating a summary.
- Retains startup context, activated skills, and the current turn.
- Trims stale reads and large tool output in the surface according to budget.
- Preserves the original transcript as the complete factual source for `history` retrieval.
- Uses project `NOTE.md` as explicit handoff state.

`/compact` uses the same rollover mechanism and retains the latest working turn. `/rollover` starts a window without ordinary conversation and requires a nonempty `NOTE.md` by default. Use `/rollover --force` to bypass that check.

`RepeatDetector` in `src/cade/agent/watchdog.py` detects repetition with file-change awareness and clears read-only call history after changes.
