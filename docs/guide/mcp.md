# MCP 外部服务与工具扩展

Cade 原生支持开放行业标准 **MCP (Model Context Protocol)** 协议。通过 MCP，你可以无缝将各种现成的外部生态工具（例如 GitHub API、PostgreSQL 数据库、Sentry、文档服务器等）作为本地工具接入 Cade。

---

## 1. 配置 MCP 服务器

在项目根目录的 `.cade/mcp_config.json` 中，通过 `mcpServers` 块注册服务：

```json
{
  "mcpServers": {
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git", "--repository", "."]
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    },
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost:5432/mydb"],
      "env": {
        "DEBUG": "true"
      }
    }
  }
}
```

每个服务支持以下配置字段：
- `command`：可执行命令名（如 `uvx`、`npx`、`docker` 等）；
- `args`：启动参数数组；
- `env`：注入 MCP 子进程的环境变量。

---

## 2. 工具发现与自动注入

1. **启动发现**：Cade 启动时读取有效的 schema 缓存；没有有效缓存时，与已启用的 MCP 服务握手、获取工具定义并关闭发现连接；
2. **完整注册**：工具以 `mcp__{server}__{tool}` 名称和完整参数 schema 注册，首次调用时建立持久连接，无需先搜索 schema；
3. **安全审计遵循**：MCP 工具同样受到 Cade 权限引擎的保护，如果外部工具涉及写数据库或外部 HTTP 请求，依然会按照当前模式（Plan/Build/Act）执行拦截或弹窗确认。

---

## 3. 在终端中管理 MCP

在 TUI 运行期间，你可以使用 `/mcp` 命令管理连接状态：

```bash
/mcp status       # 显示当前应用的 MCP 连接状态、工具数和协议身份
```

修改 `.cade/mcp_config.json` 后重新启动 Cade。可以使用 `cade --continue tui`
恢复最近会话；工具配置在新应用启动时生效。
