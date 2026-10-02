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

## Provider 与凭据

`provider.model_profiles` 包含 `main`、`subagent` 和 `fallback`。配置发现时，子模型
与备用模型继承 main 中的字段，可用对象覆盖部分字段，或用模型名称字符串只覆盖
`chat_model`。默认 main 使用 `openai_chat`、`deepseek-flash` 和
`https://api.deepseek.com`；登录凭据与显式配置共同决定最终 profile。

配置发现阶段会检查已保存的 OpenAI 登录凭据。main 指向 Codex/OpenAI 账户调用
且符合凭据优先条件时，登录凭据可覆盖 API 配置；显式第三方 transport、自定义
非 OpenAI 地址或非 Codex 模型保留其 API 配置。判定实现为
[`_auth_preferred_over_api()`](../../src/cade/harness/config.py)。

API key 解析依次查找 profile 中的 `api_key`、`<PROFILE>_API_KEY`、transport 专用
变量、`OPENAI_API_KEY` 和 `API_KEY`。专用变量包括 DeepSeek 的
`DEEPSEEK_API_KEY`、MiMo 的 `MIMO_API_KEY`，以及 ChatGLM 的 `CHATGLM_API_KEY`、
`ZHIPUAI_API_KEY`、`BIGMODEL_API_KEY`。同名变量优先使用当前进程环境，再依次查找
包目录 `.env`、项目根 `.env`、项目 `cade/.env`；Python 调用可显式提供 `env_files`。

## 查看与修改

`cade config` 打开配置浏览器并写入项目配置；`cade config --config PATH` 指定
写入目标。界面中的 `/config` 编辑项目 `cade.config.json`，两者使用同一个字段
目录。保存后的配置在下一次装配应用时读取；运行期间可通过模型切换等专用入口
更新能力，模型和静态权限策略替换要求 agent 空闲，发布边界见
[组件与装配](../architecture.md#组件与装配)。
