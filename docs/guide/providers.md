# 模型与 Provider 接入指南

Cade 拥有独立的 AI 适配层（`src/cade/ai/`），屏蔽各大底层服务商在 API 协议、分块流式传输、推理思考链以及工具调用格式上的异构性，原生支持前沿大模型与本地私有化部署服务。

---

## 1. 支持的 Provider 与 Transport 协议

在 `provider.connections.<provider>` 中，通过 `transport` 指定连接协议；
默认模型通过独立的 `default_provider` 与 `default_model` 选择：

| Transport 协议 | 对应适配类 | 适用平台与典型模型 |
| :--- | :--- | :--- |
| **`deepseek_chat`** | `DeepSeekProvider` | DeepSeek 官方 API（`deepseek-flash` 默认模型、`deepseek-v4-pro` 旗舰推理） |
| **`openai_responses`**| `OpenAIResponsesProvider` | OpenAI 最新 Responses 协议（`gpt-6-astra`、`gpt-5.6-luna`、`gpt-6-sol`、`gpt-6-luna`、`gpt-5.6` 系列） |
| **`openai_chat`** | `OpenAIChatProvider` | 标准 OpenAI 格式接口与兼容聚合服务 |
| **`openai_codex`** | `OpenAICodexResponsesProvider` | ChatGPT / Codex 登录会话与模型通道 |
| **`chatglm_chat`** | `ChatGLMProvider` | 智谱 AI（`glm-5.1`、`glm-5`、`glm-4.7-flash` 系列）官方 API |
| **`mimo_chat`** | `MiMoProvider` | 小米 MiMo 平台大模型接口（`mimo-v2.5-pro`、`mimo-v2.5`） |
| **`custom`** | `OpenAIChatProvider` | 任意自定义 OpenAI 兼容网关、vLLM、Ollama 本地私有服务 |

---

## 2. 默认选择与连接

账号和 API key 登录只保存凭据，默认选择独立写在 `settings.json` 或项目配置：

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "high"
}
```

DeepSeek 可设 `default_provider: "deepseek"`、`default_model: "deepseek-flash"`。
两者的认证都可保留在 `~/.cade/auth.json`，切换默认值不删除任何 provider 凭据。
内置连接不必写 transport 或 base_url；自定义连接独立定义。例如 Ollama：

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

设置 `OLLAMA_API_KEY=ollama` 作为本地无认证服务的占位值。真实凭据不写入连接配置。

## 3. 辅助模型角色

主模型来自三个默认字段。`provider.model_profiles` 仅保存辅助角色的覆盖：

```json
{
  "provider": {
    "model_profiles": {
      "subagent": {"provider": "openai-codex", "model": "gpt-6-luna", "reasoning_effort": "medium"},
      "fallback": {"provider": "deepseek", "model": "deepseek-flash", "reasoning_effort": "high"}
    }
  }
}
```

`subagent` 执行委派任务；`fallback` 用于主模型容灾；`reviewer`、`judge`、`refiner`
按服务用途选择。省略角色或字段时继承主模型，显式指定其他 provider 时解析其独立凭据。
配置备用 provider 不代表它有余额；验收应使用可用服务，并检查有效配置。

---

## 4. 思考链（Thinking）与选择语法

Cade 支持精确控制推理模型的思考深度，并提供统一的选择语法：

### 模型选择语法：`provider/model:thinking_level`

在终端中使用 `/model` 时，支持一步指定提供商、模型与思考深度：

```bash
/model gpt-6-sol                          # 切换模型，保留当前思考深度
/model deepseek/deepseek-v4-pro:high      # 指定 DeepSeek 提供商、V4 Pro 模型与 high 思考深度
/model openai/gpt-6-astra:max             # 开启 max 级别极限推理深度
/model chatglm/glm-5.1:medium             # 切换至 GLM-5.1 并设定为 medium 思考
```

支持的思考深度阶梯（`thinking_level`）：
- `off` / `none`：关闭思考流，仅输出最终代码与文本
- `minimal` / `low`：轻度思考，适合常规语法与简单工具调用
- `medium`：标准推理，适合日常功能模块实现与重构
- `high` / `xhigh` / `max`：深度思考推演，适合复杂并发架构设计与疑难死锁排查
