# 配置解析与装配核对（2026-10-06）

审查基线：`22429ab9d97241178668b02937b570675db88203`（当时 main）。
对照提交：`c981eb355b000f2ca5dd6831f9b8cc2d7289ee3e`。
证据来自当前解析代码与调用链；宿主行为尚未实测。

## 已核对的契约

- `discover_runtime_config`：内置默认值 < 全局 settings < 项目配置或
  `--config` 指定文件 < 工作区 settings < 支持的环境覆盖。
  当前环境覆盖仅包含 `CADE_APPROVAL_POLICY`。
- 合并以原始字典中存在的键为准；对象递归合并，显式 null/false 保留。
  空角色对象不会清除低优先级角色字段。
- 模型发现不读取认证。`resolve_model_profiles` 在应用装配时展开角色，
  再按 provider 查认证。角色只覆盖自身明确提供的字段；provider/model 的
  null 或空字符串通过 `or` 回退到默认选择；effort 的 null 会实际清除 effort。
- subagent/fallback 缺省复用主配置；reviewer/judge/refiner 缺省由
  provider bundle 补齐。`--model` 在 exec 装配前修改默认选择，
  显式配置的角色字段仍保留，未显式字段继承覆盖后的主配置。
- `build_app` 将 subagent、fallback、reviewer 分别连接到子代理管理器、
  主 composition 的 fallback 和 AutoApprovalReviewer。
- 普通 `cade` 与 `cade tui` 均经 main._run 进入 run_tui。
  当前没有独立的交互式 CLI/REPL 宿主。exec 经 prepare_exec_config
  后也调用同一个 build_app。
- OAuth login 仅写 auth；API login 的 from_connect 路径还合并全局
  provider connection，但不写默认 provider/model/effort 和角色。
  setup 是另一条主动选择默认模型的路径。
- auth 以 provider 为键保存。OpenAI API 为 `openai`，Codex OAuth 为
  `openai-codex`，二者可并存；同一 provider 的重复保存会替换该项。

## 缺口（按风险）

### [high] 自定义 provider 可能借用其他 provider 的环境 key

`assembly/providers.py::resolve_model_profile` 将 transport 传给
`registry.py::get_environment_api_key`。自定义 acme 连接使用
openai_responses 时，未声明 api_key_env、未保存 acme auth，
仍会读取 OPENAI_API_KEY，并将其装配到 acme 的 base_url。
这违反文档声明的 provider 凭据隔离，可把官方 API key 发往另一个端点。

最小方向：内置 provider 采用自身环境变量；自定义身份必须使用该身份的
auth 或显式 api_key_env。相同协议不构成凭据授权。预检和装配一起修改。

### [high] TUI 文本 /model 与 exec 的 provider 推断不同

`modes/tui/settings.py::handle_model_command` 不传当前 transport，
直接解析裸 GPT 模型名，并显式将得到的 openai_responses 传给 set_model。
Codex 会话输入 `/model gpt-5.6-luna` 会选 OpenAI API。
`exec_mode.py::prepare_exec_config` 传当前连接作为 fallback，
同样的裸模型名在 Codex 默认下保持 openai-codex。
可能导致意外使用 API 额度，或在只有 OAuth 的用户处切换失败。

最小方向：统一当前 provider/连接的解析规则；在无显式 provider 或 transport
时保留当前 Codex 的 GPT 路径。模型选择器显式 provider 路径也需要保留。

### [high] API 登录接受环境默认值会保存截断 key

`modes/tui/setup_wizard.py::_prompt_api_key` 把 env_val[:16]
作为真实输入 default。用户接受默认值时，保存的 key 只有前 16 个字符。
原本可用的环境 key 也会被 auth 中的错误 key 遮蔽。

最小方向：完整保留用户接受的既有 key；使用遮罩输入或明确的复用选择，
显示遮罩与实际凭据分开。新增测试使用合成 key。

### [medium] 交互入口的认证预检与真实装配读取不同的 .env 集合

`interaction/credentials.py::has_valid_config` 只读 workspace/.env
和 workspace/cade/.env。`assembly/config.py::resolve_config` 还先读
src/cade/.env。只有包目录 .env 中有 API key 时，exec 可装配，
普通 cade/TUI 却在预检处报无凭据或进入向导。
两处都有 key 时，包目录的值还会先于工作区值被采用。

