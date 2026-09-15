# CLI、TUI 与 Web 启动参数

## 1. 基础语法

```bash
cade [OPTIONS] [COMMAND]
```

未指定 command 时启动 TUI。可用子命令：

| 子命令 | 作用 |
| --- | --- |
| `tui` | 启动终端 TUI |
| `cli` | 启动 CLI / REPL |
| `login` / `connect` | 选择账户 OAuth 或 API key provider |
| `setup` | 运行 provider 配置向导 |
| `config` | 打开交互式设置浏览器 |
| `web` | 启动 FastAPI + WebSocket 浏览器工作台 |

## 2. 全局参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `-p`, `--prompt TEXT` | 空 | 执行单次 prompt 并退出 |
| `--project-root PATH` | 当前目录 | 指定工作区根目录 |
| `--config PATH` | 空 | 指定运行时配置文件 |
| `--sessions-dir PATH` | `.cade/sessions` | 指定 session 账本目录 |
| `--resume` | 关闭 | 启动历史 session 选择器 |
| `--continue` | 关闭 | 恢复当前项目最近的有意义 session |
| `--session ID` | 空 | 恢复指定 session |

示例：

```bash
cade -p "检查最近修改"
cade --project-root ./backend --continue
cade --project-root ./backend --session 20260825-120000 cli
cade --sessions-dir D:\\cade-sessions tui
```

`--resume` 和 `--continue` 代表两种不同路径：前者打开选择器，后者直接使用当前项目最近会话。`--session` 会校验 session 所属项目。

## 3. `cade login` / `cade connect`

```bash
cade login
# 或
cade connect
```

无参数运行时会显示：

```text
Select authentication method:

 → Sign in with an account
   Sign in with an API key
```

选择账户后进入 Codex/ChatGPT OAuth；选择 API key 后进入 provider 配置向导。也可以使用 `--method browser`、`--method device_code` 或 `--method api_key` 跳过选择。

API key 方式会配置 `main` profile，已有配置可重复运行，不会删除已保存的 OAuth 凭据。当前 `/config` 不编辑 provider；需要使用 `login`、`connect` 或 `setup`。

## 4. `cade setup`

```bash
cade setup
```

向导交互式配置 provider、API key、base URL、模型、thinking 和 reasoning effort。配置写入项目根目录的 `cade.config.json`；用户取消保存时可以使用临时配置运行当前进程。该向导也可以在首次运行后重复使用。

## 5. `cade config`

```bash
cade config
cade config --project-root ./backend
cade config --config ./private-settings.json
```

设置浏览器当前提供执行模式、审批策略、非工作区访问、sandbox mode、sandbox network 和 Shell 等常用设置。每次写入前使用 `CadeRuntimeConfig` 校验。

REPL 中的 `/config` 使用同一组设置定义；TUI 将选择菜单、说明和文本表单嵌入当前输出区域。

## 6. `cade web`

```bash
cade web
cade web --host 127.0.0.1 --port 8787 --open
cade web --project-root ./backend
```

参数：

- `--host`：绑定地址，默认 `127.0.0.1`。
- `--port`：端口，默认 `8787`。
- `--open`：服务启动后打开浏览器。
- `--project-root`：浏览器工作台使用的项目根目录。

浏览器工作台使用 REST 读取状态，使用 `/ws` 接收实时事件和发送任务、取消、审批消息。详细协议位于 [web.md](web.md)。

## 7. CLI 与 TUI 的共同输入

两种终端界面共享 CadeApp、工具注册表、session、权限 gate 和命令注册表：

- 普通文本提交 Agent turn。
- `!` 进入 bash shortcut。
- `@` 进入文件引用。
- `$` 进入技能激活。
- `/` 进入控制命令。
- Tab 补全命令、工具、技能和文件。

CLI 使用 Rich 输出 Markdown；TUI 使用 inline transcript、步骤灯、工具卡片和滚动视口。
