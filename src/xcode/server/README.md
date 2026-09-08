# Xcode Server — 浏览器工作台服务

`server` 模块为 Xcode 提供本地 Web 可视化交互工作台，专注于解决：**如何通过 FastAPI 与 WebSocket 将 Agent 的结构化事件流（思考折叠、工具执行、步骤卡片与人机审批）低延迟地投射到浏览器界面，并支持全双工的实时交互。**

---

## 1. 核心架构与通信模型

```
       前端浏览器 (Web UI)
              │
              ├─────── REST API (查询会话 / 配置 / 工作区树) ─────┐
              │                                                │
              │                                                ▼
              ├─────── WebSocket /ws (双向实时流) ──────► FastAPI (api.py)
              │       - 推送 ReasoningDelta / ToolCallEvent    │
              │       - 推送 步骤状态与 Token 统计              ▼
              │       ◄─── 提交用户指令 / HITL 审批裁决 ─── TaskRunner (runner.py)
              │                                                │
              │                                                ▼
              └────────────────────────────────────────── XcodeApp 实例
```

### 核心模块职责
- **FastAPI 路由与 WebSocket 处理器 ([api.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/server/api.py))**：
  - REST 端点：会话列表检索、单会话事件回放、文件树结构读取、配置修改；
  - WebSocket `/ws`：维持客户端长连接，双向传输事件与指令。
- **后台异步调度 ([runner.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/server/runner.py))**：将 Web 端的异步请求安全派发给 `XcodeApp`，管理当前任务的取消、排队与状态广播。
- **服务启动器 ([serve.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/server/serve.py))**：`run_web_server` 封装 Uvicorn 启动逻辑，支持端口绑定、工作区动态重载与启动后自动打开浏览器（`--open`）。
- **事件序列化 ([serialize.py](file:///C:/Users/dwei/workspace/xcode/src/xcode/server/serialize.py))**：将 `AgentHarnessEvent` 及领域对象转为 Web 端消费的标准 JSON。

---

## 2. 架构不变量与设计禁忌

- **本地安全绑定**：默认仅监听本地回环地址（`127.0.0.1`），严禁在无鉴权环境下默认绑定 `0.0.0.0`，杜绝未授权远程代码执行风险。
- **与 CLI 对等表现**：Web 端与 CLI REPL/TUI 处于同一表现层级，均通过 `build_app()` 统一接入 `coding_agent`，享有完全一致的权限管控与审计规则。

---

## 3. 子模块分工

- **[static/](file:///C:/Users/dwei/workspace/xcode/src/xcode/server/static/README.md)**：包含原生单页面前端资产（HTML/CSS/JS），无第三方打包构建依赖。
