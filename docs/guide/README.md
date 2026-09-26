# Cade 使用指南

Cade 是运行在本地工作区中的编码 Agent。它把模型推理、工具执行、权限审批、上下文管理、会话恢复和交互界面组合成一条可持续的工作流。

## 推荐阅读路径

| 目标 | 文档 |
| --- | --- |
| 安装并完成第一次运行 | [install.md](install.md) → [quickstart.md](quickstart.md) |
| 理解运行流程 | [architecture.md](architecture.md) |
| 配置模型和运行参数 | [providers.md](providers.md) → [configuration.md](configuration.md) |
| 选择自主性和安全边界 | [modes.md](modes.md) → [security.md](security.md) |
| 使用本地工具 | [tools.md](tools.md) |
| 管理长任务 | [sessions.md](sessions.md) → [memory.md](memory.md) |
| 扩展 Agent | [skills.md](skills.md) → [mcp.md](mcp.md) → [hooks.md](hooks.md) |
| 使用子代理 | [subagents.md](subagents.md) |
| 查阅命令和界面 | [slash-commands.md](slash-commands.md) → [cli.md](cli.md) → [web.md](web.md) |

## 日常工作流

```bash
cd /path/to/project
cade               # 首次启动会引导配置，随后输入任务
cade -c            # 下次继续最近的任务
cade --resume      # 选择其他历史任务
```

`@` 引用文件，`/` 查找命令，Ctrl+J 换行。任务运行时 Enter 排队，Alt+Enter 纠偏。操作提示就在输入框下方；详细示例见 [快速开始](quickstart.md)。

运行时的 turn、session 和工具审批机制见 [架构说明](architecture.md)，不影响你直接开始使用。

## 配置与数据位置

- 个人默认配置：`~/.cade/settings.json`
- 项目配置：`cade.config.json`
- 项目本地配置：`.cade/settings.json`
- 会话账本：`.cade/sessions/`
- 永久授权：`.cade/approval_grants.json`
- MCP 配置与缓存：`.cade/mcp_config.json`、`.cade/mcp_cache.json`
- 项目记忆：`MEMORY.md`
- 用户记忆：`~/.cade/memory/MEMORY.md`
- 审计日志：由 `observability.audit_path` 指定

工具以当前 `--project-root` 作为默认工作区边界；项目外访问需要 `external_directories` 与对应 access 授权。启动时明确指定项目根目录，可以让会话、工具、配置和 Git 状态保持同一工作区语义。
