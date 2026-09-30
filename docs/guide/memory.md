# Memory vNext

Memory 保存当前 workspace 中昂贵才获得、可能再次有用、可以核验的知识。
一条 Memory 是 `.cade/memory/` 内一个 Markdown 文件，文件路径用于定位。
没有用户全局层、索引、manifest、生命周期或后台整理；不同 clone/worktree 独立。

## 保存

Agent 通过普通工具调用保存任意 Markdown 正文和显式来源：

```json
{
  "path": ".cade/memory/provider-timeout.md",
  "markdown": "# Provider timeout\n\n说明观察到的症状、适用条件、根因或约束、处理结果，以及下次检查的代码或验证入口。",
  "sources": [
    {"session_id": "20260930-100000", "entry_id": "abc123def456"}
  ]
}
```

示例中的 ID 应替换为 `history` 返回的真实 ID。`sources` 可以引用错误、代码观察、
修改和验证结果；只有 Agent 显式给出的事件才成为来源。省略 `session_id` 时机械绑定
当前 Session。Host 校验并规范化引用，只有与当前调用天然绑定、无需推断的信息
才自动补充。文件尾部记录可读的来源；引用存在不能证明正文解释正确。

Host 只管理一块保留标记包围的末尾 footer：

```markdown
<!-- cade:memory:sources -->
## Sources
- session_id=... entry_id=...
<!-- /cade:memory:sources -->
```

更新时可以将读取后的完整文件作为 `markdown`；Host 用本次显式 `sources` 替换该
footer，不累加来源段。标记外的正文（包括 Agent 自己的 `Sources` 标题）不解析。
标记保留给 Host；损坏、重复或后面带正文的 footer 会报错，避免静默删除正文。

正文没有必填标题、字段或 parser。建议保留条件、原因、有效措施和核验入口，
不把普通任务总结或容易从当前代码获得的事实写入 Memory。

Memory 自身不发起任何模型调用、后台整理或额外推理。`save_memory` 是普通 Agent
工具调用；工具结果之后是否继续正常 Agent loop，由现有运行时语义决定。

覆盖已有文件时，先读取它，再通过 `expected_content` 提供完整旧正文（含来源尾注）
作为文件写入前置条件。未提供前置条件不会覆盖已有文件；内容变化时拒绝写入，
重新读取后再修订。Cade 保存调用之间使用文件锁，临时文件原子提交；外部编辑器
不参与该锁，首版不承诺与任意外部编辑器之间的事务隔离。

## 按需读取与核验

普通任务及 Session 恢复不扫描或注入 Memory。用户有历史需求，或 Agent 判断重复
排查昂贵时，先通过既有 `bash` 执行 `rg "timeout" .cade/memory`，再 `read` 具体文件。
Memory 不改变默认 tool surface，不为可选 `grep/glob/find` 增加隐藏目录例外。
默认 `rg "timeout" .` 及普通项目搜索仍排除隐藏的 Memory 目录。

来源是 History 的通用精确读取接口：

```json
{"operation":"read","session_id":"20260930-100000","entry_id":"abc123def456","offset":0,"max_chars":8000}
```

```json
{"operation":"around","session_id":"20260930-100000","entry_id":"abc123def456","before":3}
```

显式 `session_id` 时，读取同 workspace 的指定原始事件；邻域沿锚点的祖先 branch，
不返回其他后继分支；显式指定 Session 时只支持 `before`，传入 `after` 会报错。
不指定 Session 的 `around` 仍支持当前 branch 的 `before/after`。
读取复用 artifact 和分页，不切换当前 Session/head。
不指定 Session 的 search/read/around 仍操作当前 branch；不提供跨 Session 搜索。
缺失或无效的 artifact 会明确报错，预览不冒充完整证据。

精确读取不依赖导航 cache `session_index.json`。默认 `.cade/sessions/` 的目录归属
可以验证 workspace；新日志首条记录的 `project_path` 随事实一起提交，用于共享或
自定义 Session 目录的归属核验。缺少这一绑定的外部日志不能靠 cache 推断归属，
会明确拒绝读取。日志分叉保留 workspace 绑定，不新建索引或来源副本。

使用结论前检查当前代码、配置或环境，并按当前决策验证。Git SHA 可辅助定位，
不能代表当时未提交的 workspace，也不证明经验仍适用。必要的代码/diff/验证
证据留在 History；Memory 不复制历史或自动抓取快照。

读取结果是普通 evidence，ContextPolicy 可以裁剪或回收；原始读取结果仍在 Session。
旧结论变化时直接读取并修订文件，不增加 stale/supersedes 状态。用户也可用编辑器
修改或删除文件；Git 只为纳入版本控制的文件提供额外历史。

`/memory` 仅显示目录位置，不扫描文件。普通文件写入工具不能写 `.cade/memory/`，
Agent 保存必须经过 `save_memory`，并遵守当前执行模式、`security.permissions.edit`
及具体工具规则的写入权限。

## 验证边界

首版协议测试验证存储、权限、来源读取、正常路径 I/O 和 ContextPolicy 行为，
不能证明模型任务表现改善。收益评估应保持模型、任务和 History 能力一致，
覆盖重复问题、条件变化和无关任务，并计入写入、搜索、核验及从未复用的 Memory 成本。
