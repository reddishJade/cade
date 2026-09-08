# Xcode Harness Session — 会话树与可回放事实账本

`session` 模块是 Xcode 最核心的持久化系统，专注于解决：**如何将多轮对话、工具意图、换窗边界、子代理衍生以纯粹追加写（Append-only）的事件树形式无损存储，保证整个系统的完全可重放性与可审计性。**

---

## 1. 核心架构与事件树模型

```
                      Agent / User 交互事件
                               │
                               ▼
                        SessionRecorder
                               │ (原子追加写)
                               ▼
        ┌─────────────────────────────────────────────┐
        │      TreeSessionRepo (SessionStore)         │
        │                                             │
        │    Node 0 (Root)                            │
        │       │                                     │
        │    Node 1 ───► Node 2 (Branch A: Plan)      │
        │       │                                     │
        │       └──────► Node 3 (Branch B: Build)     │
        └──────────────────────┬──────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     replay_session()   SessionHistory       SessionSurface
    (事件重放还原消息)    (history 检索工具)   (投影至 UI/TUI 视口)
```

### 核心能力与组件
- **不可变事实账本 ([recorder.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/recorder.py) / [tree_store.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/tree_store.py))**：
  - `TreeSessionRepo`：会话树存储仓库，保存在本地 `.xcode/sessions/` 目录；
  - 每一个交互步骤作为不可变节点（`TreeNode`）持久化，天然支持基于任意历史节点的会话分叉（Fork）。
- **无损事件重放 ([replay.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/replay.py))**：从磁盘文件中按顺序流式重播事件，精确重构消息序列与工具调用历史。
- **无损历史工具 ([history.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/history.py))**：
  - 导出 `history` 工具供 Agent 自行调用：
    - `list_windows`：列出所有换窗重置点；
    - `search`：关键词定位过往对话片段；
    - `read`：按序号分页拉取完整记录；
    - `around`：获取指定消息的上下文邻域。
- **输入排队管道 ([inbox.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/inbox.py))**：`SessionInbox` 处理执行期间进入的并发消息（Busy message queue）。
- **视图投影 ([surface.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/harness/session/surface.py))**：将复杂的树形结构投影为终端/Web 易于呈现的线性折叠状态。

---

## 2. 架构不变量与设计禁忌

- **追加写（Append-only）与不可变性**：已持久化的事件节点严禁原地修改或覆写；任何“撤销”（undo）或“回退”（rewind）操作在底层均表达为分支切换或新增事件。
- **事实源唯一性**：会话账本是系统执行状态的最终裁判；严禁在外部维护无法从 Session 账本中重构出的隐式内部状态。
- **容灾与断电安全**：单条事件写入必须具备原子性，即使进程突然终止也不会导致现有日志文件损坏。
