# Experience Memory v0：设计审查

审查基线：2026-09-30，`491d57a4`。已 fetch origin，main、dev 与本地 HEAD
一致。本文先记录当前实现与历史，再给出最小增量；验证结果单独记录。

实现与实验已完成：保守入口可用，本轮真实 coding 对照没有证明收益。
详见 [验证与实验结果](experience-memory-evaluation.md)。

## 当前能力与边界

| 层 | 当前实现 | 对 Experience 的意义 |
| --- | --- | --- |
| 当前代码 | 文件、git、普通读写工具 | 当前事实高于历史经验 |
| 事实历史 | `SessionRecorder` → append-only JSONL tree → branch/surface replay；大输出外置 artifact | 不复制 trajectory、daily log 或 episodic store |
| 历史工具 | `SessionHistory` 的 list_windows/search/read/around，只读当前绑定 session 的 branch | 旧 session 指针可保存，但工具尚不能直接跨 session 查询 |
| 工作状态 | 根目录 `NOTE.md`；NotesCollector 每次请求读前 4 KiB；换窗事件保留恢复快照 | frontier/checkpoint 不进入 Memory |
| 请求策略 | `DefaultRequestAssembler` / `ContextPolicy` 统一输出预留、输入、证据和换窗预算 | recall 结果沿普通工具证据路径准入 |
| 持久记忆 | `./MEMORY.md`、`~/.cade/memory/MEMORY.md`；H2 为记录；确定性 BM25 | 保留两个 scope 和 Markdown |
| 技能 | description 索引、显式 load_skill、reference 懒加载、activation 恢复 | 稳定执行方法，不自动从一次经验晋升 |

`MemoryRecord` 只有 block/title/body/memory_id/layer/score。ID 从层和标题派生，
不是内容版本。BM25 附加词重叠和 exact match，scope 只是附加检索词；任何词
重叠都可能返回结果，不能把 score 当质量、概率或 validity。搜索索引通过
inode/mtime/size 失效。CLI `/memory add/update/delete` 使用 filelock 和原子替换。
正常 turn 没有自动 top-k；resume 首轮按文件顺序在 6000 tokens 内加载记录，
之后还受整体 ContextPolicy 约束。换窗不做递归摘要；当前版本会保留受保护状态
和预算内最近完整工具交互，并非绝对删除所有 assistant/tool 消息。

Agent 只有正式只读 `recall`，没有 memory_write。它可以用普通 write/edit/patch
修改项目 MEMORY.md；这些操作经过普通路径权限、mode gate、审计与 diff 呈现。
Build 默认允许结构化项目写入；Act 默认要求审批；Plan 只允许计划文件和 NOTE，
不允许写 MEMORY。用户文件位于 workspace 外：`.cade` 默认只有只读外部授权，
写入必须满足现有外部目录授权，Memory 不新增权限例外。CLI 是用户直接操作，
不会通过 Agent 工具审批。普通文件写入也不会自动获得 MemoryManager 的锁和
条目校验；不能声称所有写入都由 MemoryManager 管理。

