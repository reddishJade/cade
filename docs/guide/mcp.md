# MCP services and tools

Cade supports the Model Context Protocol (MCP). Connect external tools such as GitHub, PostgreSQL, Sentry, and documentation services.

## Configure servers

Register services in the `mcpServers` block of `.cade/mcp_config.json` in the project root:

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

Each server accepts these fields:

- `command`: executable, such as `uvx`, `npx`, or `docker`.
- `args`: startup argument array.
- `env`: environment variables for the MCP subprocess.

## Discovery and registration

1. At startup, Cade reads the valid schema cache. Without a valid cache, it connects to enabled servers, retrieves tool definitions, and closes the discovery connections.
2. Tools register as `mcp__{server}__{tool}` with complete parameter schemas. The first call opens a persistent connection; no separate schema search is needed.
3. MCP tools pass through Cade's permission engine. External writes and requests remain subject to the current mode's permission and approval rules.

## Inspect connections

In the TUI, run:

```bash
/mcp status       # 显示连接状态、工具数和协议身份
```

Restart Cade after editing `.cade/mcp_config.json`. Use `cade --continue tui` to resume the most recent session. Tool configuration takes effect when the new application starts.
