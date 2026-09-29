# 模型与 Provider 接入指南

Cade 拥有独立的 AI 适配层（`src/cade/ai/`），屏蔽各大底层服务商在 API 协议、分块流式传输、推理思考链以及工具调用格式上的异构性，原生支持前沿大模型与本地私有化部署服务。

---

## 1. 支持的 Provider 与 Transport 协议

在配置中，通过 `transport` 字段指定通信协议类型：

| Transport 协议 | 对应适配类 | 适用平台与典型模型 |
| :--- | :--- | :--- |
| **`deepseek_chat`** | `DeepSeekProvider` | DeepSeek 官方 API（`deepseek-flash` 默认模型、`deepseek-v4-pro` 旗舰推理） |
| **`openai_responses`**| `OpenAIResponsesProvider` | OpenAI 最新 Responses 协议（`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-5.6` 系列） |
| **`openai_chat`** | `OpenAIChatProvider` | 标准 OpenAI 格式接口与兼容聚合服务 |
| **`openai_codex`** | `OpenAICodexResponsesProvider` | ChatGPT / Codex 登录会话与模型通道 |
| **`chatglm_chat`** | `ChatGLMProvider` | 智谱 AI（`glm-5.1`、`glm-5`、`glm-4.7-flash` 系列）官方 API |
| **`mimo_chat`** | `MiMoProvider` | 小米 MiMo 平台大模型接口（`mimo-v2.5-pro`、`mimo-v2.5`） |
| **`custom`** | `OpenAIChatProvider` | 任意自定义 OpenAI 兼容网关、vLLM、Ollama 本地私有服务 |

---

## 2. 典型配置示例 (`cade.config.json` 或 `~/.cade/settings.json`)

### 示例 A：配置 DeepSeek（官方 API）

```json
{
  "provider": {
    "model_profiles": {
      "main": {
        "transport": "deepseek_chat",
        "chat_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-your-deepseek-key",
        "thinking": true,
        "reasoning_effort": "high"
      }
    }
  }
}
```

### 示例 B：配置 OpenAI GPT-6

```json
{
  "provider": {
    "model_profiles": {
      "main": {
        "transport": "openai_responses",
        "chat_model": "gpt-6-sol",
        "api_key": "sk-your-openai-key",
        "thinking": true,
        "reasoning_effort": "medium"
      }
    }
  }
}
```

### 示例 C：配置本地 Ollama 或 vLLM 私有模型

无需公网网络，完全本地私密运行：

```json
{
  "provider": {
    "model_profiles": {
      "main": {
        "transport": "custom",
        "chat_model": "qwen2.5-coder:32b",
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "context_window": 32768
      }
    }
  }
}
```

---

## 3. 多 Profile 分工：主模型、子代理与容灾回退

Cade 支持定义独立的模型 Profile，分担不同的工作负荷以优化成本与速度：

1. **`main`（主模型）**：
   负责与人类直接交互并驱动主事件循环，通常选用推理能力最强、支持思考链的模型（如 `deepseek-v4-pro` 或 `gpt-6-sol`）。
2. **`subagent`（子代理模型）**：
   负责执行拆分出去的孤立子任务（如单纯检索大目录、执行局部静态语法检查）。可配置速度极快的小模型（如 `deepseek-flash`、`gpt-6-luna` 或 `glm-4.7-flash`）。
3. **`fallback`（容灾回退模型）**：
   当主模型遭遇网络故障、服务商限流（Rate Limit）或 5xx 错误时，系统会自动切至回退模型重试，保障开发流程不中断。

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
