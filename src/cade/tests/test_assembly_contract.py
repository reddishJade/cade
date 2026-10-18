from __future__ import annotations

import tempfile
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest

from cade.agent._codec import convert_to_llm
from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextCollectionInput,
    ContextCollectorRegistry,
    ContextPriority,
    ContextSection,
    FrozenContextCollectorRegistry,
    make_state_section,
)
from cade.agent.messages import ContextMessage
from cade.agent.request import DefaultRequestAssembler
from cade.ai.events import (
    FinalMessage,
    Message,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.providers.base import Provider
from cade.ai.providers.registry import ModelProfileConfig, ProviderBundle
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.coding_agent import app as app_module
from cade.coding_agent.app import build_app
from cade.coding_agent.assembly import registry as registry_module
from cade.coding_agent.assembly.agent import build_hook_manager
from cade.coding_agent.assembly.security import (
    external_directories_from_security,
    mode_rulesets_from_runtime_config,
    permission_policy_from_security,
    sandbox_policy_from_security,
    sensitive_path_overrides_from_security,
)
from cade.harness.agent_runtime import AgentComposition
from cade.harness.agent_runtime.config import (
    AgentRuntimeConfig,
    GateConfig,
    GateRuntimeConfig,
)
from cade.harness.agent_runtime.harness import AgentHarness
from cade.harness.config import (
    CadeRuntimeConfig,
    ExecutionModesRuntimeConfig,
    ModeRuleRuntimeConfig,
    ModeRulesetRuntimeConfig,
    SandboxMode,
    SandboxRuntimeConfig,
    SecurityExternalDirectory,
    SecurityRuntimeConfig,
    SecuritySensitivePathOverride,
)
from cade.harness.execution_env import ExecutionResult, Shell, SubprocessShell
from cade.harness.security import Action, StaticPolicyEvaluator, Target
from cade.harness.session import SessionInbox, SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.surface import project_session_surface


class TestAssemblySecurityContract:
    """验证装配层对安全策略、权限展开、沙箱边界及运行模式的组装不变量。"""

    def test_permission_policy_group_expansion_and_concrete_tool_override(self) -> None:
        """测试用户级权限名展开为具体工具，且具体工具配置具备更高优先级（后定义生效）。"""
        security = SecurityRuntimeConfig(
            permissions={"read": "allow"},
            tools={"ls": "deny"},
        )
        policy = permission_policy_from_security(security)
        assert policy is not None

        # 验证 policy.rules 包含 read 组展开的所有工具，且 ls 排在最后覆盖
        tool_names = [r.tool for r in policy.rules]
        assert "read" in tool_names
        assert "glob" in tool_names
        assert "grep" in tool_names
        assert "find" in tool_names
        assert "ls" in tool_names

        # 使用 StaticPolicyEvaluator 评估：验证 last-match-wins 契约
        evaluator = StaticPolicyEvaluator(
            policy.rules, global_default=policy.global_default
        )

        # 1. 组权限展开：read 应该为 allow
        action_read = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value="test.txt", access="read"),),
            input={"path": "test.txt"},
        )
        constraints_read = evaluator.evaluate(action_read)
        assert len(constraints_read) == 1
        assert constraints_read[0].decision == "allow"

        # 2. 具体工具覆盖：ls 虽属于 read 组，但因 tools 显式指定 deny 而排在后面生效
        action_ls = Action(
            tool="ls",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value=".", access="read"),),
            input={"path": "."},
        )
        constraints_ls = evaluator.evaluate(action_ls)
        assert len(constraints_ls) == 1
        assert constraints_ls[0].decision == "deny"

    def test_external_directories_non_workspace_access_toggle(
        self, tmp_path: Path
    ) -> None:
        """测试 non_workspace_access 开关控制是否采用用户配置的外部目录。"""
        ext_path = tmp_path / "shared"
        ext_path.mkdir()

        # 1. 关闭 non_workspace_access 时，用户自定义外部目录被完全忽略
        disabled_sec = SecurityRuntimeConfig(
            non_workspace_access=False,
            external_directories=(
                SecurityExternalDirectory(path=str(ext_path), access="read"),
            ),
        )
        dirs_disabled = external_directories_from_security(disabled_sec)
        assert not any(d.path == ext_path.resolve() for d in dirs_disabled)

        # 2. 开启 non_workspace_access 时，用户自定义外部目录正常包含
        enabled_sec = SecurityRuntimeConfig(
            non_workspace_access=True,
            external_directories=(
                SecurityExternalDirectory(path=str(ext_path), access="read"),
            ),
        )
        dirs_enabled = external_directories_from_security(enabled_sec)
        assert any(
            d.path == ext_path.resolve() and d.access == "read" for d in dirs_enabled
        )

    def test_mode_rulesets_from_runtime_config(self) -> None:
        """测试各执行模式（plan, build, act）的 ruleset 转换与集合属性保留。"""
        plan_rule = ModeRuleRuntimeConfig(
            action="bash",
            effect="deny",
            command="rm",
            subcommand_in=("rf", "fr"),
            flags_any=("-f", "--force"),
        )
        cfg = CadeRuntimeConfig(
            execution_modes=ExecutionModesRuntimeConfig(
                plan=ModeRulesetRuntimeConfig(rules=(plan_rule,)),
            )
        )
        rulesets = mode_rulesets_from_runtime_config(cfg)
        assert "plan" in rulesets
        assert len(rulesets["plan"]) == 1
        r = rulesets["plan"][0]
        assert r.action == "bash"
        assert r.effect == "deny"
        assert r.command == "rm"
        assert r.subcommand_in == {"rf", "fr"}
        assert r.flags_any == {"-f", "--force"}

    def test_sandbox_policy_from_security_writable_roots(self, tmp_path: Path) -> None:
        """测试沙箱模式提取可写目录：仅将可写外部目录与系统临时目录纳入 writable_roots。"""
        writable_ext = tmp_path / "writable_ext"
        writable_ext.mkdir()
        readonly_ext = tmp_path / "readonly_ext"
        readonly_ext.mkdir()

        security = SecurityRuntimeConfig(
            non_workspace_access=True,
            sandbox=SandboxRuntimeConfig(mode=SandboxMode.WORKSPACE_WRITE),
            external_directories=(
                SecurityExternalDirectory(path=str(writable_ext), access="write"),
                SecurityExternalDirectory(path=str(readonly_ext), access="read"),
            ),
        )
        policy = sandbox_policy_from_security(tmp_path, security)
        assert policy.mode == SandboxMode.WORKSPACE_WRITE
        tempdir = Path(tempfile.gettempdir())
        if tempdir.is_dir():
            assert tempdir in policy.writable_roots
        assert writable_ext.resolve() in policy.writable_roots
        assert readonly_ext.resolve() not in policy.writable_roots

    def test_sensitive_path_overrides_normalization(self, tmp_path: Path) -> None:
        """测试相对路径的敏感路径例外根据 project_root 解析为绝对路径。"""
        sec = SecurityRuntimeConfig(
            sensitive_path_overrides=(
                SecuritySensitivePathOverride(path=".env.local", access="read"),
            ),
        )
        overrides = sensitive_path_overrides_from_security(sec, tmp_path)
        assert len(overrides) == 1
        assert overrides[0].path == (tmp_path / ".env.local").resolve()
        assert overrides[0].access == "read"


