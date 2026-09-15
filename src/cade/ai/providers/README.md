# Cade AI Providers — 模型厂商适配器

Providers 层位于 `cade.ai` 内部，专注于解决：**如何将各大模型提供商特有的请求参数、端点差异及 SSE 流式协议，适配到统一的 Provider 抽象与 `ProviderEvent` 事件流。**

---

## 1. 核心抽象与适配架构

```
                 ModelProfileConfig / ProviderSettings
                                │
                                ▼
                   build_provider_bundle()
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
  OpenAIChatProvider                           DeepSeekProvider
  (兼容标准 OpenAI API)                        (支持 thinking/reasoning delta)
          │                                           │
          ├─────────────────┬─────────────────────────┤
          ▼                 ▼                         ▼
   ChatGLMProvider     MiMoProvider         自定义 OpenAI-compatible
```

### 核心类型与组件
- **Provider 基类 ([base.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/providers/base.py))**：定义模型交互协议与接口规范。
- **厂商适配器**：
  - `openai.py`：标准 OpenAI Chat Completions 接口适配器。
  - `deepseek.py`：DeepSeek 专用适配器，专门处理 `reasoning_content` 推理流字段及温度/参数限制。
  - `chatglm.py`：智谱 GLM 系列模型协议适配。
  - `mimo.py`：小米 MiMo 协议适配。
- **协议兼容与流水线**：
  - `_compat.py`：OpenAI 兼容协议基础通信抽象。
  - `_codec.py`：请求/响应报文与工具调用（ToolCall）双向编解码。
  - `_stream.py`：底层 SSE 字符块缓冲、行切分与 JSON 流解析。
  - `_runtime.py`：HTTP 客户端底层会话与连接池管理。
- **注册与工厂 ([registry.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/providers/registry.py))**：
  - `PROVIDER_REGISTRY`：厂商名称到 Provider 构造器的映射表。
  - `build_provider_bundle`：依据配置一键装配包含主模型与辅助模型的 `ProviderBundle`。

---

## 2. 架构不变量与设计禁忌

- **无泄露适配**：所有厂商特异性参数（如 DeepSeek reasoning 模式开关、智谱参数）只能在各自的 Provider 内部消化，不得泄露到通用 `ProviderConfig` 公共字段中（通过 `extra` 字典透传）。
- **零业务状态**：Provider 实例应当无业务状态，仅持有网络连接客户端配置与模型元信息。
- **测试环境解耦**：上层测试若需模拟 Provider，应通过标准接口构造假流，不得在生产包内留存已废弃的假桩模块。

---

## 3. 典型使用示例

```python
from cade.ai.providers import build_provider_bundle, ProviderSettings

# 装配 DeepSeek 运行时
bundle = build_provider_bundle(
    settings=ProviderSettings(
        provider="deepseek",
        model="deepseek-reasoner",
        api_key="sk-...",
    )
)

# 获取流式生成器
events = bundle.llm.stream(messages, tools=tool_specs)
```
