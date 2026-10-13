# CLI 命令行参数与交互指南

Cade 提供了开箱即用的命令行工具 `cade`。本节汇总启动参数、终端快捷键与高级输入技巧。

---

## 1. 命令行启动参数

```text
用法: cade [选项] [子命令]
```

### 常用全局选项

| 选项参数 | 类型 | 说明 |
| :--- | :--- | :--- |
| `--project-root <path>` | path | 工作区根，默认当前目录 |
| `--config <path>` | path | 替代项目配置文件 |
| `--sessions-dir <path>` | path | 覆盖本次会话目录 |
| `-c, --continue` | flag | 恢复当前项目最近会话 |
| `--session <id>` | string | 恢复指定会话 |
| `--resume` | flag | 启动 TUI 会话选择器 |
| `-h, --help` | flag | 打印命令行帮助信息 |

三个公共路径参数可以放在子命令前后，`session` 查询还可放在动作后；重复指定时
最后的显式值生效。会话目录优先使用 CLI 覆盖，再使用配置中的 `paths.sessions_dir`，
未配置时使用工作区 `.cade/sessions/`。

### 命令行常用场景

```bash
# 1. 默认交互式启动
cade

# 2. 在指定工作区启动 TUI
cade tui --project-root ./repo

# 3. 在 CI 或脚本中非交互式执行单条任务（Headless）
cade exec "运行 pytest 并修复所有失败的测试用例"
```

`exec` 的模型、模式、审批与运行上限由子命令选项覆盖，见 `cade exec --help`。

---

## 2. 终端交互快捷键与技巧

在交互终端（TUI）中，Cade 提供了丰富的操作便利：

### 2.1 快捷键清单
- **多行输入**：按 `Shift+Enter` 进行换行。如果你的终端未正确映射该快捷键，可使用 `Esc` 然后按 `Enter` 作为通用后备换行方案。
- **补全机制 (`Tab`)**：
  - 自动补全 Slash 命令（输入 `/` 后按 `Tab`）；
  - 自动补全工具名称（输入 `/tool ` 后按 `Tab`）；
  - 自动补全文件路径（输入 `@` 触发文件补全）。
- **打断与退出 (`Ctrl+C`)**：
  - **单次按下**：秒级取消当前正在进行的模型流式推理或后台长命令执行；
  - **连续按两次**：安全退出 Cade 终端。

### 2.2 高级输入魔法

1. **`@` 文件直接引用**：
   在提示词中输入 `@` 即可模糊补全并引用工作区中的文件：
   ```text
   > 请参考 @src/cade/main.py 的入参处理，为 @src/cade/coding_agent/interaction/commands.py 补充对应选项
   ```
   Cade 会在发送请求时自动把引用的文件内容作为背景上下文呈递给模型。

2. **`!` 快速执行本地命令**：
   如果只想在不打断对话的情况下快速看一下本地状态，可以在行首加 `!`：
   ```text
   > !git status
   > !pytest src/tests/test_harness.py
   ```
   命令输出将直接呈现在终端中。
