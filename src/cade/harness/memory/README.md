# Cade Harness Memory — 长期事实与可复用经验

本目录负责跨会话的长期记忆管理，专注于解决：**如何在长周期、多会话的研发过程中，低成本沉淀并按需检索跨会话的架构决策、用户偏好与昂贵获得的可复用经验，同时防止噪声注入导致 Token 膨胀。**

---

## 1. 核心架构与记忆分层

```
    [项目级记忆]                      [用户级记忆]
  ./MEMORY.md                      ~/.cade/memory/MEMORY.md
(项目规则、架构决策、经验)          (全局个人偏好、编码习惯)
        │                                 │
        └────────────────┬────────────────┘
                         ▼
                MemoryManager (BM25 索引 + 写入校验)
                         │
        ┌────────────────┼─────────────────────┐
        ▼                ▼                     ▼
  recall 工具       remember 工具        memory_hints section
 (显式检索/锚点)   (唯一经验写入入口)   (锚点命中的稀疏指针)
```

### 核心设计原则

1. **长期记忆 vs 短期状态**：
   - `MEMORY.md` 只沉淀规则、架构决策、已验证事实与可复用经验；
   - 当前进度与下一步动作由项目根 `NOTE.md` 记录；
   - 历史事件的精确事实源是 session 的追加写事件账本。
2. **记忆低于仓库权威**：记忆是历史提示，文件/git/测试是当前事实；经验自带 `evidence` 指针与现算的 `state`。
3. **低频按需检索**：普通回合不注入检索结果；只有显式 `recall`、resume 概览，以及锚点确定性命中时的少量指针块。
4. **没有隐藏推理**：本模块不调用任何模型（无 judge、reranker、embedding、摘要或后台整理），也不保存 per-record 的置信度、效用、计数或生命周期状态。

### 核心文件与职责

- **经验视图 ([experience.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/experience.py))**：把声明为 `type: experience` 的记录解释为 `Experience`，负责校验、锚点匹配、git 新鲜度与单行指针渲染。
- **记忆管理 ([manager.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/manager.py))**：`MemoryManager` 维护 BM25 索引，管理项目/用户两层，提供 anchor 过滤、原子写入与统一写入门 `memory_write_rejection`。
- **条目解析 ([parsing.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/parsing.py))**：把 Markdown 的 H2 节与 `key: value` 字段行解析为 `MemoryRecord`。
- **工具生成 ([tools.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/tools.py))**：构建只读检索工具 `recall` 与经验写入工具 `remember`。
- **稀疏提示 ([hints.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/hints.py))**：`MemoryHintCollector` 只在锚点确定性命中时产出低优先级指针块。

---

## 2. 架构不变量与设计禁忌

- **纯文本透明化**：记忆数据完全以人类可读可编辑的 Markdown 存在。
- **只读并发安全**：`recall` 与提示收集不得产生写副作用。
- **校验即消费门槛**：未通过确定性校验的经验不会被召回，也不会产生提示。
- **不重新引入**：embedding/向量库、LLM judge/reranker、hidden provider inference、后台或 session-end recap、自动晋升、效用与成功/失败计数、置信度状态机、手工多因子排序、每轮 top-k 注入。
