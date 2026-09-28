# Cade 架构

## 定位

Cade 是本地运行的 Python coding-agent harness。它不是一组工具的薄包装，
而是负责模型输入、运行状态、工具权限、会话事实、生命周期和终端交互的
软件运行时。

当前架构可以概括为：

```text
agent
= model provider
+ append-only session ledger
+ local capability graph
+ execution and permission policy
+ lifecycle control
+ typed interaction surface
+ product composition
```

## 核心不变量

1. 模型请求可验证。实际发给 provider 的 messages、tools 和参数必须先形成
   `before_provider_request` hook envelope；session 在发送前持久化其 SHA-256、
   规模、provider 和组装 trace，避免逐轮复制完整历史。
2. session transcript 是事实账本。用户消息、稳定运行事件、压缩 epoch、
   子代理生命周期和最终回答只能追加，不能原地改写历史。
3. 内存状态是日志投影。resume、fork 和 restart 从 transcript surface
   重建，不把 CLI/TUI 对象当成事实来源。
4. 工具呈现属于协议。terminal、diff、location、subagent 等语义由工具产生
   类型化 intent，宿主只负责投影，不从输出字符串猜测。
5. 正确性以组装后的产品为准。局部单测不能替代真实 `build_app()`、真实
   registry、session 落盘和重建路径。
6. 迁移直接完成。当前预发布阶段不保留旧签名、双写、别名适配器或旧 schema
   分支；调用方、测试和文档在同一提交中一次迁移。
7. 一个 run 只使用一个 composition generation。provider、工具表、agent 配置、
   静态权限策略、请求组装器和上下文入口必须在 run 开始前一起发布，运行中
   不得从多个可变对象分别读取。

## 分层

| 层 | 路径 | 所有权 |
|---|---|---|
| Provider | `src/cade/ai/` | provider 协议、流式事件和厂商适配 |
| Agent | `src/cade/agent/` | 消息模型、loop、工具执行和 provider 请求 |
| Harness | `src/cade/harness/` | session、权限、观测、MCP、记忆和运行策略 |
| Coding product | `src/cade/coding_agent/` | coding 工具、产品 registry 和应用装配 |
| Host | `src/cade/cli/`、`src/cade/server/` | REPL/TUI 输入输出、浏览器工作台（WebSocket 事件流） |

依赖应朝更低层稳定协议流动。CLI/TUI 不拥有 session 语义；工具不直接拥有
provider；provider 不感知产品工具。

## 一次回合的数据流

```text
user input
  -> SessionInbox.inbox/inserted
  -> active run claims next_step / next_turn
  -> SessionInbox.inbox/claimed
  -> CodingAgentHarness / Agent loop
  -> RequestAssembly
       - scoped prefix + session surface
       - context collection and injection
       - ContextPolicy admission
       - wire messages + tool schemas + options
  -> provider_request envelope
  -> provider stream
  -> typed assistant/tool events
  -> local Shell or local FileSystem
  -> permission and audit hooks
  -> append-only session events
  -> REPL/TUI projection
```

`SessionInbox` 是所有模型输入的统一所有者，`SessionRecorder` 记录运行输出。
编程式 `ask()`、REPL 和 TUI 必须经过同一条路径。`harness/session/replay.py`
负责从当前 branch 恢复 message history、run metadata、Goal 和 contextual
state；未 claim 的输入由 inbox 自身恢复。

普通 agent 请求只有一个 `RequestAssembler` 入口。provider stream 与审计 hook
消费同一个 `RequestAssembly`，其中包含最终 wire messages、tool schemas、options、
step 和动态 context provenance。禁止在发送前通过通用 transformer 隐式改写
messages；内容选择由 assembly 内的 ContextPolicy 决定，且不修改 session surface。

## Agent composition

`AgentComposition` 是发布 agent 行为的不可变 generation，包含主/备 provider、
冻结工具 schema、`AgentConfig`、静态 gate 策略、`RequestAssembler` 和 runtime
context 入口。`AgentRuntimeConfig` 只保存 session inbox、取消、压缩器、hook、
审计和 grant store 等有生命周期的服务；这些对象不伪装成产品配置。

每个 run 在取得 active-run 所有权后原子捕获一次 composition 与有效 provider，
后续 step 不再重新读取产品装配。`/model` 和静态 permission policy 变更必须发布
新的 generation；active run 存在时拒绝替换。旧的 provider setter、fallback
包装器原地换主和私有 gate 字段写入均不存在。`provider_request` 保存
`composition_id`，因此一次实际请求可以回溯到完整装配代际。

