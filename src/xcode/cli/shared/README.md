# Xcode CLI Shared — 终端共享表现逻辑

本目录存放 REPL 与 TUI 终端交互界面共享的纯计算与状态跟踪组件，专注于解决：**如何将流式思考与推理文本的增量累加、耗时统计与安全截断逻辑从具体的终端 UI 库中解耦。**

---

## 1. 核心抽象与能力

- **推理过程容器 ([thinking.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/cli/shared/thinking.py))**：
  - `ReasoningCore`：纯状态机，负责累加 `ReasoningDelta` 增量、高精度计时、计算累计耗时并生成预览截断，完全不绑定 Rich 或 Prompt-toolkit。
  - `format_elapsed`：将秒级浮点数转化为精炼的人类可读时间标签（ms、s、m、h）。
  - `should_print_reasoning_summary`：根据思考总时长与文本长度，判定是否值得为用户打印折叠摘要。
  - `reasoning_preview_lines` / `single_line_preview`：根据当前终端视口动态切分并截取最后数行作为实时预览。

---

## 2. 架构不变量与设计禁忌

- **零终端库绑定**：本目录严禁导入 `prompt_toolkit` 或直接向 `sys.stdout` 输出内容，保持纯计算函数的确定性。
- **轻量无开销**：增量追加操作必须保证 O(1) 均摊复杂度，避免在超长思考流场景下造成终端卡顿。
