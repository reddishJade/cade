---
name: cade-e2e-test
description: Run and assess real Cade end-to-end acceptance tests through exec, TUI, or web, using isolated fixtures inside the Cade workspace, explicit provider configuration, and independently verified results.
---

# Cade E2E 验收

先阅读仓库的 `HUMAN.md` 和 `docs/testing.md`。后者定义验证层级、测试代码准入和证据要求；本 skill 补充实际运行步骤。文档修改和行为保持的整理按该指南选择检查，不必运行模型任务。OpenClaw 专项评测另用 `cade-openclaw-eval` 的任务边界；默认验收模型和工作路径遵循本 skill，用户明确指定的参数优先。

## 验收路径与工作目录

- Agent、工具、权限、上下文和会话：通过公共 `cade exec` 入口，使用真实 provider、服务和会话存储。
- TUI：驱动真实终端宿主；web：驱动实际服务器和浏览器。exec 成功不能证明 UI 行为。
- 替换 provider 或直接调用内部组件属于集成验证，不标记为 E2E。
- 所有由测试管理的工作区、Git worktree、会话、配置、prompt、日志、截图、缓存与结果必须位于当前 Cade 工作路径内。使用 `e2e-results/<unique-run>/`；不使用 `/tmp` 或其他工作路径外的位置。账号凭据仍由 Cade 从既有认证存储读取，不复制到测试目录。
- 创建或清理前解析路径和符号链接，确认实际目标仍在 Cade 工作路径内。不要覆盖已有目录。把 task fixture 与 results 分开，避免被测 Agent 改写验收证据。不要给测试写入工作路径外的额外授权。

开始前写清用户任务、初始文件、受保护行为、预期结果、独立检查方法和执行命令。验收标准先于运行，不能因模型结果而放宽。选择最小但完整的场景；涉及恢复或权限时包含相应失败、拒绝或续接步骤。

## 配置与前置检查

1. 在本次运行目录下准备独立 fixture 或 Git worktree，以及专用 sessions 和结果目录。检查 runtime、依赖、shell、sandbox 和独立验证命令能够运行。记录 Cade 与任务仓库的版本及初始状态。
2. 显式选择 mode、model、transport、effort 和审批方式。用户指定参数优先；普通 Cade 验收默认使用已有 Codex 登录、`openai_codex`、`gpt-5.6-luna`、`high`、`build`、`auto-review`。模型可用性仍需确认；验证其他 provider、mode 或审批边界时使用场景要求的参数。
3. 使用独立 `--config`，不修改全局 `~/.cade/settings.json` 或删除 API 配置。默认模型与连接保存在 settings，API key 和 OAuth 按 provider 独立保存在 `~/.cade/auth.json`。当前 DeepSeek API 欠费，普通验收不使用它；明确测试 DeepSeek 时先确认服务可用。
4. `--config` 只替代项目 `cade.config.json`，仍会合并全局配置和工作区 `.cade/settings.json`。检查有效 main、subagent、fallback 和 Reviewer 来源；单独传 `--model` 不足以控制它们。使用 [配置示例与运行命令](references/run.md) 显式指定默认与辅助角色的 provider/model/effort，避免合并后遗留 DeepSeek 选择。
5. 检查凭据存在和配置解析；只记录模型、transport、effort、端点和是否有凭据。不要打印或复制 API key、OAuth token、refresh token 或完整认证配置。凭据存在不代表服务有额度。
6. 第一次真实调用同时检查服务可用性。`config.resolved` 应符合场景参数；涉及子代理或 Reviewer 时也检查相应请求。配置不符的运行不能算作该场景的有效验收。

## 执行与判断

- 从 Cade 仓库运行安装的入口；先检查当前 `cade exec --help`。使用 prompt 文件、JSONL 事件、专用会话目录及 final answer 文件。设置与任务规模匹配的 step、LLM call 和 wall-clock 上限。
- 保持场景要求的 sandbox、网络与审批设置。不要为了通过测试关闭保护或把审批改为无条件允许。依赖临时目录的工具使用运行目录内的 `TMPDIR`；uv cache 使用工作路径内的目录。
- 保存 stdout、stderr、进程退出码和所有尝试。已有结果文件不覆盖；重试使用新的运行目录或文件名。
- 检查工具结果、审批拒绝、上下文事件和最终状态。命令非零退出码仍是失败，即使模型随后恢复。空的 `run.completed.validation` 或 `changed_files` 不能替代工具事件与真实工作区检查。
- 独立读取结果文件、审查 diff，并执行预先写明的验证命令。模型完成声明和退出码 0 只是辅助证据。需要续接时通过新进程恢复同一 session 后再验证。
- 外层 Agent 在任务开始后补写结果、修代码、准备依赖或发送 steering prompt，会结束自主阶段；分别报告干预前后的结果。不要把外层完成的工作记为 Cade 自主完成。

## 失败处理

区分配置、环境、服务、模型任务和 Cade 缺陷：

- HTTP 402、余额不足、失效凭据或明确缺少依赖：记录具体前置条件，停止相同条件下重试。不充值，不改全局 provider，不把它报告为通过。
- 连接错误：先检查宿主网络限制、端点和授权条件。只有修正原因或有临时故障证据才进行有上限的重试。
- 普通验收已知 provider 不可用时，可在既有授权内改用可用 provider；保留失败记录，另起一次运行并说明参数变化。针对特定 provider 的验收不能用换 provider 代替。
- 模型遗漏步骤或错误实现：保留失败结果；重新提示或重复运行仍按原标准评估，报告次数。
- 可复现的 Cade 缺陷：保留最小复现场景，在拥有该行为的层修复，运行相关检查及真实宿主验收。新增测试代码须符合 `docs/testing.md`，不加入任务专用 workaround。

## 完成与交付

报告通过、失败或前置条件阻塞，列出有效配置、命令、session、独立检查结果和剩余覆盖范围。区分自主完成与干预后完成。保留失败尝试和脱敏证据；生成日志、认证数据、临时任务仓库不进入 Git。

结果默认保留在工作路径内，便于复查；不自动删除整次运行目录。完成的 worktree 按任务约定通过 Git 生命周期移除，先保留 diff 和验收证据。不要删除用户已有目录。
