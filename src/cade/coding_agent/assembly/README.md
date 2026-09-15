# Cade Coding Agent Assembly — 应用装配工厂

本子包是 `coding_agent` 的内部装配工厂，专注于解决：**如何将分散在运行时各处的基础设施、配置解析、工具注册与安全策略以依赖注入的方式解耦组装。**

---

## 1. 核心装配分工

```
                             resolve_config()
                                    │
       ┌────────────────────────────┼────────────────────────────┐
       ▼                            ▼                            ▼
build_shared_infra()       build_tool_registry()        build_security_rules()
- SessionStore             - Builtin Tools              - PermissionEngine
- MemoryManager            - McpTools                   - Mode Rulesets
- McpRuntimeRegistry       - search_tools               - ShellAnalyzer
- JsonlAuditLogger                  │                            │
       │                            └─────────────┬──────────────┘
       └────────────────────────────┬─────────────┘
                                    ▼
                               build_agent()
                          (装配 CodingAgentHarness)
```

### 模块详细职责
- **配置解析 ([config.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/assembly/config.py))**：`resolve_config` 处理全局、项目级、本地级与环境变量的四层配置覆盖合并，产出不可变的 `ResolvedConfig`。
- **共享基础设施 ([infra.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/assembly/infra.py))**：`build_shared_infra` 构建 `SharedInfra` 实例，初始化会话仓库、BM25 长期记忆管理器、MCP 运行时及审计落盘。
- **安全与权限策略 ([security.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/assembly/security.py))**：
  - `build_shell_from_security`：依据当前系统与配置构造沙箱 Shell。
  - `mode_rulesets_from_runtime_config`：生成 Plan/Build/Act 三种模式下的初始权限规则集。
  - `permission_policy_from_security`：构造路径安全与操作权限策略。
- **工具注册表装配 ([registry.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/assembly/registry.py))**：装配工作区核心工具、MCP 动态工具及工具延迟检索工具（`build_search_tools_tool`）。
- **Agent 与 Hooks 构建 ([agent.py](file:///C:/Users/dwei/workspace/cade/src/cade/coding_agent/assembly/agent.py))**：注入模型 Provider、装配外部 Hooks 运行器并返回最终可执行的 `CodingAgentHarness`。

---

## 2. 架构不变量与设计禁忌

- **工厂与状态分离**：Assembly 模块内仅包含无状态的构建函数（Factory Functions），严禁在模块级别持有任何全局可变状态或单例引用。
- **严格自底向上组装**：依赖只能从基础设施流向应用层，工具注册表不可反向持有 `CodingAgentHarness` 的引用。
