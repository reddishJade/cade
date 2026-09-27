# 会话、恢复与上下文换窗

先记住三个恢复入口：

```bash
cade -c            # 直接继续当前项目最近的任务
cade --resume      # 从历史会话中选择
cade --session ID  # 恢复指定会话
```

在界面内使用 `/resume` 选择历史，`/new` 开始新会话。恢复后直接输入下一步；`cade -c` 没有历史时会提示开始新任务。会话默认位于项目的 `.cade/sessions/`。

以下介绍会话存储和高级操作。Cade 将 session 组织为可追加的 JSONL 事实账本，并从当前 branch 投影出模型历史、界面历史和运行状态。

## 1. Session 文件

默认目录：

```text
.cade/
├── sessions/
│   └── session-<timestamp>.jsonl
├── session_index.json
├── snapshots/<session-id>/
└── approval_grants.json
```

每个 entry 包含 `id`、`parent_id`、`type`、`content` 和 `created_at`。`session_index.json` 保存 title、summary、project path、updated time 和 `head_id`。追加操作由 file lock 保护。

## 2. 稳定事件

运行时主要记录：

- `inbox/inserted`、`inbox/claimed`、`inbox/discarded`：输入生命周期。
- `provider_request`：请求指纹、规模、provider、options 和 context trace；完整 wire
  payload 只交给同步 `before_provider_request` hook，避免随历史长度重复落盘。
- `assistant`、`tool_use`、`tool_result`：模型与工具语义事件。
- `context_window_reset`：新窗口 ID、触发原因、replacement、generation、源 entry id 和 surface digest。
- `final`：回答、终止原因、metrics 和不含消息副本的 run metadata。
- `goal_state`：Goal 的条件、暂停状态和重入计数。
- `subagent/descriptor`、`subagent/activation`、`subagent_run`：子代理身份与运行谱系。

实时 text delta、reasoning delta 和 tool update 用于流式展示；稳定事件足以恢复语义历史。

## 3. Surface 与恢复

`SessionSurface` 沿当前 `head_id` 回溯 branch，再应用 inbox claim、assistant、tool use、tool result 和 context-window replacement，生成模型消息。

消息使用显式 `kind`/`payload` 标签编码。恢复时校验消息结构与工具配对：每个 tool call 对应一个 result，orphan result、重复 id 和未闭合调用形成恢复错误。

`replay_session` 同时恢复：

- Agent message history。
- 当前执行模式、Goal 和 todo。
- 已激活技能。
- active files 与工具结果上下文。
- session id、history session id 和恢复提示。

## 4. 输入 lane 与运行控制

`SessionInbox` 使用两条输入 lane：

| lane | 消费时机 |
| --- | --- |
| `NEXT_STEP` | 当前 active run 的下一次模型请求前 |
| `NEXT_TURN` | 当前 run 完成后启动新的 run |

`SessionRunController` 为每个 session 维护一个 active run。普通输入、`/steer`、follow-up、runtime reminder 和 interrupt 都先写入 inbox，再由 run 在定义好的边界 claim。

`ActiveRunHandle` 的状态为 running、cancelling、finishing、finished。生成结束前会关闭 step input，再 claim 最后一批输入，保证输入和结束事件顺序稳定。

## 5. 上下文换窗

换窗触发来源：

- 下一次请求的预测输入加有界 allowance 达到触发预算；成功 provider usage 校准预测，缺失时使用统一本地估算。
- 配置的 message count 或绝对 token threshold。
- 模型调用 `new_context`，或用户执行 `/compact`、`/rollover`。

窗口大小优先取 provider profile 的 `context_window` 覆盖；未覆盖时读取模型元数据。
输入预算扣除输出预留与独立运行余量，请求准入再给下一轮新增输入留 allowance。
先回收旧证据与旧工具交互，预测成本仍无法安全容纳时换窗；95% 比例只是额外 guardrail。

