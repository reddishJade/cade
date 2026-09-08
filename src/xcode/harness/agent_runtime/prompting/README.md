# Xcode Harness Prompting — 动态系统提示词构建

本子包负责运行期系统提示词（System Prompt）的动态编译与拼装，专注于解决：**如何根据当前激活的工具集、工作区上下文与 Token 预算，按需组装出结构化、低开销的系统指令。**

---

## 1. 核心架构与拼装流水线

```
       PromptContext (核心身份 + 工作区根路径 + 活跃配置)
                             │
                             ▼
                   SystemPromptBuilder
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
build_tool_prompt()    build_citations()    build_runtime_context()
(工具规范与 JSON Schema)  (工作区引用规则)     (环境与时间信息)
       │                     │                     │
       └─────────────────────┼─────────────────────┘
                             ▼
                    TokenBudget 预算裁剪
                             │
                             ▼
                   最终 SystemMessage 字符串
```

### 核心文件与职责
- **动态拼装引擎 ([builder.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/prompting/builder.py))**：`SystemPromptBuilder` 与 `PromptContext`，协调静态身份与动态上下文的组合。
- **工具使用规范生成 ([tools.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/prompting/tools.py))**：`build_tool_guidelines` 根据已注册的工具动态提取用法范式。
- **引用与元数据 ([citations.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/prompting/citations.py))**：上下文文件引用与事实出处的标准化格式化。
- **Token 预算评估 ([token_budget.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/agent_runtime/prompting/token_budget.py))**：计算系统提示词所占 Token 空间，防止提示词过长挤压对话窗口。

---

## 2. 架构不变量与设计禁忌

- **按需编译原则**：未激活的工具不应将详尽指南注入提示词；提示词体积必须受到严格的预算上限约束。
- **纯文本输出**：本子包只负责生成文本字符串，不触发任何外部 IO 或网络操作。
