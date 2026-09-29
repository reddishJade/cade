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

1. **自动握手**：Cade 启动时会自动与配置的所有 MCP 守护进程握手，抓取它们声明的工具定义（Tool Schemas）；
2. **命名空间隔离**：外部工具会自动注册进 Cade 的主工具池；
3. **安全审计遵循**：MCP 工具同样受到 Cade 权限引擎的保护，如果外部工具涉及写数据库或外部 HTTP 请求，依然会按照当前模式（Plan/Build/Act）执行拦截或弹窗确认。

---

## 3. 在终端中管理 MCP

在 REPL 运行期间，你可以使用 `/mcp` 命令管理连接状态：

```bash
/mcp status       # 检查所有外部 MCP 服务的连通性、延迟与暴露的工具列表
/mcp reload       # 修改配置后热重载所有 MCP 客户端，无需重启终端
```