## Context Policy 与请求工作集

session 的 append-only 事件与外置 artifact 保存完整事实；模型请求是由
`DefaultRequestAssembler` 生成的临时投影。沿用现有 `ContextBlock` 的来源与
优先级表达生命周期，不另建历史库或平行的 Context Planner。

`ContextState` 只保留当前请求前缀，`WorldState` 保存各 section 的最新完整
快照。规则、NOTE、验证事实和运行状态更新后替换旧投影；删除的状态退出
下一次请求。每次请求的 trace 覆盖全部实际纳入或丢弃的块，而非只记录变化。

不可变 `ContextPolicy` 统一物理窗口、输出预留、额外余量、证据准入与
换窗工作集。固定前缀、工具 schema、持久状态和活动消息共享输入预算，
不人为划分固定配额。CRITICAL/HIGH 块作为必需状态保留，其他块按优先级
使用剩余预算；必需内容超限会显式出现在诊断中，不静默丢掉规则或笔记。

配置沿用 `agent.reserve_tokens`（默认 16384）与
`agent.rollover_trigger_ratio`（默认 0.95）。
`agent.headroom_tokens` 可指定额外余量；默认独立预留窗口的 2%，最多 8192
tokens，未知窗口时为 1024。物理窗口来自 model/provider 元数据，并尊重
现有用户 override；fallback 到较小模型时收紧预算。

`physical_window = input_budget + output_reserve + headroom`。
增大单次输出上限先增加预留、再缩小输入预算。支持输出上限的 transport
将预留下发；ChatGPT Codex 后端不支持该参数，其预留只作运行时预算，
审计记录 `output_limit_supported=false`。

`agent.next_turn_input_tokens` 默认 1024，并限制为触发预算的八分之一。
这是下一轮新增调用与证据的有界 allowance，不是任意工具输出的精确预测。
准入目标为 `rotation_threshold - next_input_allowance`，实际输入预算仍为
物理窗口减去输出预留和 headroom；三者分别记录，输出与余量只扣一次。
当本轮输入预测加 allowance 达到触发预算时提前换窗。比例阈值和显式
token 阈值只作 guardrail；CLI 与运行时从同一个 policy 获取预算。

整体输入预测以成功请求的 provider 用量为锚点，只估算新增内容。固定前缀和事实历史保持不变时，
NOTE 更新、状态替换与工具裁剪按消息增减估算差额，保留实测基线。原始历史
被替换、前缀或配置改变时回退本地估算；失败、零输入用量与旧窗口统计不
作为新锚点。差额仍是估算，不能把校准后的预测当作下一次请求的精确用量。
本地预算用于准入与提前换窗，不能充当精确的服务端硬限制。
文本差额直接计量解码后的消息正文和工具参数，避免 JSON 转义或 Python
对象表示使换行、引号等重复计费；结构化和多模态内容仍使用保守估算。

工具正文按新到旧共享证据配额：默认最多 32K、不超过输入预算，
`agent.evidence_token_budget` 可覆盖。证据准入裁剪均给出
tool-call ID、执行状态和原文恢复提示。原始结果不变。技能正文和声明为
durable 的工具状态受保护，多模态结果不猜测 token 成本；小正文比引用
更短时保留原文。大正文的头尾预览使用可用证据预算，不固定为几百字符；
引用自身的 token 开销也从该预算扣除，并计入完整请求。

下一次 provider 请求在工具执行完成后组装，此时实际工具结果已经可用于
准入，无需仅凭工具名称猜测输出大小。尚未执行的任意工具调用仍只使用有界
allowance；目前没有可靠的工具输出上限或历史分布契约，不根据名字提高
换窗预留。

`context_lifetime=durable` 的工具结果可通过 `context_key` 声明状态替换关系。
同一键只有最新的成功版本受保护；旧版本改为 history 索引，混合并行组中
不能整体移除的旧结果显式标记为过期。todowrite 使用 `session-todo` 键，
清空清单也是有效的新状态。无键的持久结果保留为独立事实，不猜测其替换关系。

