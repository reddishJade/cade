# Cade Harness Memory

本目录管理透明、可编辑的跨会话 Markdown 知识：项目规则、架构决策、个人偏好、
验证过的事实，以及昂贵调查得到的 coding experience。

| 层 | 事实载体 | 职责 |
| --- | --- | --- |
| 项目 Memory | `./MEMORY.md` | 项目 durable knowledge 和 Experience |
| 用户 Memory | `~/.cade/memory/MEMORY.md` | 跨项目个人偏好与约束 |
| 工作状态 | `./NOTE.md` | 当前任务 frontier/checkpoint |
| Session/Event History | append-only session tree | 完整对话和工具事实 |

## 数据流

正常任务验证根因与修复 → 可见普通文件写入 → H2 Markdown record → 显式
`recall` → 普通工具结果 → ContextPolicy 证据预算 → 使用前检查当前代码。

- [manager.py](manager.py)：两个 scope、确定性 BM25、文件签名/index invalidation，
  CLI CRUD 使用文件锁和原子更新。普通文件工具保留自己的写入语义。
- [parsing.py](parsing.py)：H2 记录和最小 Experience 正文约定；不扩展 MemoryRecord。
- [tools.py](tools.py)：只读 recall，没有写入或 provider 副作用。

Experience 使用 `Type: experience` 和六项非空正文标签：Problem、Root cause、
Fix pattern、Applies when、Anchors、Evidence。只有原始 query 命中完整 literal
anchor 才参与排序。字段完整不代表真实或适用；当前文件、git 和测试优先。

正常 turn 不自动 top-k 注入。resume 在独立预算中加载普通 durable records，
排除 Experience；已经存在于原始历史的经验仍按既有历史恢复。完整 recall 输出
保存在事实历史，请求中的预览由共享 ContextPolicy 控制。

不创建 episodic store、candidate 目录、状态机、使用计数、embedding、reranker、
后台提取或 consolidation；不自动把经验晋升为 Skill。

详见 [设计审查](../../../../docs/experience-memory-design.md)、
[架构](../../../../docs/memory-architecture.md) 和
[使用指南](../../../../docs/guide/memory.md)。
