# Cade

Cade 是在你的项目目录中工作的终端编码 Agent：输入任务、查看进度、审批操作，退出后继续上次工作。
运行机制、状态归属和代码入口见 [架构说明](docs/architecture.md)。

## 安装和启动

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv tool install --python 3.12 git+https://github.com/reddishJade/cade.git
cd /path/to/your/project
cade
```

当前命令安装 `main` 开发版；首个版本标签发布后，安装说明将固定到标签。[版本与发布](docs/guide/releases.md) 记录发布规则。

首次启动会引导你选择账户登录或 API key，并配置模型。推荐保存为个人默认配置，以便在其他项目中直接启动；也可以选择仅保存到当前项目。之后使用 `cade login` 更换登录方式，或 `cade setup` 重新配置 provider。[安装与环境准备](docs/guide/install.md) 包含 Linux Shell sandbox 的依赖说明。

输入第一条任务，例如：

```text
阅读 README 和项目入口，用三点说明这个项目如何运行。
```

默认 Act 模式允许读取和搜索；写文件和执行 Shell 时按权限规则申请批准。准备让 Cade 实现修改时，可以输入 `/mode build`；只想先调查方案时，可以输入 `/mode plan`。[模式与权限](docs/guide/modes.md) 解释各模式的自动执行范围和切换规则。

## 每天使用

| 操作 | 使用方式 |
| --- | --- |
| 提交任务 | 输入文本，按 Enter |
| 引用文件 | 输入 `@` 查找项目文件 |
| 查找命令 | 输入 `/`；↑/↓ 选择，Enter 接受，再按 Enter 执行 |
| 输入多行 | Ctrl+J 换行 |
| 任务进行时追加任务 | Enter 排队，当前任务完成后执行 |
| 任务进行时纠偏 | Alt+Enter 把指导送入当前任务 |
| 停止任务 | 输入为空时按 Ctrl+C；有内容时先清空输入 |
| 浏览对话历史 | 滚轮或终端翻页快捷键（常见为 Shift+PageUp/PageDown） |
| 展开思考 / 工具结果 | Ctrl+T / Ctrl+O |
| 执行 Shell | `!git status`，仍经过权限审批 |

欢迎区列出常用操作；补全、审批和任务运行时，输入框下方显示对应提示。Alt+Enter 在一些终端中通过 Esc、Enter 实现；空闲时这个组合用于换行。也可以使用 `/steer 你的指导` 提交纠偏。纠偏在下一次模型请求前生效，已执行的工具结果继续保留。

TUI 支持 `/queue 消息` 显式排队。`/queue steer|followup|interrupt` 可以调整忙时 Enter 的行为。

## 继续上次工作

```bash
cade -c                 # 继续当前项目最近的任务
cade --resume           # 从历史会话中选择
cade --session ID       # 恢复指定会话
```

项目会话列表为空时，`cade -c` 会提示你开始新任务。在界面内也可以使用 `/resume` 选择会话、`/new` 开始新会话。输入 `/exit` 退出，或在空闲且输入为空时，三秒内连续按两次 Ctrl+C。

退出后会显示耗时和可复制的恢复命令。会话默认保存在项目的 `.cade/sessions/`。详细恢复、分支和上下文管理见 [会话说明](docs/guide/sessions.md)。

## 更多入口和配置

```bash
cade exec "检查最近修改"     # 单次任务与自动化
cade web --open              # 浏览器工作台
```

- [第一次任务到继续上次工作](docs/guide/quickstart.md)
- [命令与快捷操作](docs/guide/slash-commands.md) · [启动参数与自动化 exec](docs/guide/cli.md)
- [模型与 provider](docs/guide/providers.md) · [配置](docs/guide/configuration.md)
- [技能](docs/guide/skills.md) · [MCP](docs/guide/mcp.md) · [Hooks](docs/guide/hooks.md)
- [完整使用指南](docs/guide/README.md) · [架构说明](docs/architecture.md)

## 开发与 Python 调用

```bash
git clone https://github.com/reddishJade/cade.git
cd cade
uv sync --extra dev
uv run cade
```

所有包代码位于 `src/cade/`。开发检查和提交规范见 [AGENTS.md](AGENTS.md)。Python 集成可以直接调用 `build_app`；运行时入口和生命周期见 [架构说明](docs/architecture.md)。

```python
from pathlib import Path
from cade.coding_agent.app import build_app

app = build_app(project_root=Path.cwd())
try:
    print(app.ask("列出当前目录的 Python 文件。"))
finally:
    app.close()
```

## License

MIT