真实 eval 暴露了另一个接线缺陷：CodingAgentHarness 创建 ExecutionModeState
时没有传入配置的 approval_router，Build 因此忽略强制人工审批配置。这会导致
实验产生额外 reviewer 请求。这属于独立基础缺陷，不计入 Memory 进展，已通过
[`a36af79f`](https://github.com/reddishJade/cade/commit/a36af79f) 单独修复并进入
dev/main。E2E 用真实待审批工具确认仅调用显式人工回调，eval 启动时也检查该
回调，首轮诊断试跑不进入正式 A/B。

系统提示仍引用旧名 search_memory，这是一个实际可修复的小缺口。
`parsing.py` 仅剥离 `- Evidence:` 等旧 bullet metadata；新约定采用无 bullet 的
`Evidence:`，保留正文和 provenance，不恢复旧字段。

## 历史考古

以下提交均检查了变更范围、接口和相关 diff；旧代码当时位于 `src/xcode/`。

| 提交 | 当时引入或移除的机制 |
| --- | --- |
| 3e2de7c | 只有 TODO 设计清单，标题虽为 feat，并没有生产代码实现 |
| 050511e | 稳定 ID、类型推断、结构化 evidence 和旧 metadata migration |
| 2f69f86 | 检索 gate、使用时间与 exposure tracking，统一显式搜索和注入 |
| 9a31293 | compaction candidate/quarantine、质量与 evidence promotion gate |
| d3411a9 | session exposure/adoption/outcome、utility 和成功失败计数 |
| dea479e | 从最终回答检测 ID/title 引用，独立 reference counter |
| 5b49abf、7c691f1 | episodic success 分组派生 procedural candidate、人工 promotion/rejection |
| 1d17a74 | status/validity/type/utility/outcome/engagement 参与 retention |
| 549ec1c、19163e1 | field/path/symbol 权重、可配置 rerank policy |
| a73c90e、7881ce7 | 时间 freshness multiplier、negative-transfer multiplier |
| 67a00d3 | file/symbol/error/phase/module/recent-files 检索上下文和自动注入 |
| d007ec7 | 删除 candidate/quarantine 和 procedural promotion，仍保留直接 consolidation、反馈和 retention；不是一次删除整个复杂系统 |
| b1db70a | compaction Key Decisions 自动写 MEMORY，另存 checkpoint |
| 4512437、d6cecb3 | resume overview 与 turn/overview budget；重要性排序 |
| ee8eca1 | section/bullet LLM judge、embedding/hybrid search、隐式引用判断与反馈 |
| 05c5e47 | 统一 hybrid retrieval、预算与反馈归因路径 |
| 3681c40 | lifecycle maintenance、merge、contradiction、supersede、promotion/archive |
| 4d70303、93082c5 | 真实 session provenance、保留 quarantine；修复合并顺序依赖 |
| 67f2ace、4c81054 | explain/质量 gate/检索 eval；修复 exact-ID 绕过 confidence gate |
| fe42377 | 删除约 6766 行；收缩为 harness-grade Markdown/BM25 + working checkpoint/history；删除 embedding、judge、反馈、状态维护、多因素排序、逐轮注入和 rank eval |
| 8bbeb4b | 补全共享实例、NOTE/checkpoint 交接与只读原始历史检索 |
| 1c6b3c6 | 原子 CRUD、锁、文件签名/index invalidation；没有恢复 candidate 状态机 |

这些删除表达的是产品边界，不是尚未补齐的 TODO。Experience v0 不恢复它们。

## 新证据及适用范围

已阅读 [VibeMemBench 论文](https://arxiv.org/html/2609.23570v1) 与
[官方仓库](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/VibeMemBench)。
论文显示，修复原则和适用边界在整理中丢失或被 transcript 淹没，是主要失效点；
扩大 top-k 不是可靠补救。高质量经验的收益仍依赖 solver 和任务，不能假定
每个任务都应注入。其任务经过正向收益筛选，迁移结果的置信区间跨零，且评测
发生在任务开始；它支持谨慎试验，不能证明 Cade 的在线写入或最佳 recall 时机。
当前官方 README 只有 Coming，不能声称已复跑其 benchmark。

官方参考只取必要约束：

- [Claude Code](https://code.claude.com/docs/en/memory)：会话内可见 Markdown 写入、按需读取 detail；不照搬每次启动加载索引。
- [Gemini CLI](https://geminicli.com/docs/cli/auto-memory/)：明确 scope 和 authoritative copy；不照搬禁止保存 bug fix 的内容边界。
- [OpenHands SDK 源码](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/memory.py)：用户/项目 Markdown，本地 Agent 维护；Cade 不复制 daily logs。
- [Copilot](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/)：citation 与使用前验证；不照搬自动刷新、TTL 或 hosted 生命周期。

## 最小 v0 决策（对应审查问题 3–14）

**缺口**是跨 session 保存昂贵调查得到的根因、失败探索边界和有效修复原则，
并让后续 Agent 在具体信号出现时优先验证；不是持久化更多聊天事实。

**表示**采用普通 H2 body convention。MemoryRecord 不增加持久化字段，
不建立 ExperienceRecord。Preference/Constraint、Decision 继续是普通正文；
只解释 `Type: experience` 来控制经验是否可进入 resume/recall。

```markdown
## fd 单文件发现的根因
Type: experience
Problem: 对明确指定的单文件执行发现时得到空结果。
Root cause: fd 的 search path 语义要求目录；文件路径不是等价的搜索根。
Fix pattern: 对已经明确指定的文件直接处理，目录发现仍交给 fd。
Applies when: 调用方传入单文件且仍使用目录发现逻辑；先检查当前实现。
Anchors: fd; src/search/discovery.py; discover_files
Evidence: commit:<真实 SHA>; test:<命令与结果>; session:<真实 ID>/event:<真实 ID>
```

示例是格式说明，不是本仓库已验证事实。字段可使用换行继续写正文。Anchors
用分号分隔具体 literal，不用通配符，不写泛化的 coding/bug/fix。Evidence 是
简短指针：至少一个真实 commit 或 session/event（工具调用 ID 也可用于历史搜索）
以及执行过的验证命令/结果。不要编造不可获得的 event ID；当前响应本身的 event
尚未落盘时，可引用之前已落盘的测试事件或 commit。少量 pointer 不复制输出。

**写入**复用普通文件工具和现有 CLI；先验证 root cause/fix，说明为何值得复用，
再在当前任务中可见写入。项目经验存 project，跨项目个人偏好存 user，一份
authoritative copy。不保存可廉价重读代码得到的目录地图、当前任务总结或待办。
Host 不判断经验是否真实，形式完整也不是 verified 的证明。

**same-inference**：ProviderEvent/AssistantMessage 能同时表达文本和 tool calls，
可以同一次正常 inference 说明经验并请求 write/edit。但不存在独立 memory
proposal channel，所有工具执行后都继续 loop；没有 final+side-effect 完成契约。
把 XML/JSON 藏进最终文本并由 Host 解析，会新建权限、失败反馈、持久化与呈现路径，
不选。新增 memory_write 也不能消除 loop：独立的最后写入通常再需一次可见模型
请求；若和本就要执行的普通工具合并，可共享下一次正常请求。v0 不新增该工具，
不声称零 round-trip，也不为写入调用 judge/recap provider。

**recall** 保持 explicit。出现具体文件、symbol、error 或明确历史决策需求时才
查询；模型可先开始自主探索，在疑似重复昂贵调查时 recall，不强制任务起始查询。
Experience 只有六项非空且 query 中包含至少一个完整 literal anchor 才参与现有
BM25 排序；不使用 scope 补词绕过 gate，不增添权重。未标记经验的现有记录行为
保持原样。无相关经验则返回空。这个 gate 降低词义模糊造成的污染，但不能证明
相关性和正确性。缺字段记录仍可人工读取和修正，不建立 candidate 存储。

解析只解释行首的无 bullet 标签，标签不区分大小写；Type 与六项字段各出现一次，
空值或重复字段不参与 recall。正文允许续行，Anchors 必须单行。匹配忽略大小写，
要求 anchor 两侧不是代码 token 字符（字母数字、下划线、路径/标识符分隔符）；
`fd` 不匹配 `fdisk`，`discover_files` 不匹配 `other_discover_files`，相对路径
不靠匹配较长绝对路径的后缀召回。严格规则会有 false negative，由 Agent 用
明确 literal 重试，不自动放宽。

**resume** 的普通 durable overview 排除所有 Type: experience，包括形式不完整
的经验。不自动发现或注入 hint/index。用户主动直接读取 MEMORY.md 仍可能看到
全部内容，这不是自动 admission。此前已经进入原始 transcript 的经验仍按照
历史恢复，不伪造删除 lossless history。

**ContextPolicy** 不改：recall 是普通可丢弃 ToolResultMessage，完整结果保存到
事实历史，下一次请求在既有 project_evidence/整体预算中准入，并进入 request
hook/fingerprint；不标为 durable，不扩大 evidence budget。如果正文被预算裁剪，
Agent 必须读完整来源后再采用，不能从头尾残片推断修复。大记录可人工拆成窄问题，
v0 不添加第二套 admission engine 或自动摘要。

**revalidation** 是 Agent 使用前的当前分支文件/symbol/测试检查，走原有权限工具；
Host 不在 recall 中偷偷读取 anchors、执行命令或验证 commit。仅发现文件仍存在
不足以证明历史根因仍成立，变化也不一定使通用 lesson 失效。验证后可使用或拒绝。

**状态** 暂不需要 active/stale/superseded。当前没有可靠确定性规则判定“明显不
再匹配”，目录存在或缺失也无法判定 lesson。用户改变 Decision 时显式 update
或 delete 同一个 authoritative record；旧推理仍在历史/git。发现经验失效时
显式修改/删除，不持久化模型主观 confidence，不做 TTL、maintenance 或后台合并。

**form** 六项保留 problem→cause→fix→boundary 关系和 evidence；避免 raw transcript
和只剩口号的 atomic fact。非空检查只能检查形状，无法自动识别措辞质量；写入
纪律和 coding behavior eval 才能判断。不能用“字段齐全”宣称质量 gate 已解决。

## 设计自审

| 风险 | v0 的实际边界 |
| --- | --- |
| 旧 Memory OS 重生 | 无新存储、状态、计数、policy object、retrieval context 或 reranker；仅 body 解释与排除/匹配 |
| hidden inference | 无 Memory 后台调用；读写都是正常可观察工具，额外 loop 成本明确 |
| bad-memory injection | Experience 不自动进入 resume；literal gate 降低 weak match；仍需使用前检查 |
| 重复 Event History / NOTE | 只存 reusable lesson + pointer；进度与完整轨迹继续由原有层负责 |
| ContextPolicy bypass | 不保护、不固定配额、不在组装之后追加；所有输入仍被 hook 和 fingerprint 覆盖 |
| 权限绕过 | 不新增 Memory write privilege；recall 不额外读取任意 anchor 路径 |
| 无记忆 baseline | 不新增 Host 后台请求；固定指导可能促使 Agent 多执行一次空 recall，实际成本由 eval 记录；普通记忆搜索保持行为 |

## Coding behavior eval（审查问题 15）

选择真实历史修复的前一版本作为隔离 workspace，隐藏 gold patch 和验收测试，
所有条件使用同一个 provider、题目、工具、预算和起点。先运行独立验收确认旧版
失败、gold 修复通过；不能让 solver 自己写的测试决定成功。

| 条件 | 输入 |
| --- | --- |
| A | 无 Experience；同样的 recall 可用且为空 |
| B | 从真实历史修复提取、经独立验收验证的六项 Experience；属于 oracle 经验对照 |
| C | irrelevant 和 stale 分开测试；后者保留匹配 anchor 但故意给错误修复，测重新验证 |
| D | 同一 lesson 淹没在长 transcript 式 Memory 中，字段仍完整 |
| E | 同一 lesson 压缩到 handle fallback correctly 一类短句 |

第一组测显式 recall 整条链路；第二组受控把各 form 交给 solver，隔离 form 和
gate 的影响。E 被形状 gate 拒绝只能证明保护路径，不能证明 solver 的 form
敏感性。不能让脚本依据条件写死探索路径，再把 script steps 当模型收益。
受控 form 组在统一的首次 recall 结果中返回原样文本，所有条件都走相同普通
ToolResultMessage 和 ContextPolicy；仅此组绕开 Experience gate，A 返回空结果。

记录独立验收、tool calls、探索 read/grep/bash 数、第一次读取关键文件的调用
序号、provider 输入/输出 tokens、patch、损坏正常行为的验收失败和 session trace。
至少配对小样本；未观测收益照实报告，不通过不断调题目或挑成功 seed 声称改善。
C 应不降低成功率，匹配 stale anchor 的失败要明确报告；小样本不能证明无显著
损害。协议回归可使用确定性 provider driver，但只能证明 runtime 行为，不能
作为 Experience 价值证据。真实 provider 不可达时保留可重跑的实验与明确阻塞，
不以 mock A/B 代替。

本试验重新呈现同一真实缺陷，并加入独立触发场景；它验证理想经验能否影响
solver 行为，不能证明跨任务迁移或在线写入质量。stale 是明确标记未验证的
合成错误建议，不能代表所有隐蔽过时经验。源码快照建立空 git 仓库以阻止向上
发现真实历史，隐藏 gold patch 和验收代码；发现隔离缺陷的诊断试跑不计入结果。

## 实现落点与数据流

```text
当前任务的 lossless history / 实际验证
  → 当前正常 inference 提出值得复用的经验
  → 可见 write/edit（原有权限）
  → MEMORY.md 的 H2 body
  → explicit recall：完整标签 + literal anchor → 既有 BM25
  → 普通工具证据 → 既有 ContextPolicy
  → Agent 读取当前文件/验证 applicability → 使用或拒绝
```

Host 不从 history 自动提取经验，不判断 Evidence 指针真实性。Session/Event
是 provenance 的事实源，Memory 只存 distilled lesson 和 pointer。

| 文件 | 变更 |
| --- | --- |
| `src/cade/harness/memory/parsing.py` | 解释最小正文约定和 anchor 边界，不扩展 MemoryRecord |
| `src/cade/harness/memory/manager.py` | recall 的 Experience gate、历史提示、resume 排除 |
| `src/cade/harness/memory/tools.py` | 稀疏查询和完整来源提示 |
| `src/cade/harness/agent_runtime/prompting/builder.py` | 修正 recall 名称，指导可见写入、scope、form 和使用前检查 |
| `src/cade/harness/memory/README.md` | 更新模块职责、数据流及链接 |
| `docs/guide/memory.md` | 修正过时路径/能力描述，解释真实读写行为 |
| `docs/memory-architecture.md` | Experience 的边界、ContextPolicy 复用和恢复行为 |
| `docs/experience-memory-design.md` | 当前/历史审查、15 项设计问题、自审和实现落点 |
| `docs/experience-memory-evaluation.md` | 测试、真实结果、成本、限制和复现 |
| `evals/experience_memory.py` | 真实历史缺陷、独立验收、隔离 A/B 和受控 form 模式 |

本地 E2E 源码另在 `src/cade/tests/e2e/test_experience_memory_e2e.py`，按仓库规则
忽略、不提交；可检查的运行 artifact 在 `e2e-results/experience-memory/`。

真实缺口仍包括：人工整理质量未被证明、完整字段但错误/冗长的经验可能通过
gate、revalidation 只有 Agent 行为约束、history 无跨 session query、普通文件
写入没有 manager 锁，以及 final+proposal 无额外 loop 的 provider/host 契约尚不存在。
本轮没有 online distillation、自然 recall timing 或跨任务迁移的收益证据。
