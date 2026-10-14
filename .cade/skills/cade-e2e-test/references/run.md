# exec 配置与命令

## 配置

默认使用已有 Codex 登录。把下面的配置保存为本次目录中的 `run.config.json`。用户指定模型时，同时修改默认模型和辅助角色。

```json
{
  "default_provider": "openai-codex",
  "default_model": "gpt-5.6-luna",
  "default_reasoning_effort": "none",
  "provider": {
    "options": {"thinking": false},
    "model_profiles": {
      "subagent": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "none", "thinking": false},
      "fallback": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "none", "thinking": false},
      "reviewer": {"provider": "openai-codex", "model": "gpt-5.6-luna", "reasoning_effort": "none", "thinking": false}
    }
  },
  "execution_modes": {"default_mode": "build"}
}
```

`thinking: false` 关闭推理摘要；`reasoning_effort: "none"` 关闭推理。测试推理行为时，使用场景指定的设置。

`--config` 替代项目配置，仍会合并全局配置和工作区的 `.cade/settings.json`。检查默认模型和辅助角色的实际选择。只传 `--model` 无法覆盖辅助角色。

## 命令

下面是 Bash 示例。在 Cade 仓库根目录运行。把 `unique-run` 改成本次标识，提前准备工作区、配置、任务文件、会话目录和 `tmp/`。限额按任务调整。

```sh
CADE_E2E_ROOT="$PWD"
CADE_E2E_RUN="$CADE_E2E_ROOT/e2e-results/unique-run"
CADE_E2E_WORKSPACE="$CADE_E2E_RUN/workspace"

TMPDIR="$CADE_E2E_RUN/tmp" \
UV_CACHE_DIR="$CADE_E2E_ROOT/.uv-cache" \
uv run --locked cade exec \
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

## 检查

核对 `config.resolved` 中的 provider、模型、推理设置、模式和审批策略。子代理或 Reviewer 参与时，检查对应请求。

`cade auth status` 只能确认认证状态。真实请求才能确认服务可用。余额不足、认证失效或依赖缺失时，记录错误并停止同条件重试。

保存日志后，独立检查工作区结果。完整流程和报告要求见[技能正文](../SKILL.md)。
