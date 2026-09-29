# 外部生命周期 Hooks

Cade 提供了灵活的外部生命周期事件钩子（Hooks）系统。通过 Hooks，你可以在工具执行前后、模型请求发出前或上下文换窗时，自动触发你自定义的 Shell 脚本或监控程序，实现自动化告警、格式化代码或审计上报。

---

## 1. 支持的生命周期事件

| 钩子事件名称 | 触发时机 | 典型用途 |
| :--- | :--- | :--- |
| **`before_agent_start`** | 每次会话启动或用户发送新指令开始前 | 检查前置依赖、拉取最新 Git 变更 |
| **`before_provider_request`**| 组装完 Prompt 即将发送给大模型 API 时 | 记录 Token 消耗预估、本地日志留存 |
| **`pre_tool`** | 工具准备执行、但尚未执行前 | 记录安全审计事件、临时备份 |
| **`post_tool`** | 工具执行完成并获得输出后 | 自动触发代码格式化（如 `ruff format`）、运行快速语法校验 |
| **`on_context_window_reset`**| 上下文达到 95% 水位线触发换窗重置时 | 同步备份 `NOTE.md`、向团队发送长任务进展通知 |
| **`on_error`** | 发生底层非预期异常或网络致命错误时 | 触发系统告警、记录排障 Crash Dump |

---

## 2. 配置 Hooks

在 `.cade/config.json` 中添加 `hooks` 配置块：

```json
{
  "hooks": {
    "post_tool": [
      {
        "command": "ruff format src/",
        "failure_policy": "warn"
      }
    ],
    "on_context_window_reset": [
      {
        "command": "git add NOTE.md && git commit -m 'chore: checkpoint NOTE.md' || true",
        "failure_policy": "ignore"
      }
    ]
  }
}
```

### 字段说明
- `command`：触发时执行的 Shell 命令行。
- `failure_policy`：当钩子命令执行失败时的处理策略：
  - `ignore`：静默忽略错误，不影响 Agent 继续运行；
  - `warn`：在终端打印黄色警告，但允许 Agent 继续推进；
  - `fail`：将钩子失败视为硬错误，终止当前工具或指令。

---

## 3. 在终端中查看 Hooks

运行 `/hooks` 命令即可在终端中实时查看当前工作区所激活的全部事件钩子列表。
