# Cade AI — LLM 通信与流事件层

AI 层是 Agent 与各大模型厂商之间的**协议翻译与流归一化层**，专注于解决一个核心问题：**如何屏蔽各大模型在 API 协议、流式分块（SSE delta）、推理思考流（reasoning）以及工具调用格式上的异构性，向上层提供中立且强类型的流式事件契约。**

---

## 1. 核心抽象与数据流

本层将所有异构 LLM 的输出统一转换为强类型的 `ProviderEvent` 流，屏蔽厂商实现细节：

```
                    ┌─────────────────────────┐
                    │      Model Request      │
                    │   (Messages + Tools)    │
                    └────────────┬────────────┘
                                 │ HTTP POST / SSE
                                 ▼
                    ┌─────────────────────────┐
                    │   AI Provider Adapter   │
                    │  (OpenAI/DeepSeek/GLM)  │
                    └────────────┬────────────┘
                                 │ Stream Decoding & Normalization
                                 ▼
                     ProviderEvent 联合事件流
         ┌───────────────┬───────────────┬───────────────┐
         ▼               ▼               ▼               ▼
   ReasoningDelta    TextDelta     ToolCallEvent    FinalMessage
   (思考/推理增量)    (回答文本增量)   (工具调用与入参)   (StopReason + 完整内容)
                                         │
                                         ▼
                                    UsageUpdate
                                   (Token 用量审计)
```

### 核心类型契约
- **统一流事件 ([events.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/events.py))**：
  - `ReasoningDelta`：提取模型的内部思考/推理文本增量。
  - `TextDelta`：模型生成的最终回答文本增量。
  - `ToolCallEvent`：结构化的工具调用请求（含工具名与参数 JSON）。
  - `FinalMessage`：流终止标志，附带终止原因（`end_turn`、`tool_use`、`max_tokens` 等）。
  - `ProviderFailure`：网络故障或 HTTP 错误的结构化异常。
- **标准化配置 ([types.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/types.py))**：
  - `ProviderConfig`：标准化的 Provider 构造参数（`api_key`、`model`、`base_url`、`thinking`、`reasoning_effort` 等）。
- **模型解析与缓存 ([models.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/models.py) / [cache.py](file:///C:/Users/dwei/workspace/cade/src/cade/ai/cache.py))**：
  - 模型别名映射、上下文窗口规格解析与 Prompt Cache 状态追踪。

---

## 2. 架构不变量与设计禁忌

- **单向翻译契约**：
  - AI 层仅负责 LLM 的通信、参数编码与事件流解码。**严禁感知** Agent 的循环步数、换窗逻辑、会话持久化或具体操作系统工具。
- **思考流一等公民**：
  - 不论厂商原生接口将思考流放在单独字段（如 DeepSeek 的 `reasoning_content`）还是特定标签中，均必须归一化为 `ReasoningDelta` 发射，供 UI/CLI 实时展示。
- **无静默吞咽**：
  - 流解析中断、JSON 解析异常或网络超时必须产生显式的 `ProviderFailure` 或抛出明确异常，严禁静默丢弃 delta 导致生成内容残缺。

---

## 3. 典型装配与调用

```python
from cade.ai.providers.registry import build_provider_bundle, ProviderSettings

# 根据配置构建 Provider 运行时绑束
bundle = build_provider_bundle(
    settings=ProviderSettings(
        provider="deepseek",
        model="deepseek-reasoner",
        api_key="sk-...",
    )
)

# 直接发起流式会话并消费归一化事件
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

---

## 4. 子模块分工

- **[providers/](file:///C:/Users/dwei/workspace/cade/src/cade/ai/providers/README.md)**：各大厂商的具体协议适配实现（OpenAI、DeepSeek、ChatGLM、MiMo 等），基于公共基类与编解码器封装。
