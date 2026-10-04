# Cade Memory

`save_memory` 将 Agent 给出的 Markdown 和显式 History 来源保存到
workspace 的 `.cade/memory/*.md`。Host 负责安全路径、引用解析、原子提交和内容冲突
检测；正文由 Agent 表达，不解析经验字段。
Host 仅替换保留标记内的末尾来源 footer；完整文本修订不会累加来源段。

既有 `bash + rg` 和 `read` 承担搜索和读取，History 独立承担跨 Session 精确读取，ContextPolicy
管理读取的 evidence。`MemoryCatalogCollector` 在每次请求中注入有界的路径、H1 和
紧随 H1 的普通段落，正文仍按需读取。Catalog 是动态 USER_CONTEXT 投影，无独立
存储或索引；不调用模型、不增加工具。目录缺失时返回空上下文，也不创建目录。

详细边界见 [Memory 架构](../../../../docs/memory-architecture.md)。
