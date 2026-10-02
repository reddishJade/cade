# 请求上下文与预算策略

本文说明请求投影、证据准入、输入预测和换窗算法；运行生命周期与状态归属见
[架构说明](architecture.md)，历史原文读取见 [会话指南](guide/sessions.md)。实现入口为
[请求组装器](../src/cade/agent/request.py)、
[上下文策略](../src/cade/agent/context_policy.py) 和
[上下文管理器](../src/cade/agent/context_manager.py)。

## 请求投影与预算

session 追加事件与外置 artifact 保存会话事实，`DefaultRequestAssembler` 为每次
模型请求生成临时投影。`ContextBlock` 的来源与优先级用于表达上下文生命周期。
`ContextState` 保存当前请求前缀，`WorldState` 保存各 section 的最新完整快照。
规则、NOTE、验证事实和运行状态更新后替换旧投影，删除的状态退出下一次请求。
每次请求的 trace 记录全部上下文块的纳入或丢弃结果。

不可变 `ContextPolicy` 管理物理窗口、输出预留、额外余量、证据准入与换窗工作集。
固定前缀、工具 schema、持久状态和活动消息共享输入预算。CRITICAL/HIGH 块作为
必需状态保留，其他块按优先级使用剩余预算；必需内容超限时，诊断记录超限情况。

预算参数及默认值见 [配置指南](guide/configuration.md)。物理窗口来自
model/provider 元数据，用户可覆盖窗口大小；fallback 到较小模型时收紧预算。
输入预算按 `physical_window = input_budget + output_reserve + headroom` 计算。
增大单次输出上限时，先增加输出预留，再缩小输入预算。支持输出上限的 transport
将预留下发；ChatGPT Codex 后端的预留用于运行时预算，审计记录
`output_limit_supported=false`。

新增输入预留 `next_input_allowance` 最多为触发预算的八分之一，准入目标为
`rotation_threshold - next_input_allowance`。当前输入预测加上新增输入预留达到
触发预算时提前换窗，比例阈值和显式 token 阈值提供触发上限。输出预留与额外余量
在输入预算中各扣除一次，CLI 与运行时从同一个 policy 获取预算。

## 输入预测与证据准入

整体输入预测以成功请求的 provider 用量为锚点，通过消息增减估算后续差额。原始
历史、前缀和配置保持稳定时，NOTE 更新、状态替换与工具裁剪保留实测基线；原始
历史被替换、前缀或配置改变时，预测回退到本地估算。锚点采用当前窗口中成功请求
的正输入用量。本地预测用于准入与提前换窗，实际用量由 provider 返回。

文本差额按解码后的消息正文和工具参数计量，结构化和多模态内容采用保守估算。

工具正文从新到旧共享证据配额，受输入预算和 `agent.evidence_token_budget` 限制。
裁剪投影包含 tool-call ID、执行状态和原文恢复提示，原始结果保留在会话事实中。技能正文和声明为 durable 的工具状态
受保护，多模态结果保持原有内容。小正文比引用更短时保留原文，大正文按可用证据
预算生成头尾预览，引用本身的 token 开销计入配额和完整请求。

下一次 provider 请求在工具执行完成后组装，实际工具结果参与证据准入。尚未执行
的调用使用有界的新增输入预留；当前工具契约缺少可靠的输出上限和历史分布，预留
按统一额度计算。

`context_lifetime=durable` 的工具结果可通过 `context_key` 声明状态替换关系。
同一键的最新成功版本受保护，旧版本改为 history 索引；混合并行组中需要保留的
旧结果标记为过期。无键的持久结果作为独立事实保留。

准入以最终请求为依据。持久状态和新增证据使预测超预算时，先收紧旧证据投影，
再将旧的完整工具交互替换为带调用 ID 的 history 索引；近期完整组和持久组保留。
释放空间后优先恢复近期证据原文，再判断是否换窗，各阶段调整最多三次。预算充足
时，工具调用参数和结果保持原文；预算不足时，策略裁剪证据或回收完整旧交互。
展示格式适配器负责消息格式转换，内容选择和保留由类型化策略处理。

## 换窗工作集

自动换窗带走启动上下文、当前用户请求、持久工具状态和最近一组完整交互。近期
交互受输入预算和 `agent.working_set_token_budget` 限制，可将该配额设为零。
工具调用与结果整组保留，大正文改为历史引用；整组仍放不下时，使用有界操作索引和 history 恢复。
新窗口重新加载当前 NOTE、验证事实和运行状态，换窗保留选定的消息与操作索引，
旧窗口内容通过历史记录查询。

自动 token 换窗要求必需输入能够容纳且存在可回收历史，手动或模型请求的换窗
独立执行。明确的 provider 上下文超限最多尝试一次恢复，恢复路径随自动换窗配置
启用。仍超限的请求保留 provider 错误。

## 预算观测

`RequestAssembly.context_snapshot` 和 `ContextManager.context_snapshot` 提供
物理与有效窗口、固定前缀、持久状态、工作消息、证据、输出预留、余量、剩余预算、
新增输入预留、准入目标、下一轮输入预测、窗口编号与换窗原因。分项复用请求的
本地计量函数，标记 `category_source=local`；其总量为 `category_total_tokens`，
provider 锚定的输入预测为 `total_input_tokens`。

预算分项按注入位置归因：当前前缀中的混合运行状态计入 fixed prefix，NOTE、
验证事实、当前用户意图与受保护工具组计入 durable。审计保存快照、上下文纳入与
丢弃的来源、证据回收量，以及换窗条件未满足的原因。

## 请求审计与换窗记录

普通 agent 请求发送前，`before_provider_request` hook 接收最终
`RequestAssembly` 的 envelope，session 记录 BLAKE2b 请求指纹（32 字节摘要）、
请求规模、provider 信息、options、composition ID 与组装 trace。该记录可用于
关联实际请求与预算决策；完整 wire payload 由当次 assembly 持有。

`context_window_reset` 保存完整、类型化的 surface replacement、来源 entry IDs
和 generation。回放校验来源 IDs 与当前分支前缀的对应关系、generation 递增、
消息结构和工具调用配对。replacement 内容采用上述来源与结构校验，现有记录
提供的指纹针对 provider 请求。

请求审计实现见 [SessionRecorder](../src/cade/harness/session/recorder.py)，换窗回放
实现见 [session surface](../src/cade/harness/session/surface.py)。
