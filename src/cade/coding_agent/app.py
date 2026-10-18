"""Cade 应用入口。

CadeApp 数据类和 build_app 编排函数。装配逻辑委托给 assembly 子包。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cade.agent.context_policy import ContextPolicy
from cade.agent.messages import AgentMessage, UserMessage
from cade.agent.types import ToolSpec
from cade.ai.events import FinalMessage, TextDelta
from cade.ai.models import get_model_context_window
from cade.ai.providers.registry import (
    ModelProfileConfig,
    ProviderSettings,
    build_provider_bundle,
)
from cade.coding_agent.execution_modes import ExecutionMode
from cade.coding_agent.harness import CodingAgentHarness
from cade.harness.agent_runtime import (
    AgentHarnessEvent,
)
from cade.harness.config import AgentConfig, CadeRuntimeConfig
from cade.harness.observability import ExternalHookDiagnostic, ExternalHookRunner
from cade.harness.session import SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.replay import replay_session

from . import assembly as _assembly
from .assembly import (
    build_agent,
    build_shared_infra,
)
from .assembly.providers import resolve_model_profile, resolve_model_profiles
from .working_note import render_working_note_restoration

if TYPE_CHECKING:
    from cade.harness.agent_runtime.subagents import SubagentSessionManager
    from cade.harness.mcp import McpRuntimeRegistry


def _bound_context_window(
    profile: ModelProfileConfig, requested: int | None
) -> ModelProfileConfig:
    """窗口覆盖值不能超过模型已知的真实窗口。

    显式请求超限时报错；切换模型时沿用的旧覆盖值超限则丢弃，回到模型默认窗口。
    """
    window = profile.context_window
    limit = get_model_context_window(profile.chat_model, transport=profile.transport)
    if window is None or limit is None or window <= limit:
        return profile
    if requested:
        raise ValueError(
            f"context window {window} exceeds the {limit} supported by "
            f"{profile.chat_model}"
        )
    return replace(profile, context_window=None)


@dataclass
class CadeApp:
    """Cade 应用句柄。"""

    agent: CodingAgentHarness
    registry: tuple[ToolSpec, ...] = ()
    external_hook_runner: ExternalHookRunner | None = None

    mcp_runtime: McpRuntimeRegistry | None = None
    subagents: SubagentSessionManager | None = None
    session_recorder: SessionRecorder | None = None
    _model_profiles: dict[str, Any] | None = None
    _runtime_config: CadeRuntimeConfig | None = None
    _env_files: tuple[Path, ...] = ()
    _closers: tuple[Callable[[], None], ...] = ()
    _closed: bool = False

    def set_model(
        self,
        *,
        model: str,
        profile: str = "main",
        transport: str | None = None,
        base_url: str | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        context_window: int | None = None,
    ) -> str:
        """切换主/子 agent 模型；`context_window` 为 0 时恢复模型默认窗口。"""
        from cade.ai.providers.registry import ModelProfileProto
        from cade.ai.resolver import ModelResolver, provider_for_transport

        if profile not in {"main", "subagent"}:
            raise ValueError("profile must be main or subagent")
        if self._model_profiles is None or self._runtime_config is None:
            raise RuntimeError("model configuration is not available")
        sync_reviewer = profile == "main" and "reviewer" not in self._model_profiles
        current = self._model_profiles.get(profile) or self._model_profiles["main"]
        resolution = ModelResolver.resolve(
            model_or_alias=model,
            transport=transport,
            fallback_transport=current.transport,
        )
        selected = (
            provider_for_transport(transport)
            if transport and "/" not in model
            else resolution.provider
        )
        if (
            "/" not in model
            and transport is None
            and (
                resolution.model == current.chat_model
                or ModelResolver.infer_provider(resolution.model) is None
            )
        ):
            selected = current.provider
        config = self._runtime_config
        if base_url is not None or transport is not None:
            from cade.harness.config import ProviderConnectionRuntimeConfig

            connection = config.provider.connections.get(selected)
            connections = dict(config.provider.connections)
            connections[selected] = ProviderConnectionRuntimeConfig.model_validate(
                {
                    "transport": resolution.transport,
                    "base_url": base_url or (connection.base_url if connection else ""),
                    "api_key_env": connection.api_key_env if connection else None,
                }
            )
            config = config.model_copy(
                update={
                    "provider": config.provider.model_copy(
                        update={"connections": connections}
                    )
                }
            )
        options = {
            "context_window": (
                current.context_window
                if context_window is None
                else context_window or None
            ),
            "thinking": thinking if thinking is not None else current.thinking,
            "reasoning_effort": reasoning_effort
            if reasoning_effort is not None
            else current.reasoning_effort,
            "clear_thinking": current.clear_thinking,
            "tool_stream": current.tool_stream,
            "response_format": current.response_format,
        }
        new_cfg = resolve_model_profile(
            config, selected, resolution.model, options, self._env_files
        )
        new_cfg = _bound_context_window(new_cfg, context_window)
        resolved_model = new_cfg.chat_model
        profiles: dict[str, ModelProfileProto] = {
            "main": self._model_profiles["main"],
            profile: new_cfg,
        }
        if sync_reviewer:
            profiles["reviewer"] = new_cfg

        bundle = build_provider_bundle(
            ProviderSettings(
                env_files=self._env_files,
                model_profiles=profiles,
            )
        )
        new_provider = bundle.llms.get(profile, bundle.llm)
        if profile == "main":
            self.agent.replace_primary_provider(new_provider)
            if sync_reviewer:
                reviewer = self.agent.auto_approval_callback
                replace_provider = getattr(reviewer, "replace_provider", None)
                if callable(replace_provider):
                    replace_provider(bundle.llms["reviewer"])
        elif self.subagents is None:
            raise RuntimeError("subagent runtime is not configured")
        else:
            self.subagents.replace_provider(new_provider)
        self._model_profiles[profile] = new_cfg
        return resolved_model

    def get_model_info(self) -> dict[str, str]:
        active = self.agent.provider
        active_profile = next(
            (
                config
                for config in (self._model_profiles or {}).values()
                if config.chat_model == active.model
                and (
                    config.transport == active.transport
                    or config.transport == "custom"
                    and active.transport == "openai_chat"
                )
                and config.base_url == active.base_url
            ),
            ModelProfileConfig(),
        )
        info: dict[str, str] = {
            "model": active.model,
            "base_url": active.base_url,
            "transport": active.transport,
            "profile": "main",
            "provider": active_profile.provider,
        }
        if active.thinking:
            info["thinking"] = "on"
        else:
            info["thinking"] = "off"
        if active.reasoning_effort is not None:
            info["reasoning_effort"] = active.reasoning_effort
        model_window = get_model_context_window(
            active.model, transport=active.transport
        )
        window = active.context_window or model_window
        if window:
            info["context_window"] = str(window)
        if model_window:
            info["model_context_window"] = str(model_window)
        return info

    def instruction_paths(self) -> tuple[Path, ...]:
        """从当前指令收集器读取实际可用的文件来源，不改变上下文状态。"""
        from cade.agent.context import (
            ContextCollectionInput,
            FrozenContextCollectorRegistry,
        )
        from cade.agent.request import DefaultRequestAssembler

        assembler = self.agent.composition.request_assembler
        if not isinstance(assembler, DefaultRequestAssembler):
            return ()
        collectors = assembler.context_collectors
        if not isinstance(collectors, FrozenContextCollectorRegistry):
            return ()
        paths: list[Path] = []
        for section in collectors.sections:
            if section.section_id == "agents":
                for block in section.collect(ContextCollectionInput()):
                    if block.provenance and block.provenance != "inline instruction":
                        path = Path(block.provenance)
                        if path not in paths:
                            paths.append(path)
        return tuple(paths)

    def ask(self, question: str) -> str:
        answer = ""
        for event in self.ask_stream(question):
            if event.type == "final":
                answer = event.data.answer
        return answer

    @property
    def session_store(self) -> SessionStore:
        recorder = self.session_recorder
        if recorder is None:
            raise RuntimeError("session recorder is not configured")
        return recorder.store

    def ask_stream(
        self,
        question: str | None,
        mode: ExecutionMode | None = None,
        *,
        display_question: str | None = None,
    ) -> Iterator[AgentHarnessEvent]:
        recorder = self.session_recorder
        if recorder is None:
            raise RuntimeError("session recorder is not configured")
        recorder.bind_agent(self.agent)
        if question is not None:
            self.agent.followup(
                UserMessage(content=question),
                display_text=display_question,
            )
        for event in self.agent.run_stream(
            None,
            mode=mode,
            display_question=display_question,
        ):
            recorder.record_event(event)
            yield event

    def ask_side_question(self, question: str) -> Iterator[str]:
        """用独立 provider 回答旁支问题，主会话保留原运行状态。"""
        from cade.harness.agent_runtime.agent_helpers import aiter_to_sync_iter
        from cade.harness.agent_runtime.cancellation import CancellationToken

        request = self.agent.prepare_read_only_request(question)
        request.require_sendable()
        profiles = self._model_profiles or {}
        provider = build_provider_bundle(
            ProviderSettings(
                env_files=self._env_files,
                model_profiles={"main": profiles["main"]} if "main" in profiles else {},
            )
        ).llm

        async def answer() -> AsyncIterator[str]:
            streamed = False
            async for event in provider.stream(
                messages=list(request.wire_messages),
                tools=[],
                options=request.options,
            ):
                if isinstance(event, TextDelta):
                    streamed = True
                    yield event.chunk
                elif isinstance(event, FinalMessage):
                    if event.stop_reason == "error":
                        raise RuntimeError(
                            event.content or "Side question provider failed"
                        )
                    if not streamed and event.content:
                        yield event.content

        yield from aiter_to_sync_iter(answer(), CancellationToken())

    def hook_diagnostics(self) -> tuple[ExternalHookDiagnostic, ...]:
        """返回外部命令 hook 的运行时诊断。"""
        if self.external_hook_runner is None:
            return ()
        return self.external_hook_runner.diagnostics()

    def record_context_window_reset(
        self,
        *,
        window_id: str,
        messages_before: int,
        messages_after: int,
        replacement: list[AgentMessage],
        reproject_from_facts: bool = False,
        rollover_policy: ContextPolicy | None = None,
        preserve_user_request: bool = True,
    ) -> str:
        recorder = self.session_recorder
        if recorder is None:
            raise RuntimeError("session recorder is not configured")
        return recorder.record_context_window_reset(
            window_id=window_id,
            messages_before=messages_before,
            messages_after=messages_after,
            replacement=replacement,
            reproject_from_facts=reproject_from_facts,
            rollover_policy=rollover_policy,
            preserve_user_request=preserve_user_request,
        )

    def restore_session(self) -> None:
        """从当前 session branch 恢复完整 agent 运行状态。"""
        project_root = self.session_store.project_root
        replay_session(
            self.agent,
            self.session_store,
            restoration_context_renderer=lambda state: render_working_note_restoration(
                project_root, state
            ),
        )

    def mcp_status(self) -> tuple[dict[str, object], ...]:
        """返回 MCP server 运行时状态快照。"""
        if self.mcp_runtime is None:
            return ()
        return tuple(status.__dict__ for status in self.mcp_runtime.status_snapshot())

    def close(self) -> None:
        if self._closed:
            return
        for closer in self._closers:
            closer()
        self._closed = True


def build_app(
    project_root: Path,
    env_files: tuple[Path, ...] | None = None,
    agent_config: AgentConfig | None = None,
    skills_dir: Path | None = None,
    audit_path: Path | None = None,
    runtime_config: CadeRuntimeConfig | None = None,
    sessions_dir: Path | None = None,
) -> CadeApp:
    """装配完整的 Cade 应用。"""
    cfg = _assembly.resolve_config(
        project_root, env_files, agent_config, skills_dir, audit_path, runtime_config
    )
    infra = build_shared_infra(
        project_root,
        cfg.runtime_config,
        sessions_dir=sessions_dir,
    )

    model_profiles = resolve_model_profiles(cfg.runtime_config, cfg.env_files)
    providers = build_provider_bundle(
        ProviderSettings(
            env_files=cfg.env_files,
            model_profiles=model_profiles,
        )
    )
    external_hook_runner = (
        ExternalHookRunner(cfg.runtime_config.hooks.entries, project_root)
        if cfg.runtime_config.hooks.entries
        else None
    )
    (
        registry_state,
        shell_spec,
        closers,
        skill_registry,
        mcp_runtime_registry,
        subagents,
    ) = _assembly.build_tool_registry(
        project_root=project_root,
        subagent_provider=providers.llms.get("subagent", providers.llm),
        runtime_config=cfg.runtime_config,
        session_recorder=infra.session_recorder,
        cancel_event=infra.cancellation_token,
        skills_dir=cfg.skills_dir,
        session_history=infra.session_history,
        context_window_controller=infra.context_window_controller,
    )

    auto_approval_callback = None
    security = cfg.runtime_config.security
    if security.approval_policy == "on-request":
        from cade.harness.security import AutoApprovalReviewer

        auto_reviewer = AutoApprovalReviewer(
            providers.llms.get("reviewer", providers.llm),
            timeout_seconds=float(security.auto_review_timeout_seconds),
            max_workers=cfg.agent_config.tool_workers,
        )
        auto_approval_callback = auto_reviewer
        closers = (*closers, auto_reviewer.close)

    agent = build_agent(
        project_root=project_root,
        llm=providers.llm,
        registry=registry_state,
        config=cfg.agent_config,
        audit_path=cfg.audit_path,
        runtime_config=cfg.runtime_config,
        session_recorder=infra.session_recorder,
        session_inbox=infra.session_inbox,
        shell_spec=shell_spec,
        context_rollover=infra.context_rollover,
        context_window_controller=infra.context_window_controller,
        cancellation_token=infra.cancellation_token,
        judge_provider=(
            providers.llms["judge"]
            if "judge" in cfg.runtime_config.provider.model_profiles
            else None
        ),
        skill_registry=skill_registry,
        external_hook_runner=external_hook_runner,
        session_history=infra.session_history,
        auto_approval_callback=auto_approval_callback,
    )

    infra.session_recorder.bind_agent(agent)
    return CadeApp(
        agent=agent,
        registry=registry_state,
        external_hook_runner=external_hook_runner,
        mcp_runtime=mcp_runtime_registry,
        subagents=subagents,
        session_recorder=infra.session_recorder,
        _env_files=cfg.env_files,
        _model_profiles=model_profiles,
        _runtime_config=cfg.runtime_config,
        _closers=closers,
    )
