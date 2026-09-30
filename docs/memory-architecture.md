# Cade Memory vNext 架构

Memory 是 `.cade/memory/` 内少量可编辑 Markdown 文件，保存昂贵才获得、可能再次
有用的 workspace 知识。路径用于定位，无独立 ID、索引、manifest 或全局 scope。

`Event History → 少量持久 Memory → 普通 search/read → 原始 evidence → 当前代码/验证`。

- Repository/Git/Tests 判定当前事实；NOTE.md 保存任务状态；Skills 保存方法。
- `save_memory(path, markdown, sources)` 校验显式引用、限制路径、原子写入及检测冲突。
  覆盖另带通用文件前置条件 `expected_content`；正文无内容 schema 或字段 gate。
- Host 只规范化显式来源，并补充与调用天然绑定的信息；省略的 Session ID 绑定当前
  Session。不会推断哪些测试证明了哪些结论，也不会自动收集 Git/diff/snapshot。
- History 独立提供同 workspace 内 `session_id + entry_id` 精确读取和祖先邻域，复用
  artifact/page，不切换 Session/head，不增加全局搜索。
- `.cade/memory/` 仅保存工具可写，普通 read/search 可显式读取。默认项目搜索排除它，
  其他 `.cade/**` 和隐藏目录保护保持有效。
- 正常任务和恢复不读取、扫描或注入 Memory。固定能力指引和工具 schema 是常量成本。
- Memory 读取是普通 evidence，受 ContextPolicy 约束；失效结论直接编辑文件。
- Memory 自身不发起模型调用、后台整理或额外推理。保存是普通 Agent tool call，
  后续正常 Agent loop 按运行时既有语义继续。

来源存在不证明解释正确；Git SHA 不代表完整执行现场。来源应回到当时的错误、
代码观察/修改和实际验证结果，使用前核对当前代码、配置和环境。

不做 retrieval service、向量/BM25、逐轮 hint、生命周期、计数器、自动 promotion、
历史副本或来源保留策略。协议测试不替代任务收益评估。

使用说明及参数见 [Memory vNext](guide/memory.md)。
