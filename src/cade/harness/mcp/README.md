# Cade Harness MCP — Model Context Protocol 扩展系统

本目录实现对 Model Context Protocol (MCP) 开放标准的支持，专注于解决：**如何将本地或远程独立运行的 MCP 工具服务器，安全、惰性、无缝地挂载为 Agent 可直接调用的标准化工具。**

---

## 1. 核心架构与工具挂载

```
                     .cade/mcp_config.json
                               │
                               ▼
                      McpRuntimeRegistry
                               │ (读取配置并注册服务)
                               ▼
        ┌──────────────────────────────────────────────┐
        │  McpClient (基于官方 Python SDK stdio 进程)   │
        │  - 采用 LazyClientRef 惰性启动连接           │
        └──────────────────────┬───────────────────────┘
                               │
                               ▼
                        build_mcp_tools()
                               │
                               ▼
           生成以 mcp__{server}__{tool} 命名的标准 ToolSpec
                               │
                               ▼
                    接入 ToolGate 统一权限校验
```

### 核心文件与职责
- **客户端与生命周期 ([client.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/mcp/client.py))**：`McpClient` 与 `LazyClientRef`。管理子进程 stdio 管道、JSON-RPC 会话初始化、断线重连与优雅销毁。
- **工具注册与元数据 ([tools.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/mcp/tools.py))**：
  - `McpRuntimeRegistry`：管理所有配置的 MCP Server 状态（CONNECTED, ERROR, DISABLED）。
  - `build_mcp_tools`：拉取 Server 的工具定义（Tool Schema）并转化为 Cade 的 `ToolSpec`。
- **结果归一化 ([results.py](file:///C:/Users/dwei/workspace/cade/src/cade/harness/mcp/results.py))**：解析 MCP 协议中的文本块、内嵌资源（EmbeddedResource）与图像数据，转为 Cade 统一的工具返回格式。

---

## 2. 架构不变量与设计禁忌

- **工具命名隔离**：所有 MCP 动态工具必须严格遵循 `mcp__{server}__{tool}` 命名空间规范，严禁覆盖内置核心工具。
- **异常故障隔离**：单个 MCP Server 的崩溃或响应超时不得拖垮主 Agent 循环；连接故障必须作为局部工具错误返回。
- **受权限引擎管辖**：MCP 工具绝非安全盲区，其执行同样必须经过 `PermissionEngine` 与审计日志记录。
