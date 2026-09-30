# Cade Harness Memory — 长期事实与可复用经验

本目录负责跨会话的长期记忆管理，专注于解决：**如何在长周期、多会话的研发过程中，低成本沉淀并按需检索跨会话的架构决策、用户偏好与昂贵获得的可复用经验，同时防止噪声注入导致 Token 膨胀。**

---

## 1. 核心架构

```
    [项目级记忆]                      [用户级记忆]
  ./MEMORY.md                      ~/.cade/memory/MEMORY.md
(规则、决策、经验)                 (跨项目偏好)
        │                                 │
        └────────────────┬────────────────┘
                         ▼
        MemoryManager (BM25 索引 + 读取期消费门槛)
                         │
     ┌───────────────────┼────────────────────┐
     ▼                   ▼                    ▼
 recall 工具        remember 工具       memory_hints section
(索引 → memory_id)  (证据约束写入)      (锚点命中的一次性指针)
```

### 核心设计原则

1. **长期记忆 vs 短期状态**：`MEMORY.md` 只沉淀规则、架构决策、已验证事实与可复用经验；当前进度由项目根 `NOTE.md` 记录；历史事实的唯一来源是 session 追加写账本。
2. **记忆低于仓库权威**：记忆是历史提示，文件/测试/git 是当前事实；经验自带证据指针与内容快照新鲜度。
3. **两段式渐进披露**：`recall` 返回索引行，`recall memory_id=<id>` 才返回正文；每轮最多注入 3 行指针，每条经验每个窗口最多自动提示一次。
4. **证据由 Host 盖章**：`remember` 只接受当前分支上真实成功的验证事件，并盖章 `validation`/`verify`/`exit_code`/`session`/`anchor_state`；没有事件就拒绝写入。
5. **校验即消费门槛**：声明为 `type: experience` 但不满足约定的记录（包括手工编辑写入的）不会产生提示、不会返回正文、不会被注入恢复概览。
6. **没有隐藏推理**：本模块不调用任何模型（无 judge、reranker、embedding、摘要、后台整理），也不保存 per-record 的置信度、效用、计数或生命周期状态。

### 核心文件与职责

- **经验视图 ([experience.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/experience.py))**：Experience/Anchor 类型、结构校验、路径与错误锚点匹配、内容快照新鲜度、索引与指针渲染。
- **记忆管理 ([manager.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/manager.py))**：BM25 索引、anchor 过滤、memory_id 精确读取、原子写入与统一写入门 `memory_write_rejection`。
- **条目解析 ([parsing.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/parsing.py))**：把 H2 节与 `key: value` 字段行解析为 `MemoryRecord`。
- **工具生成 ([tools.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/tools.py))**：`recall`（两段式）与 `remember`（证据约束写入）。
- **稀疏提示 ([hints.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/hints.py))**：确定性触发、窗口内一次性、LOW 优先级指针块与 `MemoryHintState`。

---

## 2. 架构不变量与设计禁忌

- **纯文本透明化**：记忆数据完全以人类可读可编辑的 Markdown 存在。
- **只读并发安全**：`recall` 与提示收集不得产生写副作用。
- **经验只写项目层**：仓库相对锚点在别的项目里没有意义。
- **不重新引入**：embedding/向量库、LLM judge/reranker、hidden provider inference、后台或 session-end recap、自动晋升、效用与成功/失败计数、置信度状态机、手工多因子排序、每轮 top-k 注入。