最小方向：共用环境文件发现及优先级，预检与装配保持一致。

### [medium] TUI /config 忽略启动 --config

`modes/tui/app.py::_open_config_browser` 固定读取并写入
project_root/cade.config.json，尽管 TUI 已保存启动的 _config_path。
`cli/config_cmd.py::handle_config_command` 则尊重 args.config。
使用独立 --config 启动后，TUI /config 会编辑本次启动不读取的文件。

最小方向：TUI 浏览器使用显式 config_path，缺省时才选择项目配置。

## 生命周期范围

启动时的字段继承与运行时 /model 切换应分开验收。
`CadeApp.set_model` 仅替换 primary；只在未配置 reviewer 时同步 reviewer，
不会重新展开 subagent/fallback 或部分 reviewer 的继承字段。
例如 reviewer:{} 会在启动时继承 main，但随后不随主模型切换；
fallback 也仍为原来的模型。文档主要明确启动继承，未明确动态同步契约，
此处作为待明确的生命周期边界，不将新增同步行为直接写入补丁。

## 最小回归范围

新增 `src/cade/tests/test_provider_configuration.py` 使用真实配置文件、
真实 AuthStore/AuthManager 和生产解析函数，控制交互输入。
不调用远端模型、不替换整个 ModelProvider，属于配置/凭据边界回归，
不证明 TUI、exec 或 provider 协议 E2E。

| 用例 | 独立期望 |
|---|---|
| 全局 + 独立 config + 工作区 | 独立 config 替换项目文件；角色字段跨层保留；工作区显式默认值胜出 |
| 角色缺省与 null/false | subagent/fallback 继承；reviewer null effort/window 与 false thinking 生效 |
| exec --model | 主模型和缺省角色切换；显式 reviewer 保留；输入配置对象不变 |
| API login | 默认三项和角色不变；既有 OAuth 保留；key 只进入 auth |
| API/OAuth 并存 | openai-codex 使用 OAuth，openai 使用 API key，不带 OAuth account_id |
| 自定义 provider 隔离 | 缺少自身 auth/env 声明时拒绝装配，不能借 OPENAI_API_KEY |
| 接受既有环境 key | 完整 key 写入 auth |

最后两项暂用 strict xfail 标记已知缺陷；只接受断言失败，
导入、类型或其他运行错误仍会失败。修复生产逻辑后移除对应标记。
不得将 xfail 报为通过。

运行：

```sh
uv run --locked pytest -q -rxX src/cade/tests/test_provider_configuration.py
uv run --locked ruff check src/
uv run --locked ruff format --check src/
uv run --locked pyright src/
uv run --locked cade --help
```

所有测试管理的文件保存在仓库 e2e-results 下；
认证只使用合成值，隔离 HOME 和 auth 默认路径，不读取真实用户凭据。

补丁不修改生产代码。当前审查环境只提供 GitHub 读写工具，
没有终端或真实 Cade 宿主，以上命令尚未在本地执行。
CI 的结果单独报告；无论 CI 是否通过都不代表真实 E2E 已通过。

## 仍需真实宿主验收

依据仓库 `cade-e2e-test` skill，在工作区内创建独立 run 目录，
使用已有凭据、专用 sessions、显式四角色配置，不复制 auth。

1. 同一配置分别启动普通 cade、cade tui 和 cade exec；
   检查 main 与实际子代理/Reviewer 请求的 model、transport、effort 和端点。
   exec 模型调用成功不能证明两个交互入口的实际行为。
2. Codex 默认下分别测试 exec --model 裸 GPT 名、TUI 文本 /model 裸 GPT 名、
   显式 openai-codex/model、显式 openai/model，观察实际请求身份。
3. 在独立 --config 启动的 TUI 中编辑 /config，独立检查写入目标及重启结果。
4. 仅在包目录 .env 中配置合成测试服务 key，对照交互预检与 exec 装配；
   使用受控服务，不为此修改既有真实账号凭据。
5. 用受控服务使主模型返回可恢复失败，检查真实 fallback 请求；
   分别保留显式角色、缺省角色与主模型切换前后的证据。
