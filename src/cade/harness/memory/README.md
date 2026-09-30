# Cade Memory

仅提供 `save_memory`：将 Agent 给出的 Markdown 和显式 History 来源保存到
workspace 的 `.cade/memory/*.md`。Host 负责安全路径、引用解析、原子提交和内容冲突
检测；正文由 Agent 表达，不解析经验字段。
Host 仅替换保留标记内的末尾来源 footer；完整文本修订不会累加来源段。

既有 `bash + rg` 和 `read` 承担搜索和读取，History 独立承担跨 Session 精确读取，ContextPolicy
管理读取的 evidence。创建工具时不扫描、创建或读取 Memory 目录；正常任务和恢复
不加载 Memory。工具自身不调用模型，后续普通 Agent loop 遵循既有运行时语义。

详细边界见 [Memory 架构](../../../../docs/memory-architecture.md)。
