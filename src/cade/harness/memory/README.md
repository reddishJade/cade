# Cade Harness Memory — 长期事实与分层记忆系统

本目录负责跨会话的长期事实记忆管理，专注于解决：**如何在长周期、多会话的研发过程中，低成本沉淀并按需检索跨会话的架构决策与用户偏好，同时防止无脑盲注导致 Token 膨胀。**

---

## 1. 核心架构与记忆分层

Cade 建立了清晰的记忆层级体系，严格区分长期稳定事实与短期执行状态：

```
    [项目级记忆]                      [用户级记忆]
  ./MEMORY.md                      ~/.cade/memory/
(项目架构、技术栈规范)             (全局个人偏好、编码习惯)
        │                                 │
        └────────────────┬────────────────┘
                         ▼
                MemoryManager (BM25 索引)
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
build_memory_block()             search_memory 工具
(仅在 Resume/Rebuild 时注入)      (Agent 运行期只读按需检索)
```

### 核心设计原则
1. **长期记忆 vs 短期状态**：
   - `MEMORY.md` 仅沉淀经过验证的架构决策、用户规则与稳定事实；
   - 正在执行的当前进度与下一步动作必须由项目根目录的 `NOTE.md` 记录；
   - 历史事件的唯一精确事实源是 `session` 的追加写事件账本。
2. **低频按需检索**：
   - 运行时**不会在每轮交互中自动盲注检索结果**；仅在显式调用 `search_memory` 工具，或在会话重建（resume/rebuild）时才按独立 Token 预算注入关键上下文。

### 核心文件与职责
- **记忆管理 ([manager.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/manager.py))**：`MemoryManager` 维护基于 BM25 的本地倒排索引，管理项目层（`MemoryLayer.PROJECT`）与用户层（`MemoryLayer.USER`）的隔离。
- **条目解析 ([parsing.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/parsing.py))**：将 Markdown 文档中的标题、列表与元数据解析为结构化的 `MemoryRecord`。
- **工具生成 ([tools.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/tools.py))**：构建只读、并发安全的 `search_memory` 工具。

---

## 2. 架构不变量与设计禁忌

- **纯文本透明化**：记忆数据完全以人类可读可编辑的 Markdown 文件存在，严禁使用专有二进制格式阻碍用户审查。
- **只读并发安全**：`search_memory` 工具必须声明为只读且并发安全，不得产生写操作副作用。