`ContextWindowRollover` 不生成摘要。自动换窗、模型 `new_context` 和 `/compact`
重新注入启动上下文、已激活 skill、当前持久工具状态、最近的真实用户请求和
有界的近期完整工具组，其余轨迹通过 history 恢复。同一个运行继续执行；环境和
持久化历史保持不变。接近换窗预算时，根据请求预测提醒模型把执行前沿写入
项目根 `NOTE.md`。没有笔记也允许模型
换窗，避免窗口已满时无法继续；这时需要通过 `history` 找回进度。

`/rollover` 不携带普通对话，默认要求非空 `NOTE.md`；
`/rollover --force` 可显式跳过这项保护。

## 6. 换窗的持久化语义

换窗结果作为新的 `context_window_reset` event 追加，原始账本不改写。replacement 保存完整当前 surface、generation 和 source entry ids；恢复时加载最新 replacement，再沿账本继续构建。

`history` 工具可列出窗口边界、搜索当前 branch、分页读取某条原始记录，或查看其邻近记录。因此模型的当前 context 是可丢弃工作集，session transcript 才是可检索的无损事实源。

## 7. 分支与回退

```text
/fork       从当前 branch 的用户消息选择起点并创建新 session
/clone      复制当前 session
/tree       浏览 entry tree 并把 head 移到选中 entry
/rewind 3   将 head 回退 3 个用户 turn
/continue   恢复当前项目最近的有意义 session
/resume     选择历史 session
```

原 session 的 entry 保持不变；新分支通过新的文件或新的 head 投影工作历史。`/tree` 和恢复后会重新加载 Agent 与界面状态。

## 8. 文件快照与 `/undo`

Git 工程的每个用户 turn 可以建立 pre/post snapshot。快照使用 `.cade/snapshots/<session-id>` 下的隐藏 Git tree，记录修改、创建和删除文件，并排除环境密钥、生成目录和大型文件。

```text
/undo --list   查看快照记录
/undo          回退最近一个可撤销 turn
/undo 3        回退最近三个可撤销 turn
```

回退时校验路径、turn changed files、post snapshot 冲突和 PermissionEngine。冲突文件进入 skipped，已被用户继续修改的内容得到保留。文件恢复与 session `/rewind` 是两条独立操作。

## 9. 历史检索

`history` 工具在当前 branch 的原始 JSONL entry 上执行关键词 search，或读取指定 message id 附近的记录。它用于恢复后找回已经被 surface replacement 压缩的精确细节。

## 10. 自动化控制面

`cade session` 允许外层 Agent 查询 session，无需直接扫描 JSONL，也不要求 provider 凭据：

```bash
cade session status <session-id> --json
cade session tail <session-id> --lines 20 --json
cade session result <session-id> --json
cade session export <session-id> --output run.json --json
cade session interrupt <session-id> --json
```

`status` 返回路径、大小、entry 计数、是否已有结果和 active run 状态。`result` 优先返回 `cade exec` 写入的完整 result envelope，旧 session 则回退到最后一条 `final` 事件。`export` 使用权限为 `0600` 的原子写入生成单一 JSON 文件。

`interrupt` 不根据 PID 向进程发送信号；它在 session 目录写入协作式中断请求，正在运行的 `cade exec` 会监听该请求并调用自身的流中断路径。不存在 active run 时，命令返回非零状态且 `accepted` 为 `false`。

## 8. 写入与恢复边界

新 JSONL 记录同时保存提交后的 `head_id`。日志先 flush/fsync，索引再通过
临时文件和原子替换提交；索引落后或丢失时，恢复和 `history` 使用日志中的
活动分支。主动回退追加 `head` 记录，因此索引丢失不会重新激活已放弃的尾部。

没有日志 head 字段的旧会话仍优先使用原索引指针，兼容过去的主动回退；
索引也丢失时只能恢复最后写入的分支，无法推断旧的导航意图。下一次追加或
回退即开始使用新的日志提交规则。索引丢失后的标题等展示元数据无法凭空恢复。

只有损坏且未以换行结尾的末条记录被视为未完成写入，恢复完整前缀后可以
继续追加。中间坏行、重复 ID、缺失父节点和循环不再静默忽略。恢复读取和
历史检索共用日志解析及分支选择规则，不任意截取最后 200 条记录。
