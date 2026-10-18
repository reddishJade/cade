# f118d163 合并回归审计（2026-10-18）

基准：`c27562db`（两分支分叉点）；dev 首父 `cf4e9e55`；特性分支第二父 `66ab71bd`；合并提交 `f118d163`；当前远端 dev `8006e85`。审计范围：`c27562db..cf4e9e55` 共 24 个提交。

## 核对方法

逐个读取 24 个 commit 的变更文件；从 Git tree blob SHA 比较三个父/合并快照以及当前 dev。下表的“原侧/分支侧/手工”表示**合并结果的文件版本**，不表示功能是否保留。同一文件可能出现在多个提交，表中的数量不可求和。后续 dev 的文件变更还需用具体 API 契约和行为测试验证等价性。

净变更（`c27562db → cf4e9e55`）涉及 99 条文件路径：合并结果与 dev 父树完全相同 6 条、与特性分支树完全相同 88 条、独立合并结果 5 条。这是覆盖风险指标，不是“88 个功能丢失”。

## 24 个提交逐项文件版本核对

| Dev 提交 | 改动主题 | 原侧 / 分支侧 / 手工 | 代码审计状态 |
| --- | --- | ---: | --- |
| [2659163](https://github.com/reddishJade/cade/commit/2659163ad167) | SDK 重试 / 删除 ProviderRuntime | 0 / 11 / 0 | **回退确认**：`_runtime.py` 与 `ProviderRuntime` 被重新引入 |
| [32431a5](https://github.com/reddishJade/cade/commit/32431a566765) | asyncio worker | 1 / 0 / 0 | 文件保留 |
| [fe638f1](https://github.com/reddishJade/cade/commit/fe638f1d6894) | GateConfig 集中配置 | 0 / 5 / 0 | 现有 GateConfig 存在；装配等价性待测 |
| [a025a9e](https://github.com/reddishJade/cade/commit/a025a9eaf1d5) | typed rollover | 0 / 8 / 0 | `message_codec.py` 回来；行为待测 |
| [afb15e1](https://github.com/reddishJade/cade/commit/afb15e128307) | run result metadata | 0 / 6 / 0 | 旧 `CodingRunState` 恢复；范围待测 |
| [242a37e](https://github.com/reddishJade/cade/commit/242a37e3c7cb) | immutable context sections | 0 / 8 / 0 | 当前有 frozen collectors；等价性待测 |
| [86c4943](https://github.com/reddishJade/cade/commit/86c4943d5cb5) | 按活跃角色构建 Provider | 0 / 10 / 0 | **行为回退确认**：当前 `_build_llm_profiles` 为 main/subagent/reviewer/judge/refiner 全部构造实例 |
| [2212d53](https://github.com/reddishJade/cade/commit/2212d53b0f1c) | 删除冗余 file display metadata | 0 / 1 / 0 | 清理未保留；低优先 |
| [23b45df](https://github.com/reddishJade/cade/commit/23b45dfc13c1) | 删除不可达结构化搜索工具 | 0 / 12 / 1 | 旧 `glob_search.py` 等恢复；需检查实际可达性 |
| [ff3569f](https://github.com/reddishJade/cade/commit/ff3569f54e12) | 删除 inactive citations 装饰 | 0 / 6 / 0 | `prompting/citations.py` 恢复；需检查是否被重新使用 |
| [3011a78](https://github.com/reddishJade/cade/commit/3011a7897925) | 单一 runtime tool adapter | 0 / 2 / 0 | 旧 tool gate 文件版本恢复；职责重叠待审 |
| [8945851](https://github.com/reddishJade/cade/commit/89458517ff47) | audit / MCP 共用脱敏 | 3 / 1 / 2 | 部分保留并手工合并；需检查 MCP 输出回路 |
| [14467fc](https://github.com/reddishJade/cade/commit/14467fcd518c) | 单一子 Agent 工具 registry | 1 / 0 / 1 | 部分保留并手工合并；需检查实例唯一性 |
| [c7b54ca](https://github.com/reddishJade/cade/commit/c7b54ca2a082) | session entries 直读 history | 1 / 0 / 1 | 部分保留并手工合并；历史查询待测 |
| [1a24f88](https://github.com/reddishJade/cade/commit/1a24f88343a0) | 单一 MCP server state | 0 / 0 / 1 | 手工合并；当前 MCP 状态契约有测试，仍需检查配置变更 |
| [094ecdd](https://github.com/reddishJade/cade/commit/094ecdd22cb1) | 统一 loop → host 事件管线 | 0 / 17 / 1 | **高风险待测**：结构化事件、持久化与 host 消费路径 |
| [22b534a](https://github.com/reddishJade/cade/commit/22b534ac4cd4) | 原生 async provider/host stream | 0 / 19 / 0 | **高风险待测**：当前仍有 async 接口，取消和流关闭语义尚未对齐验证 |
| [563238a](https://github.com/reddishJade/cade/commit/563238a2466d) | Provider 配置一次解析 | 0 / 11 / 0 | **高风险待测**：当前仍在 registry 构造 runtime wrapper |
| [f4b83ce](https://github.com/reddishJade/cade/commit/f4b83ce19903) | 删除重复 telemetry | 0 / 6 / 0 | 简化被覆盖；需要核对 usage 单一来源 |
| [c19c7c6](https://github.com/reddishJade/cade/commit/c19c7c6f6366) | 按 tool call 绑定权限证据 | 0 / 8 / 0 | **结构回退确认**：目前仍通过 `_last_perm_results[tool_call_id]` 跨 hook 传递；实际错误需并发/重试复现 |
| [fc32a6d](https://github.com/reddishJade/cade/commit/fc32a6da771f) | 结构化工具结果 / 图片 | 0 / 18 / 2 | **行为回退确认**：`_codec._convert_tool_result` 把 `ImageContent` 变成 `[image result...]`，丢失多模态 payload |
| [4d24e34](https://github.com/reddishJade/cade/commit/4d24e34280f3) | 清理 runtime 测试契约 | 0 / 3 / 0 | 测试回退；需根据当前设计决定恢复内容 |
| [948facf](https://github.com/reddishJade/cade/commit/948facf016f2) | 每次 run 固定权限快照 | 0 / 5 / 0 | **高风险待测**：当前有 Gate snapshot；须检查中途权限切换 |
| [cf4e9e5](https://github.com/reddishJade/cade/commit/cf4e9e55eb75) | 显式执行模式 | 0 / 8 / 1 | **回退确认**：Plan 超时被恢复；由 [PR #17](https://github.com/reddishJade/cade/pull/17) 补修 |

## 后续修复顺序

1. **权限证据**：为 `c19c7c6` / `948facf` 补并行相同 tool ID、重试、run 切换和审批结果归属测试；确认失配后以独立 PR 修复。已有 [PR #18](https://github.com/reddishJade/cade/pull/18) 处理输入来源，但与 call-local 结果归属不同。
2. **结构化工具结果**：为 `fc32a6d` 恢复多模态 payload 的 provider 传递及 round-trip 测试，避免仅把图片改写为提示文本。
3. **Provider 配置及生命周期**：复测仅使用 main 的角色实例数、独立角色配置、错误重试与流关闭。恢复方式应针对当前代码，避免原样 cherry-pick 大型历史重构。
4. **事件与 Session 恢复**：对 `094ecdd`、`22b534a`、`a025a9e`、`948facf` 做同一次可重放轨迹比对，以观测日志和 wire 请求证明契约。

审计结论仅陈述有源码/文件版本证据的事项；其余保留“待测”。CI 通过不能证明跨分支功能没有发生语义回退。
