# Xcode Coding Agent — 编码产品装配与特化运行时

`coding_agent` 是 Xcode 核心软件工程能力的产品层，专注于解决：**如何将底层的通用运行时（`harness`）、思考循环（`agent`）与大模型能力（`ai`）装配为一个具备文件编辑、代码检索、Shell 隔离执行、自动审批与三执行模式管控的成熟编码助手。**

---

## 1. 核心分层与装配架构

```
                 CLI / Web Server / External Script
                                │
                                ▼
                       build_app(project_root)
                                │
                        [assembly 子包]
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
  resolve_config()      build_shared_infra()    build_tool_registry()
  (配置合并与发现)       (会话/记忆/MCP/审计)     (工作区文件/Shell/搜索)
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                ▼
                       CodingAgentHarness
                   (注入 NOTE.md 前沿、换窗管理)
                                │
                                ▼
                      Agent Loop (思考循环)
```

### 核心能力与组件
- **统一应用门面 ([app.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/app.py))**：定义 `XcodeApp` 数据类与装配工厂 `build_app()`，向上层消费方隐藏复杂的底层注入流程。
- **特化运行时 ([harness.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/harness.py))**：`CodingAgentHarness` 继承自 `AgentHarness`，增加对项目根目录 `NOTE.md`（短程执行前沿）的生命周期注入、多轮换窗边界处理及自动审查模型（Auto Reviewer）挂接。
- **三执行模式控制 ([execution_modes.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/execution_modes.py))**：
  - `plan`：只读模式，阻断一切文件修改与写操作 Shell。
  - `build`：自主构建模式，执行写操作时由独立的 Reviewer 模型非交互式自动审批。
  - `act`：人机协同模式，遇需审批动作中断并主动向用户请示。
- **工具注册表装配 ([registry.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/registry.py))**：提供 `build_project_scoped_registry`，组装工作区读写、搜索、Shell、子代理与动态 MCP 工具。

---

## 2. 架构不变量与设计禁忌

- **产品层定位**：本层是产品逻辑装配层，依赖 `harness`、`agent`、`ai`，向外输出供 `cli`、`server` 使用的 `XcodeApp`；严禁 `harness` 反向依赖 `coding_agent`。
- **只读前置校验**：所有写操作与编辑工具必须遵守工作区路径限制，严禁在未经过 `PermissionEngine` 决策前直接触发操作系统落盘。
- **短程与长程记忆解耦**：当前正在进行的任务步骤必须通过 `NOTE.md` 维护，长期跨会话事实通过 `MEMORY.md` 沉淀，会话细节无损留存于 Session 账本中。

---

## 3. 典型使用示例

```python
from pathlib import Path
from xcode.coding_agent.app import build_app

# 为当前项目装配完整 Coding Agent 应用
app = build_app(project_root=Path.cwd())

# 发起单轮或多轮提问
answer = app.ask("列出当前目录中所有 Python 模块并检查代码规范。")
print(answer)

# 安全关闭并持久化状态
app.close()
```

---

## 4. 子模块分工

- **[assembly/](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/assembly/README.md)**：工厂子包，细粒度装配配置、基础设施、安全规则与工具集。
- **[prompting/](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/prompting/README.md)**：系统身份提示词（`CORE_IDENTITY`）与版本规范。
- **[tools/](file:///C:/Users/dwei/workspace/xcode/src/xcode/coding_agent/tools/README.md)**：全套内置编码工具的实现（文件、检索、Shell、子代理等）。
