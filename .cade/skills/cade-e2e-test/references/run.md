# 独立 Codex 验收配置

把此配置保存到本次结果目录的 `run.config.json`。它只覆盖本次默认模型选择；API key 和 Codex 登录凭据按 provider 保存在既有 auth 存储，切换模型不删除凭据。

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "high",
  "provider": {
    "model_profiles": {
      "subagent": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high"},
      "fallback": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high"},
      "reviewer": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "high"}
    }
  },
  "execution_modes": {"default_mode": "build"}
}
```

辅助角色显式指定三项选择，覆盖可能存在的全局角色选择。其他请求选项和连接参数仍会合并，运行前检查实际值；需要清除某个选项时用该字段支持的显式值，例如 `context_window: null`。不要复制认证文件。针对固定 provider 协议的验收使用对应配置，其他角色（如 judge/refiner）按场景显式检查。

## 运行命令

在 Cade 仓库把变量设为本次的绝对路径；所有路径都在该仓库工作路径内。以下只是运行形状，工作区、结果目录、依赖和 `task.md` 必须事先准备。`unique-run` 应替换为未使用过的运行标识。按任务规模调整限制。

```sh
CADE_E2E_ROOT="$PWD"
CADE_E2E_RUN="$CADE_E2E_ROOT/e2e-results/unique-run"
CADE_E2E_WORKSPACE="$CADE_E2E_RUN/workspace"

TMPDIR="$CADE_E2E_RUN/tmp" \
UV_CACHE_DIR="$CADE_E2E_ROOT/.uv-cache" \
uv run cade exec \
  --config "$CADE_E2E_RUN/run.config.json" \
  --project-root "$CADE_E2E_WORKSPACE" \
  --sessions-dir "$CADE_E2E_RUN/sessions" \
  --mode build \
  --approval auto-review \
  --max-steps 20 \
  --max-llm-calls 30 \
  --timeout 5m \
  --event-format jsonl \
  --output-last-message "$CADE_E2E_RUN/answer.md" \
  --prompt-file "$CADE_E2E_RUN/task.md" \
  > "$CADE_E2E_RUN/events.jsonl" \
  2> "$CADE_E2E_RUN/stderr.log"
CADE_E2E_EXIT=$?
printf '%s\n' "$CADE_E2E_EXIT" > "$CADE_E2E_RUN/exit-code.txt"
```

事先创建运行目录内的 `tmp/`，检查 fixture 中是否存在优先级更高的 `.cade/settings.json`。配置测试按场景保留它；普通验收检查它不会切回 DeepSeek。记录环境变量 `CADE_APPROVAL_POLICY` 和实际有效审批设置。

对要求真实拒绝工作路径外写入的场景，只观察被拒绝的尝试；不要通过外层 Agent 在外部路径创建 fixture 或写入结果。

## 配置检查与服务检查

通过 `discover_runtime_config(workspace, config_path)` 检查默认字段和角色覆盖。配置发现不读取认证；`assembly.providers.resolve_model_profiles` 在应用装配时展开有效角色并解析所选 provider 凭据。检查装配结果时仅输出 model、transport、effort、base_url 和凭据是否存在，不打印整个对象。`cade auth status` 可以检查认证类型与状态，不能证明余额或请求成功。

首次调用的 `config.resolved` 应显示指定 provider、模型、`openai_codex`、effort 和 mode；随后检查 provider 错误和工具执行。余额不足时停止重复调用；配置解析或工具列表检查通过不能替代真实服务 E2E。

工作区 `.cade/settings.json` 优先级更高。未指定的全局非模型配置仍可能影响场景，必须检查实际有效值。