准入以最终请求为依据：若持久状态和新增证据使预测超预算，先收紧旧证据
投影，再将旧的完整工具交互替换为带调用 ID 的 history 索引；近期完整组和
持久组受保护。释放空间后优先恢复近期证据原文，最后才判断是否换窗。
各阶段调整最多三次，避免无界计算；未知工具未来输出不
作精确预测。输入预算已经扣除输出预留与 headroom，判断时不重复扣减。
旧的字典格式换窗、文件名专属保护、字符阈值裁剪及状态 diff 累积路径均已
删除；展示格式适配器只转换消息，同一类型化策略完成选择与保留。
消息数换窗 guardrail 也已移除，避免有界工作集仍被无关的消息计数反复重置。
无条件的 request hygiene 字节、行数与工具参数截断也已删除。预算允许时
工具调用参数和结果保持原文，预算不足时通过同一策略裁证据或回收完整旧交互。

自动换窗带走启动上下文、当前用户请求、持久工具状态和最近一组完整交互。
近期交互默认最多 4096 tokens、且不超过输入预算的四分之一，
`agent.working_set_token_budget` 可覆盖或设为零。工具调用与结果整组保留；
大正文改为历史引用，整组仍放不下则用有界操作索引和 history 恢复。
新的窗口重新加载当前 NOTE、验证事实和运行状态，不递归总结旧对话。

必需输入已经超过预算，或不存在可回收历史时，自动 token 换窗会被抑制，
防止同一问题反复换窗。手动/模型请求换窗仍可执行；明确的 provider 上下文
超限最多尝试一次恢复，关闭自动换窗也关闭这条恢复路径。不可容纳的请求
最终保留明确的 provider 错误，而非无限循环。

`RequestAssembly.context_snapshot` 和 `ContextManager.context_snapshot`
提供物理/有效窗口、固定前缀、持久状态、工作消息、证据、输出预留、余量、
剩余预算、下一轮 allowance、准入目标、预测下一轮输入、窗口编号与换窗原因。
分项统一复用请求的本地计量函数，标记
`category_source=local`；其总量 `category_total_tokens` 与 provider 锚定的
`total_input_tokens` 分开，不能把两者伪装成同一份精确账单。当前前缀中的
混合运行状态按注入位置计入 fixed prefix，NOTE、验证事实、当前用户意图
与受保护工具组计入 durable；这是一份预算归因，并非语义理解器。
审计保存快照、纳入/丢弃 provenance、证据回收量与阻止无效换窗的原因，
不依赖具体 UI。

旧窗口通过现有 `history` 的窗口索引、搜索与分页原文读取恢复。history
默认搜索原始用户意图、assistant 与工具事实，排除 history 自身的查询/回显、
运行时提醒以及 inbox/reset/audit 的派生复制；`include_derived=true` 可搜索
全部记录。按 ID 读取与邻近记录浏览仍保持完整原文，不改变账本。
搜索返回匹配处的短原文及字符偏移，支持直接定位完整结果；若分页结果
被准入裁剪，应缩小页长并重读同一偏移，而非跳过未实际读到的内容。history
输出也受同一证据准入约束；它不是第二套长期记忆。原始事件、artifact、
replay/fork/undo 的分支语义保持独立于请求裁剪。

## Session 事实模型

稳定记录包括：

- `inbox/inserted`、`inbox/claimed`、`inbox/discarded`：输入内容、lane、来源和
  消费生命周期；
- `assistant`：最终用户可见回答；
- `provider_request`：provider 请求指纹、规模、参数和组装 trace；
- `assistant`、`tool_use`、`tool_result`、`final`：运行语义；
- `context_window_reset`：追加式换窗边界，原 transcript 保持不变；
- `subagent_run`：子运行的 started/completed/failed/cancelled 生命周期。

`context_window_reset` 保存完整、类型化的 surface replacement、来源 entry IDs、generation
和指纹。replayer 只按日志顺序应用 replacement；旧窗口不生成摘要。
`final` 不复制已经存在于语义事件中的 messages 和 tool-call 参数，只保存最终回答、
计数、终止信息、metrics，以及恢复 mode/Goal/todo 所需的 run metadata。
只有 `inbox/claimed` 中的 typed message 会进入模型 surface；普通命令记录为
`command` event，不会伪装成用户消息。

## 本地执行边界

Cade 只支持本地执行，不提供容器、远程 workspace 或远程 shell 抽象。
`bash` 依赖 `Shell`，文件工具依赖 `FileSystem`；生产实现分别是
`SubprocessShell` 和 `LocalFileSystem`。这些窄协议用于测试本地行为，不代表
可切换的远程执行世界。

