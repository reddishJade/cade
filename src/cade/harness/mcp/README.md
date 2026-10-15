# MCP integration

This package connects independent Model Context Protocol servers as standard Agent tools with discovery, lazy connections, and shared permissions.

## Registration flow

```text
.cade/mcp_config.json
  → McpRuntimeRegistry
  → McpClient / LazyClientRef: SDK stdio transport
  → build_mcp_tools()
  → ToolSpec: mcp__{server}__{tool}
  → ToolGate
```

### Files

- [client.py](client.py): `McpClient` and `LazyClientRef`, subprocess stdio, JSON-RPC initialization, reconnection, and shutdown.
- [tools.py](tools.py): `McpRuntimeRegistry` server states and `build_mcp_tools` schema conversion.
- [results.py](results.py): normalize text, embedded resources, and image data into tool results.

## Invariants

Use the `mcp__{server}__{tool}` namespace; never override built-in tools. A server crash or timeout must return a local tool error rather than fail the main loop. MCP calls must pass through permissions and auditing.
