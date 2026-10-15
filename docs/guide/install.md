# Installation and environment setup

Cade is a Python coding agent that runs in a local workspace. Install the runtime dependencies before you start.

## Requirements

- Python 3.12 or later.
- A package manager. [uv](https://github.com/astral-sh/uv) is recommended for installation and virtual environments.
- Optional system tools:
  - `fd` (`fdfind`): the preferred file discovery backend for `glob` and `find`. Without it, Cade uses `rg` or Python.
  - ripgrep (`rg`): the preferred content search backend for `grep` and a fallback for file discovery. Python is available as a fallback.
  - bubblewrap (`bwrap`): required for the default Linux shell sandbox. Cade fails during assembly if `bwrap` is missing; it does not fall back to unsandboxed execution.

### Install system tools

```bash
# 在 Ubuntu 或 Debian 上安装
sudo apt-get update && sudo apt-get install -y fd-find ripgrep bubblewrap

# 在 macOS 上使用 Homebrew 安装
brew install fd ripgrep

# 在 Arch Linux 上安装
sudo pacman -S fd ripgrep bubblewrap
```

## Install Cade

### Install from source

Use a source checkout to develop Cade or use the latest code:

```bash
git clone https://github.com/reddishJade/cade.git
cd cade

# 创建虚拟环境并安装运行时依赖
uv venv
source .venv/bin/activate
uv pip install -e .

# 安装测试和静态检查所需的开发依赖
uv pip install -e ".[dev]"
```

### Install with pip

```bash
pip install cade-agent
```

## Verify installation

Run the following command to check that the CLI is installed and available on `PATH`:

```bash
cade --help
```

The command displays help with startup options such as `--mode`, `--provider`, and `--model`. Next, follow the [quickstart](quickstart.md) to configure a model and start a task.