class _WorkingDirectoryProvider(Provider):
    def __init__(self, *, call_bash: bool) -> None:
        super().__init__()
        self._model = "gpt-4o-mini"
        self._base_url = "https://provider.invalid"
        self._transport = "openai_chat"
        self._thinking = False
        self._context_window = 128_000
        self.call_bash = call_bash
        self.requests: list[tuple[list[Message], list[ToolDefinition]]] = []

    @property
    def usage_totals(self) -> UsageTotals:
        return UsageTotals()

    @property
    def cache_hit_rate(self) -> float | None:
        return None

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append((list(messages), list(tools)))
        if self.call_bash and len(self.requests) == 1:
            yield ToolCallEvent(
                calls=[ToolCall(id="pwd-call", name="bash", input={"command": "pwd"})]
            )
            yield FinalMessage(content="", stop_reason="tool_use")
            return
        yield TextDelta(chunk="Task finished.")
        yield FinalMessage(content="Task finished.", stop_reason="end_turn")


class _RecordingShell:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.outputs: list[str] = []
        self._shell = SubprocessShell()

    def run(
        self,
        argv: list[str],
        cwd: Path,
        timeout: int = 30_000,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        self.calls.append((argv, cwd))
        result = self._shell.run(
            argv,
            cwd,
            timeout=timeout,
            cancel_event=cancel_event,
            on_progress=on_progress,
            env=env,
        )
        self.outputs.append(result.output)
        return result


def _capture_collector_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[ContextCollectionInput]:
    inputs: list[ContextCollectionInput] = []
    original_collect = FrozenContextCollectorRegistry.collect

    def collect(
        registry: FrozenContextCollectorRegistry,
        input: ContextCollectionInput,
    ) -> list[ContextBlock]:
        inputs.append(input)
        return original_collect(registry, input)

    monkeypatch.setattr(FrozenContextCollectorRegistry, "collect", collect)
    return inputs


@pytest.fixture
def working_directory_root() -> Iterator[Path]:
    """把应用装配的工作区与会话限制在仓库证据目录中。"""
    root = Path(__file__).resolve().parents[3] / "e2e-results"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cwd-contract-", dir=root) as directory:
        yield Path(directory)


class TestHarnessWorkingDirectoryContract:
    """通过真实装配和本地 pwd 验证目录契约，provider 使用受控边界。

    覆盖宿主目录与项目目录不同的缺口，不验证服务协议或沙箱启动。
    运行：uv run --locked pytest -q src/cade/tests/test_assembly_contract.py
    旧版向 provider 和 collector 发送宿主 cwd；回归应在该断言失败。
    """

    @pytest.mark.asyncio
    async def test_app_uses_project_root_for_context_and_default_shell_cwd(
        self,
        working_directory_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        host_cwd = Path.cwd()
        project_root = working_directory_root / "task-workspace"
        project_root.mkdir()
        provider = _WorkingDirectoryProvider(call_bash=True)
        shell = _RecordingShell()
        collector_inputs = _capture_collector_inputs(monkeypatch)
        profiles = {
            "main": ModelProfileConfig(
                transport="openai_chat",
                chat_model=provider.model,
            ),
            "subagent": ModelProfileConfig(
                transport="openai_chat",
                chat_model=provider.model,
            ),
        }
        bundle = ProviderBundle(
            llm=provider,
            llms={"main": provider, "subagent": provider},
        )
        monkeypatch.setattr(
            app_module,
            "resolve_model_profiles",
            lambda *_args, **_kwargs: profiles,
        )
        monkeypatch.setattr(
            app_module,
            "build_provider_bundle",
            lambda _settings: bundle,
        )

        def build_recording_shell(
            _project_root: Path,
            _security: SecurityRuntimeConfig,
        ) -> Shell:
            return shell

        monkeypatch.setattr(
            registry_module,
            "build_shell_from_security",
            build_recording_shell,
        )

        app = build_app(
            project_root=project_root,
            runtime_config=CadeRuntimeConfig(),
            sessions_dir=working_directory_root / "sessions",
        )
        try:
            await app.agent.run_async("Run pwd and report its output.")
        finally:
            app.close()

        assert Path.cwd() == host_cwd
        assert len(provider.requests) == 2
        request_text = str(provider.requests[0][0])
        assert f"cwd={project_root.resolve()}" in request_text
        assert f'"cwd": "{project_root.resolve()}"' in request_text
        assert collector_inputs[0].project_root == project_root
        assert collector_inputs[0].cwd == project_root
        assert any(tool.name == "bash" for tool in provider.requests[0][1])
        assert len(shell.calls) == 1
        assert shell.calls[0][1] == project_root.resolve()
        assert str(project_root.resolve()) in shell.outputs[0]
        assert str(project_root.resolve()) in str(provider.requests[1][0])
        assert Path.cwd() == host_cwd

    @pytest.mark.asyncio
    async def test_harness_without_project_root_falls_back_to_host_cwd(
        self,
        working_directory_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        host_cwd = Path.cwd()
        provider = _WorkingDirectoryProvider(call_bash=False)
        collector_inputs = _capture_collector_inputs(monkeypatch)
        context_collectors = ContextCollectorRegistry()
        context_collectors.register_section(
            make_state_section(
                "environment",
                "environment",
                ContextBlockSource.ENVIRONMENT,
            )
        )
        composition = AgentComposition.create(
            primary_provider=provider,
            registry=(),
            config=CadeRuntimeConfig().agent,
            gate=GateConfig(),
            request_assembler=DefaultRequestAssembler(
                context_collectors=context_collectors.freeze()
            ),
            runtime_context_provider=None,
        )
        inbox = SessionInbox(SessionStore(working_directory_root / "sessions"))
        harness = AgentHarness(
            composition,
            AgentRuntimeConfig(session_inbox=inbox),
        )

        await harness.run_async("Report the current directory.")

        assert len(provider.requests) == 1
        assert f'"cwd": "{host_cwd}"' in str(provider.requests[0][0])
        assert collector_inputs[0].project_root is None
        assert collector_inputs[0].cwd == host_cwd
        assert Path.cwd() == host_cwd


@pytest.mark.asyncio
async def test_model_facts_are_durable_before_send_and_not_recorded_twice(
    working_directory_root: Path,
) -> None:
    """覆盖缺口：UI 消费晚于请求时，模型事实仍须先落盘。

    运行本模块；使用真实装配、会话与受控 provider。在每次发送边界独立
    重放磁盘日志并比较完整消息，消费端再次记录同一事件不得复制事实。
    """
    store = SessionStore(working_directory_root / "sessions")
    recorder = SessionRecorder(store)

    class Provider(_WorkingDirectoryProvider):
        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            restored = project_session_surface(
                store.build_branch(), artifacts_dir=store.artifacts_dir
            )
            assert any(isinstance(m, ContextMessage) for m in restored.messages)
            assert convert_to_llm(list(restored.messages)) == messages
            async for event in super().stream(messages, tools, options, **kwargs):
                yield event

    provider = Provider(call_bash=False)
    context_collectors = ContextCollectorRegistry()
    context_collectors.register_section(
        ContextSection(
            "environment",
            lambda input: [
                ContextBlock(
                    source=ContextBlockSource.ENVIRONMENT,
                    priority=ContextPriority.CONTEXT,
                    content=str(input.state["environment"]),
                )
            ],
        )
    )
    harness = AgentHarness(
        AgentComposition.create(
            primary_provider=provider,
            registry=(),
            config=CadeRuntimeConfig().agent,
            gate=GateConfig(),
            request_assembler=DefaultRequestAssembler(
                context_collectors=context_collectors.freeze()
            ),
            runtime_context_provider=None,
        ),
        AgentRuntimeConfig(
            session_inbox=SessionInbox(store),
            gate=GateRuntimeConfig(
                hook_manager=build_hook_manager(
                    None, recorder, working_directory_root, subagent=False
                )
            ),
        ),
    )
    harness.session_id = store.session_id
    for question in ("First task", "Second task"):
        async for event in harness.arun_stream(question):
            recorder.record_event(event)
    facts = [entry.content for entry in store.build_branch() if entry.type == "event"]
    assert (
        sum(isinstance(f, dict) and f.get("type") == "runtime_context" for f in facts)
        == 1
    )
    assert sum(isinstance(f, dict) and f.get("type") == "assistant" for f in facts) == 2
    assert len(provider.requests) == 2


class _GoalRoutingProvider(_WorkingDirectoryProvider):
    """根据请求类型返回主任务或 Goal 验收结果。"""

    def __init__(self, model: str) -> None:
        super().__init__(call_bash=False)
        self._model = model

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append((list(messages), list(tools)))
        is_judgment = any(
            message.get("role") == "user"
            and "Has this stop condition been satisfied?" in str(message.get("content"))
            for message in messages
        )
        if is_judgment:
            yield FinalMessage(
                content='{"ok": true, "reason": "Completion verified."}',
                stop_reason="end_turn",
            )
        else:
            yield FinalMessage(content="Task finished.", stop_reason="end_turn")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_judge", "switch_main"),
    [(False, False), (True, False), (False, True), (True, True)],
)
async def test_goal_uses_configured_judge_or_current_main(
    configured_judge: bool,
    switch_main: bool,
    working_directory_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goal 验收使用显式 judge；省略时跟随主模型切换。"""
    main = _GoalRoutingProvider("gpt-4o-mini")
    judge = _GoalRoutingProvider("gpt-4o")
    switched = _GoalRoutingProvider("gpt-4.1-mini")
    profiles = {
        "main": ModelProfileConfig(chat_model=main.model),
        "subagent": ModelProfileConfig(chat_model=main.model),
        "judge": ModelProfileConfig(chat_model=judge.model),
    }
    bundle = ProviderBundle(
        llm=main,
        llms={"main": main, "subagent": main, "judge": judge},
    )
    monkeypatch.setattr(app_module, "resolve_model_profiles", lambda *_args: profiles)
    monkeypatch.setattr(app_module, "build_provider_bundle", lambda _settings: bundle)
    config = CadeRuntimeConfig.model_validate(
        {"provider": {"model_profiles": {"judge": {"model": judge.model}}}}
        if configured_judge
        else {}
    )
    root = working_directory_root / "judge-workspace"
    root.mkdir()
    app = build_app(
        project_root=root,
        runtime_config=config,
        sessions_dir=working_directory_root / "sessions",
    )
    try:
        if switch_main:
            app.agent.replace_primary_provider(switched)
        app.agent.set_goal("Task finished.")
        await app.agent.run_async("Finish the task.")
        assert app.agent.goal_condition is None
    finally:
        app.close()

    active_main = switched if switch_main else main
    assert len(active_main.requests) == (1 if configured_judge else 2)
    assert len(judge.requests) == (1 if configured_judge else 0)
    assert len(main.requests) == (0 if switch_main else 1 if configured_judge else 2)
    if switch_main:
        assert len(switched.requests) == (1 if configured_judge else 2)


class _UnauthorizedModelError(RuntimeError):
    status_code = 401


class _FailingSelectedProvider(_WorkingDirectoryProvider):
    """在每次模型请求中模拟不可重试的鉴权失败。"""

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append((list(messages), list(tools)))
        yield TextDelta(chunk="")
        raise _UnauthorizedModelError("Selected model authentication failed")


def test_config_rejects_automatic_model_fallback() -> None:
    """移除的辅助模型角色必须在配置加载时明确报错。"""
    with pytest.raises(ValueError, match="fallback"):
        CadeRuntimeConfig.model_validate(
            {"provider": {"model_profiles": {"fallback": {"model": "other-model"}}}}
        )


@pytest.mark.asyncio
async def test_failed_main_model_never_uses_backup_provider(
    working_directory_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续模型失败保持所选 Provider，禁止隐式模型切换。"""
    selected = _FailingSelectedProvider(call_bash=False)
    unused = _WorkingDirectoryProvider(call_bash=False)
    profiles = {
        "main": ModelProfileConfig(chat_model=selected.model),
        "subagent": ModelProfileConfig(chat_model=selected.model),
    }
    bundle = ProviderBundle(
        llm=selected,
        llms={"main": selected, "subagent": selected, "fallback": unused},
    )
    monkeypatch.setattr(app_module, "resolve_model_profiles", lambda *_args: profiles)
    monkeypatch.setattr(app_module, "build_provider_bundle", lambda _settings: bundle)

    root = working_directory_root / "no-model-fallback"
    root.mkdir()
    app = build_app(
        project_root=root,
        runtime_config=CadeRuntimeConfig(),
        sessions_dir=working_directory_root / "sessions",
    )
    try:
        assert app.agent.provider is selected
        for attempt in range(4):
            await app.agent.run_async(f"Request {attempt}")
            assert app.agent.provider is selected
    finally:
        app.close()

    assert len(selected.requests) == 4
    assert unused.requests == []
