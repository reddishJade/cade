# Cade Memory vNext 架构

Memory 是 `.cade/memory/` 内少量可编辑 Markdown 文件，保存昂贵才获得、可能再次
有用的 workspace 知识。路径用于定位，无独立 ID、索引、manifest 或全局 scope。

`Event History → 少量持久 Memory → 微型确定性 Catalog → 普通 read → History evidence → 当前代码/验证`。

- Repository/Git/Tests 判定当前事实；NOTE.md 保存任务状态；Skills 保存方法。
- `save_memory(path, markdown, sources)` 校验显式引用、限制路径、原子写入及检测冲突。
  覆盖另带通用文件前置条件 `expected_content`；正文无内容 schema 或字段 gate。
  Host 仅替换末尾保留标记内的来源 footer，完整文本更新不会重复累加 Sources。
- Host 只规范化显式来源，并补充与调用天然绑定的信息；省略的 Session ID 绑定当前
  Session。不会推断哪些测试证明了哪些结论，也不会自动收集 Git/diff/snapshot。
- History 独立提供同 workspace 内 `session_id + entry_id` 精确读取和祖先邻域，复用
  artifact/page，不切换 Session/head，不增加全局搜索。
  显式 Session 的 `around` 只接受 `before`，`after` 仅用于当前 branch。
- 精确读取不依赖导航 cache：默认 Session 目录以当前物理位置提供归属，忽略
  旧 `project_path`，移动/改名后来源仍可读取。新日志首条记录的 `project_path`
  仅为共享/外部目录提供随日志提交的 workspace 绑定。
- `.cade/memory/` 仅保存工具可写；按需通过既有 `bash + rg` 搜索、普通 read 读取。
  默认项目搜索仍排除它，不增加默认工具或可选搜索的 Memory 例外。
- 每次 provider 请求经现有 Context section 注入当前 Catalog，恢复与换窗后重新投影。
  直接子级 Markdown 按文件名排序，跳过隐藏文件、符号链接和非普通文件；目录缺失不创建。
  每个文件最多读取开头 8 KiB，仅取首个 H1 及紧随的普通段落，不追查正文中的替代摘要。
  标题最多 160 字符，提示最多 320 字符，整个 Catalog（包括标签与溢出提示）最多 4 KiB。
  无 H1 的旧文件仍以路径可见，格式或编码不可读的文件安全跳过。
- Catalog 使用 `ContextBlockSource.MEMORY`、`USER_CONTEXT` 和 HIGH 优先级，复用
  现有请求预算、trace、fingerprint 和换窗机制。条目是可能过期的历史数据，
  当前指令、项目规则和现状验证仍决定行动；转义标签不构成模型服从边界的保证。
- Catalog 无磁盘索引或内存文件缓存。稀疏文件集每请求有限读取，外部编辑及删除
  在下一请求可见，也不依赖 mtime/size 是否改变。
  SystemPromptBuilder 不包含动态 Memory；现有组装器把 Catalog 放在历史消息之前，
  Catalog 改变会使该位置之后的 provider cache 前缀失效，稳定内容不产生额外重排。
- Catalog 与 NOTE/Skills 一样属于 Host 的 Context 收集；工具权限控制正文读取及保存，
  不控制这段自动暴露的检索元数据。正文读取继续遵守既有路径边界与权限。
- Memory 读取是普通 evidence，受 ContextPolicy 约束；失效结论直接编辑文件。
- Memory 自身不发起模型调用、后台整理或额外推理。保存是普通 Agent tool call，
  后续正常 Agent loop 按运行时既有语义继续。

来源存在不证明解释正确；Git SHA 不代表完整执行现场。来源应回到当时的错误、
代码观察/修改和实际验证结果，使用前核对当前代码、配置和环境。

不做 retrieval service、向量/BM25、自动 query matching、生命周期、计数器、自动 promotion、
历史副本或来源保留策略。协议测试不替代任务收益评估。

使用说明及参数见 [Memory vNext](guide/memory.md)。
