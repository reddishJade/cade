# 安装与环境准备

Cade 是一个运行在本地工作区的轻量 Python Coding Agent。在开始使用前，请确保系统已安装必要的运行依赖。

---

## 1. 系统要求与环境依赖

- **Python**：`>= 3.12`
- **包管理器（推荐）**：[`uv`](https://github.com/astral-sh/uv)（极速安装与虚拟环境管理）
- **推荐系统工具**：
  - `fd` (`fdfind`)：可选的 `glob`/`find` 工具优先用它发现文件；未安装时回退到 `rg` 或 Python。
  - `ripgrep` (`rg`)：可选的 `grep` 工具优先用它搜索内容，也是文件发现的回退后端；未安装时可使用 Python 实现。
  - `bubblewrap` (`bwrap`)：仅在 Linux 下生效。用于提供命名空间级的 Shell 执行沙箱隔离。未安装时系统将以无沙箱的直接执行模式运行。

### 在各系统安装系统依赖

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y fd-find ripgrep bubblewrap

# macOS (Homebrew)
brew install fd ripgrep

# Arch Linux
sudo pacman -S fd ripgrep bubblewrap
```

---

## 2. 安装 Cade

### 方式 A：源码克隆与开发安装（推荐）

如果你需要基于最新代码进行开发或体验最新特性：

```bash
git clone https://github.com/reddishJade/cade.git
cd cade

# 创建虚拟环境并安装运行时依赖
uv venv
source .venv/bin/activate
uv pip install -e .

# 若需要运行测试与代码静态检查，可安装 dev 依赖
uv pip install -e ".[dev]"
```

### 方式 B：使用 pip 直接安装

```bash
pip install cade-agent
```

---

## 3. 验证安装

在终端运行以下命令，验证命令行工具已正确安装并处于 PATH 中：

```bash
cade --help
```

如果看到包含 `--mode`、`--provider`、`--model` 等启动选项的帮助界面，说明安装成功。
下一步请参考 [快速上手指南](quickstart.md) 配置模型并开启你的第一个开发任务。
