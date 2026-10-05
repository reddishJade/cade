# Slash 命令参考手册

在 Cade 终端交互界面中，所有以斜杠 `/` 开头的输入均被视为内部控制指令。终端支持按 `Tab` 键自动补全命令名称与参数。

---

## 1. 会话管理与生命周期

| 命令 | 完整语法 | 功能说明 | 典型场景 |
| :--- | :--- | :--- | :--- |
| `/new` | `/new` | 创建并切换到一个全新的会话 | 开启一段互不干扰的全新任务 |
| `/continue` | `/continue` | 恢复当前项目最近的有效会话 | 重新打开终端，继续未完成的工作 |
| `/resume` | `/resume [session_id]` | 通过会话 ID 恢复指定历史会话 | 切换回昨天的某个特定任务 |
| `/sessions` | `/sessions` | 列出当前项目下的所有历史会话 | 查看历史任务记录与最后更新时间 |
| `/rename` | `/rename <新标题>` | 为当前会话设置自定义标题 | 方便在会话列表中辨识关键任务 |
| `/exit` 或 `/quit` | `/exit` | 安全保存状态并退出 Cade 终端 | 结束本次工作 |

---

## 2. 会话分支与树状溯源

| 命令 | 完整语法 | 功能说明 | 典型场景 |
| :--- | :--- | :--- | :--- |
| `/fork` | `/fork [N\|entry_id]` | 保留所选输入之前的历史，切换新会话并将所选输入放回编辑框 | 编辑历史问题，探索新路线 |
| `/clone` | `/clone` | 完整复制会话日志和快照，然后切换到副本 | 从当前进度开始独立对话 |
| `/tree` | `/tree` | 展示当前会话的 entry tree，选中节点后移动 head 并恢复状态 | 浏览同一会话中的历史与旁支 |

---

## 3. 回滚、撤销与时空穿梭

| 命令 | 完整语法 | 功能说明 | 典型场景 |
| :--- | :--- | :--- | :--- |
| `/undo` | `/undo [N]` | 按逆序恢复最近 N 个可撤销用户轮次的文件变更，逐文件报告结果 | Agent 改错了代码，一键秒级还原 |
| `/rewind` | `/rewind [N]` | 移除最近 N 个用户任务的输入与执行结果，并裁剪快照索引 | 撤销近期的错误提示词或误判操作 |
| `/revert` | `/revert [N]` | 与 `/undo` 相同，恢复最近 N 个可撤销轮次的文件 | 恢复文件到任务开始状态 |

---

## 4. 执行模式切换

| 命令 | 完整语法 | 功能说明 |
| :--- | :--- | :--- |
| `/mode` | `/mode [act\|build\|plan]` | 打开模式列表或切换模式；Shift+Tab 按 act → build → plan 循环切换 |

---

## 5. 上下文与换窗控制

| 命令 | 完整语法 | 功能说明 | 典型场景 |
| :--- | :--- | :--- | :--- |
| `/context` | `/context` | 查看当前窗口的 Token 消耗、预算与换窗状态 | 核对当前模型的有效输入预算 |
| `/rollover` | `/rollover [--force]` | 立即通过 `NOTE.md` 交接任务并主动开启干净窗口 | 对话过长时主动归纳并重置上下文 |
| `/new-context` | `/new-context [--force]` | 执行与 `/rollover` 相同的 NOTE.md 交接和换窗 | 开启新的工作窗口 |
| `/compact` | `/compact` | 强制进行窗口归约并保留最新工作回合 | 手动清理陈旧输出 |

---

## 6. 模型与推理配置

| 命令 | 完整语法 | 功能说明 |
| :--- | :--- | :--- |
| `/model` | `/model [provider/model_name]` | 打开模型与 effort 选择器：上下选模型，左右选 effort，Enter 同时应用，Esc 取消；也可直接输入名称切换，provider 前缀选择模型提供方 |
| `/effort` | `/effort [level]` | 选择或设置当前模型支持的推理强度 |
| `/thinking` | `/thinking [on\|off]` | Responses/Codex 的推理摘要开关；推理强度使用 `/effort` |

---

## 7. 认证与设置管理

| 命令 | 完整语法 | 功能说明 |
| :--- | :--- | :--- |
| `/auth` | `/auth [status\|list\|login\|connect\|logout]` | 查看账号状态，或执行对应认证操作 |
| `/login` | `/login [provider]` | 发起特定 Provider 的凭据登录与验证 |
| `/logout` | `/logout [provider]` | 清除指定 Provider 的本地已存凭据 |
| `/config` | `/config [setting]` | 浏览受支持的设置，或打开对应设置的编辑表单；保存后在后续启动时生效 |
| `/permissions` | `/permissions [list\|clear]` | 打开权限菜单，显示生效规则与 grant，或清除当前会话 grant |
| `/hooks` | `/hooks` | 显示外部 hook 配置来源及最近执行状态 |

---

## 8. 工具、扩展与记忆

| 命令 | 完整语法 | 功能说明 |
| :--- | :--- | :--- |
| `/tool` | `/tool list` 或 `/tool NAME INPUT` | 列出当前模式的工具，或通过运行时权限门控执行工具；INPUT 接受 JSON 对象及单必填参数简写 |
| `/skill` | `/skill NAME [prompt]` | 为当前会话激活技能，附加 prompt 作为后续用户任务 |
| `/memory` | `/memory` | 显示 workspace 的 `.cade/memory/` 目录位置 |
| `/mcp` | `/mcp [status\|reload]` | 检查外部 MCP 服务的连通状态或热重载连接 |

---

## 9. 交互控制与输出

| 命令 | 完整语法 | 功能说明 |
| :--- | :--- | :--- |
| `/steer` | `/steer <message>` | 提交实时指导，运行中在下一推理边界消费，空闲时启动新任务 |
| `/queue` | `/queue [steer\|followup\|interrupt\|message]` | 显示或设置繁忙输入策略，或提交后续任务；默认策略为 followup |
| `/goal` | `/goal [目标描述\|pause\|resume\|clear]` | 显示或设置停止条件，设置后启动任务；支持暂停、恢复和清除，完成度由独立验收请求检查 |
| `/btw` | `/btw <question>` | 使用当前上下文副本独立回答问题，工具集为空；主任务继续执行 |
| `/clear` | `/clear` | 创建并切换新会话，行为与 `/new` 相同 |
| `/help` | `/help` | 在终端打印命令帮助概览 |

N 使用正整数，默认值为 1。`/undo --list` 显示快照记录；撤销按文件检查冲突和
运行时权限，结果包含完成、跳过与失败项。文件撤销、历史回退、分支和换窗的
完整语义见 [会话指南](sessions.md)。

```text
/fork 3                 # 保留第三条输入之前的历史，将第三条输入放回编辑框
/tool read NOTE.md      # 单必填参数的简写
/tool write {"path":"plan.txt","content":"draft"}
/queue followup         # 设置繁忙输入策略
/queue 检查刚完成的改动    # 排入新的用户任务
```
