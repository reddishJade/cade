# 安装与环境准备

Cade 需要 Python 3.12 或更高版本。运行时依赖 OpenAI-compatible provider、终端交互、文件处理、MCP、FastAPI Web 服务和 Linux bubblewrap sandbox。

## 快速安装

```bash
uv tool install --python 3.12 git+https://github.com/reddishJade/cade.git
cd /path/to/your/project
cade
```

当前命令安装 `main` 开发版。首个版本标签发布后，安装说明将改为固定版本；发布规则见[版本与发布](releases.md)。

首次启动自动引导账户登录或 API key 配置。完成后即可输入任务，下一次使用 `cade -c` 继续。[快速开始](quickstart.md) 介绍完整操作路径。

## 1. 前置条件

- **Python**：3.12+。
- **包管理器**：推荐 [uv](https://docs.astral.sh/uv/)，pip 也可用。
- **Git**：项目内文件快照、`/undo` 和 Git 上下文需要 Git 工程。
- **Linux sandbox**：Linux 默认使用 `bwrap`。需要时安装：

  ```bash
  # Debian / Ubuntu
  sudo apt-get update && sudo apt-get install -y bubblewrap

  # Fedora / RHEL
  sudo dnf install -y bubblewrap

  # Arch Linux
  sudo pacman -S bubblewrap
  ```

  Linux 中使用默认 `workspace-write` sandbox 时，缺少 `bwrap` 会让 sandbox 初始化失败。可以安装 bubblewrap；在明确理解风险后，也可以同时配置 `danger-full-access` 与 `network_access: allow`，让运行时使用本地 subprocess shell。

Linux 以外的环境使用本地 `SubprocessShell`；工具权限和路径策略仍然生效。

## 2. 开发模式安装

```bash
git clone https://github.com/reddishJade/cade.git
cd cade

uv venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

uv pip install -e .
```

开发依赖：

```bash
uv pip install -e ".[dev]"
```

也可以直接使用：

```bash
uv run cade --help
```

## 3. 首次配置

交互式向导：

```bash
cade setup
```

向导会收集 provider、API key、base URL、模型、thinking 和可用的 reasoning effort，保存时可选择个人默认配置 `~/.cade/settings.json`、当前项目的 `cade.config.json` 或临时配置。个人默认配置便于多个项目共用。

也可以使用环境变量。常用 key 包括：

```bash
export OPENAI_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export MIMO_API_KEY="..."
export CHATGLM_API_KEY="..."
```

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY = "..."
```

API key 也可以写入 provider profile。敏感配置适合放在个人配置或环境变量中，并结合 [security.md](security.md) 的路径和审计策略使用。

## 4. 验证安装

```bash
cade --help
cade
# 完成首次配置后，也可以执行单次任务
cade -p "输出一句安装成功"
```

若执行 `cade` 没有指定子命令，程序启动终端 TUI。标准 REPL 使用 `cade cli`，浏览器工作台使用 `cade web`。

## 5. 运行目录

启动时默认以当前目录作为项目根目录。可以显式指定：

```bash
cade --project-root /path/to/project
cade --project-root D:\\work\\project cli
```

会话、快照、MCP、技能和项目记忆都会依据这个项目根目录建立各自的运行边界。
