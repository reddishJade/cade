# Cade Harness — 通用 Agent 运行时与基础设施

Harness 层是 Cade 的通用运行时基础设施中枢，专注于解决：**如何为各类 Agent 产品（如 Coding Agent、数据分析 Agent）提供领域无关的会话账本、安全权限网关、无损上下文换窗、沙箱执行、外部协议扩展（MCP/Skills）与审计可观测性。**

---

## 1. 核心架构与数据流

Harness 层连接了底层的 `agent` 循环与上层的具体领域产品，提供运行期所需的全部基础设施服务：

```
                    ┌─────────────────────────┐
                    │      Agent Product      │
                    │ (如 coding_agent.app)   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                     AgentHarness (agent_runtime)
                                 │
       ┌─────────────────────────┼─────────────────────────┐
       ▼                         ▼                         ▼
 [执行与安全]               [会话与记忆]               [协议与扩展]
- execution_env (沙箱/FS)   - session (事件账本/树)    - mcp (MCP 客户端)
- security (权限引擎/规则)   - memory (长期事实/BM25)  - skills (两阶段发现)
- observability (审计/Hook)
```

### 核心子包分工
| 子目录 | 核心职责 |
|---|---|
| **[agent_runtime/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/agent_runtime/README.md)** | `AgentHarness` 基类、工具门控拦截（ToolGate）、上下文换窗与并发运行控制器 |
| **[security/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/security/README.md)** | 统一权限引擎（PermissionEngine）、Shell 语法语义分析、HITL 与独立模型自动审批 |
| **[session/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/session/README.md)** | 追加写事件流持久化账本（SessionStore）、多分支会话分叉树与无损历史检索 |
| **[execution_env/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/execution_env/README.md)** | 文件系统抽象与基于 Bubblewrap 的 Linux 命名空间隔离命令沙箱 |
| **[observability/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/observability/README.md)** | JSONL 结构化审计日志、敏感数据自动脱敏、链路追踪与内部/外部生命周期 Hooks |
| **[mcp/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/mcp/README.md)** | 基于 Stdio 的 Model Context Protocol 协议集成与动态工具注册 |
| **[memory/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/memory/README.md)** | 项目级与用户级 Markdown 长期事实持久化与 BM25 检索支持 |
| **[skills/](file:///C:/Users/dwei/workspace/cade/src/cade/harness/skills/README.md)** | 技能自动发现、`SKILL.md` 解析与两阶段轻量注入/按需激活机制 |

---

## 2. 架构不变量与设计禁忌

- **领域无关性（Domain Agnostic）**：
  - 本层严禁反向依赖具体的业务产品层（如 `cade.coding_agent`）。编码领域特化逻辑（如 `NOTE.md` 前沿状态管理）必须在产品层子类中实现。
- **正交分权原则**：
  - `security` 模块专注于权限裁决与规则分析，严禁直接依赖 `observability` 的具体实现；
  - `observability` 模块专注于事件追踪与审计记录，严禁重新导出（re-export）权限类型。
- **事实源唯一性**：
  - `session` 中的追加写事件日志是系统运行状态与历史的最终事实源，长期记忆和 UI 投影均必须能从事件账本中无损重建。
