# 配置

运行时配置由 [CadeRuntimeConfig](../../src/cade/harness/config.py) 校验，未知字段、
错误类型和非法枚举值会在启动时报告。本文列出配置来源、运行与预算字段；完整字段
以该模型为准，模型接入见 [Provider 指南](providers.md)。

## 配置来源

启动时按以下顺序合并 JSON，后读入的显式字段覆盖先前值，嵌套对象递归合并，
数组整体替换：

1. 用户配置：`~/.cade/settings.json`。
2. 项目配置：`<项目根>/cade.config.json`；`cade --config PATH` 指定文件时替代此项。
3. 工作区本地配置：`<项目根>/.cade/settings.json`。
4. `CADE_APPROVAL_POLICY`：覆盖 `security.approval_policy`。

未提供的字段使用模型默认值。Python `build_app(runtime_config=...)` 使用传入配置，
`agent_config=...` 可单独覆盖 agent 配置。API key 还可从环境和 `.env` 读取，其
解析顺序见下文。

以下片段可保存为 `cade.config.json`，其余字段保留默认值：

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

## 运行与预算字段

预算单位为 token。物理窗口来自模型元数据或
`provider.options.context_window` 或角色的
`provider.model_profiles.<name>.context_window`，输入预算在物理窗口中扣除输出预留
与额外余量；换窗使用预测输入和新增输入预留。算法见 [上下文策略](../context-policy.md)。

| 字段 | 默认值 | 含义 |
|---|---|---|
| `agent.max_steps` | `null` | 可选的循环 step 上限 |
| `agent.max_llm_calls` | `null` | 可选的主循环模型调用上限 |
| `agent.automatic_rollover` | `true` | 启用自动换窗与上下文超限恢复 |
| `agent.rollover_token_threshold` | `0` | 正值增加显式换窗 token 上限，零沿用窗口策略 |
| `agent.rollover_trigger_ratio` | `0.95` | 以物理窗口比例提供触发上限，并受有效输入预算约束 |
| `agent.reserve_tokens` | `16384` | 模型输出预留；支持输出上限的 transport 同时将其下发 |
| `agent.headroom_tokens` | `null` | 自动余量为物理窗口的 2%，最多 8192；窗口未知时为 1024 |
| `agent.evidence_token_budget` | `null` | 自动工具证据配额为 32000 与有效输入预算中的较小值；窗口未知时为 32000 |
| `agent.working_set_token_budget` | `null` | 自动近期交互配额为 4096 与输入预算四分之一中的较小值；窗口未知时为 4096，设零可关闭此配额 |
| `agent.next_turn_input_tokens` | `1024` | 新增调用与证据预留，上限为触发预算的八分之一 |
| `agent.tool_workers` | `4` | 声明为 parallel 的工具批次的最大并发数 |
| `agent.tool_timeout_seconds` | `120.0` | 单个工具执行超时秒数 |
| `agent.watchdog_repeated_tool_limit` | `3` | 连续相同工具调用签名达到该次数后终止 |

## 权限与执行环境字段

| 字段 | 默认值 | 可选值或用途 |
|---|---|---|
| `execution_modes.default_mode` | `act` | `plan`、`build`、`act` |
| `security.approval_policy` | `on-request` | `on-request` 按需审批；`never` 拒绝新增审批请求 |
| `security.approval_router` | `mode` | `mode` 跟随模式；`user` 或 `auto` 固定审批路由 |
| `security.sandbox.mode` | `workspace-write` | `read-only`、`workspace-write`、`danger-full-access` |
| `security.sandbox.network_access` | `deny` | `deny`、`allow`，控制 Linux Agent Shell 网络 |
| `skills.trust_project_skills` | `false` | 是否信任并发现项目技能 |
| `paths.sessions_dir` | `null` | 覆盖默认项目 `.cade/sessions/` 存储位置 |
| `paths.skills_dir` | `null` | 指定技能目录 |
| `observability.audit_path` | `null` | 指定附加审计日志路径 |

Linux sandbox、结构化文件工具和外部服务的执行边界见
[架构说明](../architecture.md#模式权限与执行环境)。

## 默认模型、连接与凭据

三类设置分别保存：

| 内容 | 位置 | 职责 |
|---|---|---|
| 默认选择 | settings 的 `default_provider`、`default_model`、`default_reasoning_effort` | 决定新会话使用哪个 provider、模型和 effort |
| 连接参数 | `provider.connections.<provider>` | 保存 transport、自定义端点和可选 `api_key_env`，切换默认模型不删除连接 |
| 认证凭据 | `~/.cade/auth.json`，按 provider 保存 | API key 与 OAuth 可以同时存在，登录不修改默认模型 |

例如，把下面字段写入 `~/.cade/settings.json`，其余字段保留：

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "high"
}
```

内置默认是 `openai-codex` / `gpt-5.6-luna` / `high`。内置 provider 包括
`openai-codex`（ChatGPT 登录）、`openai`（OpenAI API）、`deepseek`、`chatglm`、
`mimo` 和 `custom`。OpenAI API 和 Codex 账户使用独立的 provider 身份；登录其中
一个不会改变另一个的调用方式。模型选择先于凭据解析；所选 provider 缺少凭据时
明确报错，不因另一个账号存在而改模型。

`cade login --method api_key` 保存所选 provider 的 API key；`cade login` 的账号
登录保存 OAuth。两者不修改启动默认值。`cade setup` 会明确选择默认模型，并把凭据
单独写到认证存储。`cade auth status` 显示认证类型和状态，不能证明服务余额充足。

`provider.options` 保存共享请求选项：`context_window`、`thinking`、
`clear_thinking`、`tool_stream`、`response_format`。辅助角色由
`provider.model_profiles` 配置，支持 `subagent`、`fallback`、`reviewer`、`judge`、
`refiner`；不写的角色继承主模型，角色对象中未提供的字段继承主模型选项。例如：

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

API 凭据先查所选 provider 在 auth 中保存的 API key，再查 provider 专用环境变量
或 `.env`。变量包括 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`MIMO_API_KEY`、
`CHATGLM_API_KEY`（也接受 `ZHIPUAI_API_KEY`、`BIGMODEL_API_KEY`）。自定义连接可
指定 `api_key_env`；默认 custom 使用 `API_KEY`。不把 OpenAI key 借给 DeepSeek。
同名变量先查进程环境，再查包目录 `.env`、项目根 `.env`、项目 `cade/.env`。
认证不再在配置发现阶段注入；`settings.json` 不保存 `api_key`、OAuth token 或
`account_id`。旧的 `provider.model_profiles.main` 与 profile 内的连接、凭据字段
不再接受。

`cade exec --model provider/model --reasoning-effort high` 临时覆盖默认选择，
在装配前生效；未单独配置的辅助角色随主模型继承。`--transport` 可显式选择协议。
`/model`、`/effort` 修改当前运行，保存启动默认值使用配置字段；会话切换不写全局配置。

## 查看与修改

`cade config` 打开配置浏览器并写入项目配置；`cade config --config PATH` 指定
写入目标。界面中的 `/config` 编辑项目 `cade.config.json`，两者使用同一个字段
目录。保存后的配置在下一次装配应用时读取；运行期间可通过模型切换等专用入口
更新能力，模型和静态权限策略替换要求 agent 空闲，发布边界见
[组件与装配](../architecture.md#组件与装配)。