Linux 上，应用装配层默认为 `SubprocessShell` 注入 `LinuxBubblewrapSandbox`。
`workspace-write` 使用只读宿主根并重新挂载项目、`/tmp` 和批准的外部写目录；
`.git`、`.agents`、`.cade` 保持只读，凭据与环境文件被遮蔽，网络进入独立
namespace。所有后代进程继承同一 mount/network/PID namespace。找不到 `bwrap`
时启动 shell 会 fail closed，不会静默退回宿主权限。

审批与 sandbox 是两个独立边界：审批决定某次工具调用能否开始，sandbox 决定
获准命令在 OS 中实际能做什么。当前 OS sandbox 只覆盖 Linux Agent `bash`；
结构化文件工具继续使用路径边界，受信任 hooks 与 MCP server 不经过此 shell
sandbox。bubblewrap 提供 namespace、mount 与 capability 隔离，但它不是容器、
虚拟机或 syscall seccomp 边界。

## 工具呈现

`ToolRenderIntent` 当前包含：

- `terminal`：命令与本地工作目录；
- `diff`：patch、文件集合和首个变更行；
- `location`：文件或目录及行范围；
- `subagent`：batch ID 与 child run IDs。

intent 随 `ToolResultMessage` 进入 runtime event 和 session log。新增呈现类型时，
必须同时更新事件编码、回放解码、CLI/TUI 投影和契约测试。

## 子代理

子代理的身份是独立 durable session，不是父工具调用中的临时 `Agent` 对象。每个
child 拥有自己的 session log、surface、inbox、composition generation 和 provider
request envelope；`subagent/descriptor` 记录 child/parent session ID、one-shot 或
continuable 模式、persona、provider model 和初始 composition ID。session index 的
`parent_id` 提供无需激活 child 的 lineage 枚举。

`subagent` 创建新 child。并行 batch 只允许 one-shot；需要后续对话时显式创建
continuable child，再用 `subagent_continue` 按 child session ID 提交 FIFO turn。
进程中没有 activation 时，manager 从 child log 重建 surface 后冷恢复同一个
session。`subagent_list` 只读取 descriptor，不启动模型。Cade 当前只实现 spawn，
不会复制父 transcript；任务 prompt 必须自包含。

durable session ID、进程内 `activation_id` 和单次 run ID 是三个不同层级。每次
物化和释放都会在 child log 写入 `subagent/activation`；只有当前 direct parent
session 能 continuation、interrupt 或 release。interrupt 只终止当前 turn，release
只回收 idle activation，二者都不删除 durable session。one-shot settle 后自动
release；continuable child 可在 release 后冷物化。

descriptor 冻结首次发布的工具名。冷物化只取 descriptor 工具集合与当前产品
registry 的交集，因此能力可以因工具退役而收缩，不能因产品后来新增工具而静默
扩大。child gate 具有独立 session correlation，并从明确绑定的父权限域派生；父
cancellation 会传播到 child，而 child 局部 interrupt 不会取消父运行。当前 child
registry 不含 delegation 工具，最大 delegation depth 为一层。

父 app 按 child-first 顺序关闭：先取消 live child，在有界时间内等待 turn settle，
再按逆物化顺序 release activation，最后才关闭 MCP 等共享资源。若 child 未能按时
settle，关闭直接失败，不继续销毁其依赖。

每次 child turn 仍创建 batch/run ID，父 session 的 `subagent_run` 事件记录 child
session ID、activation ID、模式、状态、摘要或错误；父 tool result 的 subagent
intent 保存 run 关联。child 模型失败被解析为 completed/failed/cancelled 结果，
基础设施不会通过共享 `messages[]` 假装成普通父对话。

## 组合根

`build_app()` 是产品组合根，按顺序构造：

1. 已解析配置；
2. 共享同一 store 的 session recorder/inbox、memory、context window state 和 cancellation；
3. provider bundle；
4. 本地工具、MCP、memory/history 和 subagent registry；
5. 冻结的 `AgentComposition`、会话级 runtime services 和
   `CodingAgentHarness`；
6. `CadeApp` 生命周期句柄。

任何新能力都应进入拥有该行为的层，并在真实组合测试中证明最小应用仍可
启动、请求、落盘和恢复。

## 明确非目标

- 容器执行、远程 shell、远程 filesystem 或远程 workspace；
- 为未发布调用面保留兼容包装、双写或旧格式解析；
- CLI/TUI 各自维护一套运行或 session 语义；
- 未写入 session event 的隐式模型上下文；
- 仅靠单元覆盖率声明真实产品路径正确。
