# Cade 应用交互层

本层提供宿主无关的应用操作，供 TUI、浏览器或其他宿主使用。
它属于编码产品的应用服务，通过应用契约调用产品装配与运行时服务。

| 模块 | 职责 |
| --- | --- |
| `app_contract.py` | 宿主调用的应用与 agent 接口 |
| `commands.py` / `command_handlers.py` | 命令定义、分发和会话、模式、上下文、工具操作 |
| `completion.py` | 返回命令、模型、技能与文件的补全候选，由宿主输入控件呈现 |
| `approval.py` | 用户审批选择与 HITLResult 之间的转换；Harness 管理策略与授权记录 |
| `models.py` / `reasoning_effort.py` | 模型发现和 effort 选项 |
| `context_stats.py` | 返回上下文与用量统计快照，由宿主更新界面状态 |
| `file_refs.py` / `skills.py` / `tools.py` | 用户输入展开、技能激活和经过权限门控的工具命令 |
| `sessions.py` / `git.py` / `credentials.py` | 会话查询、项目分支和凭据可用性查询 |

依赖方向：运行模式与浏览器宿主 → 产品交互服务 → 产品装配与运行时。
`ai/` 拥有模型适配，`agent/` 拥有模型循环，`harness/` 拥有运行控制、会话和事件流，
`coding_agent/` 拥有编码产品装配。server 通过本层与编码产品调用这些能力。

命令输出与选择通过 `CommandOutput` 注入；认证、配置和其他宿主操作通过
`CommandContext.host_command` 注入。`coding_agent/modes/tui/` 拥有 Rich、prompt-toolkit、
questionary、终端输出、终端尺寸计算，以及样式、预览、计时、折叠和输入控件。
