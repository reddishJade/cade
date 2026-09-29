# 配置系统完全手册

Cade 支持分层配置系统。所有配置均由强类型的 Pydantic 模型校验，杜绝非法或过期的字段。

---

## 1. 配置加载优先级

当 Cade 启动时，配置按照以下从高到低的优先级合并覆盖：

1. **CLI 命令行参数**（最高，如 `cade --mode build --model deepseek-v4-pro`）
2. **环境变量**（当前 Shell 环境变量与项目根目录 `.env` 文件）
3. **工作区本地配置**（`<项目根目录>/.cade/settings.json`）
4. **项目级配置文件**（`<项目根目录>/cade.config.json`）
5. **用户全局配置文件**（`~/.cade/settings.json`）
6. **系统默认内置参数**（最低，默认模型 `deepseek-flash`）

---

## 2. 核心配置字段全景表

以下是完整的配置结构及默认值说明（对应 `.cade/config.json`）：

```json
{
  "provider": {
    "model_profiles": {
      "main": {
        "transport": "openai_chat",
        "chat_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "context_window": null,
        "thinking": true,
        "reasoning_effort": "high"
      },
      "subagent": {
        "transport": "openai_chat",
        "chat_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "thinking": false
      },
      "fallback": {
        "transport": "openai_chat",
        "chat_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "api_key": ""
      }
    }
  },
  "agent": {
    "rollover_trigger_ratio": 0.95,
    "reserve_tokens": 16384,
    "tool_workers": 4,
    "tool_timeout_seconds": 120.0,
    "watchdog_repeated_tool_limit": 3
  },
  "sandbox": {
    "mode": "workspace_write",
    "network_access": "deny"
  },
  "execution_modes": {
    "default_mode": "act"
  }
}
```

### 关键参数说明

| 配置路径 | 类型 | 默认值 | 详细含义与建议 |
| :--- | :--- | :--- | :--- |
| `agent.rollover_trigger_ratio` | float | `0.95` | **上下文换窗水位线**。当 Token 消耗占模型总容量的 95% 时触发换窗，保护长任务不超限。 |
| `agent.reserve_tokens` | int | `16384` | 预留给模型生成回答与工具调用的安全 Token 空间。 |
| `agent.tool_workers` | int | `4` | 只读工具（读取文件、搜代码、搜网络）的**最大并发线程数**。 |
| `agent.tool_timeout_seconds` | float | `120.0` | 单个工具（特别是 Bash 命令）的最大超时时长（秒）。 |
| `sandbox.mode` | string | `"workspace_write"` | Linux 沙箱模式：`workspace_write`（宿主只读、工作区可写）、`off`（禁用沙箱）。 |
| `sandbox.network_access` | string | `"deny"` | 沙箱内网络访问权限：`deny`（断网隔离）、`allow`（允许联网拉取包）。 |
| `execution_modes.default_mode`| string | `"act"` | 默认工作模式，可选 `act`（人工审批）、`build`（自动改代码）、`plan`（只读规划）。 |

---

## 3. 环境变量对照表

你可以不写任何 JSON 配置文件，仅通过环境变量快速注入配置：

| 环境变量 | 作用与示例 |
| :--- | :--- |
| `OPENAI_API_KEY` | 主模型 API Key |
| `OPENAI_BASE_URL` | 自定义兼容 OpenAI 协议的网关地址（如 `https://api.deepseek.com/v1`） |
| `DEEPSEEK_API_KEY` | DeepSeek 官方专属 API Key |
| `CADE_MODE` | 默认执行模式（`act` / `build` / `plan`） |
| `CADE_MODEL` | 覆盖启动时默认使用的模型名称 |

---

## 4. 交互式查看与修改设置

在 Cade 终端运行期间，你可以直接输入：

```bash
/config
```

即可打开终端交互式设置浏览器，查看每一项配置的实时生效值，并直接在终端内完成热修改。
