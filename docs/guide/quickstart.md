# 快速上手指南

只需三步，即可让 Cade 在你的项目中跑起来，协助你分析代码、执行重构或编写新功能。

---

## 1. 配置模型与 API Key

Cade 默认优先使用兼容 OpenAI 格式的大语言模型服务（默认内置 profile 支持 DeepSeek 等）。

最快捷的方式是通过环境变量设置你的 API Key：

```bash
# 方式 A：直接设置环境变量（以 DeepSeek 为例）
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

# 方式 B：或者设置标准的 OpenAI 环境变量
export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
# 若使用第三方转发网关或自建服务，可同时指定 BASE_URL
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
```

你也可以直接进入目标项目目录，在项目根目录下创建 `.env` 文件写入上述配置，Cade 启动时会自动读取。

---

## 2. 启动 Cade 交互终端

进入你想要操作的代码仓库根目录，直接输入 `cade` 启动：

```bash
cd /path/to/your/project
cade
```

首次启动时，如果没有检测到已配置的密钥，Cade 会弹出友好的初始化向导（Setup Wizard），引导你选择模型提供商并输入 API Key。

成功进入后，你将看到 Cade 的终端 REPL 界面，底部包含当前工作模式（默认 `act`）、活跃模型与上下文状态。

---

## 3. 完成你的第一个任务

### 场景一：让 Cade 先行调研，不破坏代码（Plan 模式）

如果你想先让 Cade 调研代码结构，输出方案而不随意改动代码：

```text
> /plan 梳理当前项目的认证模块实现，指出存在的安全风险，并输出重构方案
```

在此模式下，Cade 仅能使用只读工具（如 `read_file`、`grep_search`）探索代码，所有方案会规整地写入 `.cade/plans/` 目录中供你查阅。

### 场景二：让 Cade 自动编码并运行测试（Build 模式）

当你确定了需求，想让 Cade 一口气完成编码、修改与测试验证时：

```text
> /build 为 src/utils.py 中的 format_date 函数添加单元测试，并运行 pytest 确保通过
```

在 `build` 模式下，Cade 会自动检索文件、调用 `edit_file` 精确修改，并通过内置的审查机制自动运行 `pytest` 校验效果，无需你反复手动敲回车批准。

### 场景三：随时撤销改动

如果 Cade 修改的结果不符合预期，直接输入：

```text
> /undo
```

Cade 拥有基于快照的文件级回滚能力，一秒钟即可将最近一次工具执行所修改的文件恢复至修改前的状态。

---

## 4. 退出与继续工作

- **退出终端**：输入 `/exit` 或连续按两次 `Ctrl+C`。
- **继续上次未完成的工作**：再次进入目录时运行 `cade`，输入 `/continue` 即可无缝衔接上一轮会话的历史状态。
