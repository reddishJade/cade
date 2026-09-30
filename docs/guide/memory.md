# 长期记忆（Memory）

Cade 将跨会话知识保存为可检查、可编辑的 Markdown。当前任务进度和下一步
属于根目录 `NOTE.md`；完整对话和工具事实属于 append-only Session/Event History。

| Scope | 文件 | 内容 |
| --- | --- | --- |
| project | `<项目根目录>/MEMORY.md` | 项目规则、带理由的架构决策、验证过的跨会话事实和昂贵调查经验 |
| user | `~/.cade/memory/MEMORY.md` | 跨项目个人偏好与约束 |

H2 标题开始一条记录。同一知识保留一个 authoritative copy。

## 读取与写入

正常 turn 不自动加载 top-k。Agent 在具体历史约束或重复调查可能有帮助时显式
调用只读 `recall`；检索是本地确定性 BM25，没有 embedding 或 provider 调用。
resume 首轮在独立预算中加载普通 durable records，Experience 始终按需读取。
所有内容仍受整体 ContextPolicy 输入和工具证据预算约束。

用户可直接编辑文件或使用 CLI：

```text
/memory list [all|project|user]
/memory search <query>
/memory add [project|user] <title> | <durable note>
/memory update [project|user] <title> | <durable note>
/memory delete [project|user] <title>
```

CLI 条目 CRUD 带文件锁并原子更新；外部编辑会使检索索引失效。Agent 没有专用
memory_write，复用普通 write/edit/patch：沿现有权限、执行模式和 diff 呈现路径，
不会获得额外外部目录写权限。一次独立工具写入通常还需要后续可见模型轮次，
没有后台提取、recap 或 consolidation。

## Experience 的正文约定

只记录昂贵调查后验证过、能指导其他任务的经验，保留因果和适用边界。
不要复制 transcript、代码变更总结、目录地图或当前待办。

```markdown
## 具体问题的可复用经验
Type: experience
Problem: 问题类别和可识别症状。
Root cause: 验证得到的真正根因。
Fix pattern: 已验证有效的处理原则，保留必要的因果细节。
Applies when: 当前实现必须满足的条件，以及不适用的边界。
Anchors: src/example.py; affected_symbol; exact error signature
Evidence: commit:<真实 SHA>; test:<实际命令和结果>
```

也可使用真实 session/event 或 tool-call 指针；不要编造来源 ID。当前 `history`
工具只查询绑定 session 的 branch，跨 session 指针暂不能直接通过它读取。

使用无 bullet 的标签，正文可续行，Anchors 单行且用分号分隔 literal。
只有标签完整、非空、不重复，并且 recall 的原始 query 含完整 anchor 时，经验
才参与排序；scope 补词不能绕过这一限制。优先 `limit=1`。宽泛词重叠、字段齐全
和检索 score 都不能证明质量或当前适用性。

经验是历史线索。采用之前读当前分支文件、检查 symbol 和适用条件，必要时
重跑针对性验证；当前代码、git 和测试优先。结果被预算裁剪时，先读取完整记录。
不匹配就拒绝；失效记录显式更新或删除。没有 confidence、TTL、自动 stale
状态、使用计数或自动 Skill promotion。

设计和历史依据见 [Experience 设计审查](../experience-memory-design.md)。
