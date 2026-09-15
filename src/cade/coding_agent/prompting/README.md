# Cade Coding Agent Prompting — 编码角色与系统提示词

本目录维护面向软件工程场景的系统级提示词，专注于解决：**如何明确定义 Agent 作为资深软件工程师的角色边界、思考习惯与工程防御规范。**

---

## 1. 核心定义与版本

- **核心身份规范 ([identity.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/prompting/identity.py))**：
  - `CORE_IDENTITY`：声明 Agent 是专注于软件开发的代码工程师，明确遵循：
    - **Read-before-edit**：编辑前必须先读取目标代码并校验 SHA256 指纹。
    - **最小局部修改**：优先使用精确定位替换，严禁非必要的大段重写。
    - **验证闭环**：修改后通过构建、静态检查或测试命令主动验证修改结果。
  - `PROMPT_VERSION`：提示词版本常量，用于驱动 Prompt Cache 失效判定与长程任务基准对比。

---

## 2. 架构不变量与设计禁忌

- **无污染原则**：本目录只允许包含静态系统身份定义，严禁在此硬编码动态上下文（如当前时间、工作区文件列表、具体工具指南等，这些必须由 `harness.agent_runtime.prompting` 动态拼装）。
