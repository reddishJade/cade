"""Provider 请求的单一组装边界。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from cade.ai.events import Message
from cade.ai.providers._codec import provider_function_name
from cade.ai.types import StreamOptions, ToolDefinition

from ._codec import convert_to_llm
from ._context_window import context_window_id, estimate_tokens
from .context import (
    ContextAssembler,
    ContextAssemblyError,
    ContextAssemblyInput,
    ContextBlock,
    ContextBlockSource,
    ContextBlockTarget,
    ContextCollectionInput,
    ContextPriority,
    DefaultContextAssembler,
    FrozenContextCollectorRegistry,
    _block_to_text,
    _estimate_base_tokens,
    _estimate_messages_tokens,
)
from .context_manager import history_fingerprints
from .context_policy import (
    ContextPolicy,
    ContextSnapshot,
    current_durable_ids,
    latest_task_message,
    mandatory_history,
    protected_working_set,
    superseded_state_ids,
)
from .messages import (
    AgentMessage,
    ContextMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from .types import AgentTool, materialize_json_mapping

if TYPE_CHECKING:
    from .config import AgentContext

type MessageConverter = Callable[[list[AgentMessage]], list[Message]]


@dataclass(frozen=True)
class RequestContextTrace:
    """一项动态上下文对本次请求的可审计决议。"""

    source: str
    target: str
    block_id: str
    included: bool
    token_count: int
    content_digest: str
    provenance: str = ""
    truncated: bool = False
    truncation_reason: str | None = None
    scope: str = "project"


@dataclass(frozen=True)
class RequestAssembly:
    """传给 provider 与审计 hook 的同一份请求快照。"""

    messages: tuple[AgentMessage, ...]
    wire_messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    context_trace: tuple[RequestContextTrace, ...]
    current_step: int
    context_message: ContextMessage | None = None
    estimated_tokens: int = 0
    unreclaimed_input_tokens: int = 0
    token_budget: int = 0
    budget_remaining: int = 0
    options: StreamOptions | None = None
    local_estimated_tokens: int = 0
    token_estimate_source: str = "local"
    context_policy: ContextPolicy | None = None
    evidence_omitted: tuple[str, ...] = ()
    evidence_reclaimed_tokens: int = 0
    rotation_blocked_reason: str | None = None
    mandatory_estimated_tokens: int = 0
    context_snapshot: ContextSnapshot | None = None
    source_message_digests: tuple[str, ...] = ()
    working_omitted: tuple[str, ...] = ()
    assembly_error: str | None = None

    def require_sendable(self) -> None:
        """失败投影只用于预算诊断和换窗，不得发送给模型。"""
        if self.assembly_error is not None:
            raise ContextAssemblyError(self.assembly_error)
        if self.token_budget > 0 and self.estimated_tokens > self.token_budget:
            raise ContextAssemblyError(
                f"Cannot assemble context: input {self.estimated_tokens} tokens "
                f"exceeds safe budget {self.token_budget} tokens"
            )


class RequestAssembler(Protocol):
    """从 agent 当前投影生成一次完整 provider 请求。"""

    def assemble(
        self,
        context: AgentContext,
        *,
        current_step: int,
        options: StreamOptions | None,
    ) -> RequestAssembly: ...


@dataclass(frozen=True)
class DefaultRequestAssembler:
    """按固定阶段收集、注入、裁剪并编码模型输入。"""

    converter: MessageConverter = convert_to_llm
    context_collectors: FrozenContextCollectorRegistry | None = None
    context_assembler: ContextAssembler = field(default_factory=DefaultContextAssembler)
    preserve_tool_result: Callable[[ToolResultMessage], bool] | None = None

    def assemble(
        self,
        context: AgentContext,
        *,
        current_step: int,
        options: StreamOptions | None,
    ) -> RequestAssembly:
        policy = context.context_policy
        if policy is None and context.request_token_budget > 0:
            # 只有输入上限的调用方也使用同一回收策略，不额外扣除输出预留。
            policy = ContextPolicy(
                physical_window=context.request_token_budget,
                output_reserve=0,
                headroom_tokens=0,
                trigger_ratio=1,
                next_turn_input_tokens=0,
                automatic_rollover=False,
            )
        if policy is not None:
            policy = policy.for_request(options)
            options = policy.request_options(options)
        token_budget = (
            policy.input_budget if policy is not None else context.request_token_budget
        )
        admission_budget = (
            policy.admission_target if policy and token_budget > 0 else token_budget
        )
        # 请求准备不得改写已持久化或运行中的会话事实；
        # 只在本次投影里修复不完整的工具调用对。
        effective_messages = context.context_manager.normalize_messages(
            context.messages
        )
        effective_context = context.model_copy(update={"messages": effective_messages})
        context_blocks = self._collect(effective_context, list(effective_messages))
        base_messages = [
            *context.request_prefix,
            *effective_messages,
        ]
        obsolete_ids = superseded_state_ids(base_messages)
        protected_ids = current_durable_ids(base_messages) | frozenset(
            message.tool_call_id
            for message in base_messages
            if isinstance(message, ToolResultMessage)
            and (
                self.preserve_tool_result is not None
                and self.preserve_tool_result(message)
                and message.tool_call_id not in obsolete_ids
            )
        )
        current_messages, state_omitted = (
            policy.project_durable_state(base_messages, protected_ids)
            if policy is not None
            else (base_messages, ())
        )
        request_messages = list(current_messages)
        evidence_omitted: tuple[str, ...] = ()
        if policy is not None:
            request_messages, omitted = policy.project_evidence(
                request_messages, protected_ids
            )
            evidence_omitted = tuple(dict.fromkeys((*evidence_omitted, *omitted)))
        tool_definitions = _tools_to_definitions(context.tools)
        source_digests = history_fingerprints(effective_messages)
        local_tokens = 0
        estimate_source = "local"

        def calibrate_tokens(messages: list[AgentMessage], tokens: int) -> int:
            nonlocal local_tokens, estimate_source
            local_tokens = tokens
            predicted, anchored = context.context_manager.estimate_request_tokens(
                self.converter(messages),
                tool_definitions,
                options,
                tokens,
                prefix_length=len(context.request_prefix),
                source_messages=source_digests,
            )
            estimate_source = "provider_anchor" if anchored else "local"
            return predicted

        assembly_input = ContextAssemblyInput(
            system_prompt=context.system_prompt,
            messages=request_messages,
            tools=list(context.tools),
            context_blocks=context_blocks,
            token_budget=admission_budget,
            calibrate_tokens=calibrate_tokens,
        )
        result = self.context_assembler.assemble(assembly_input)
        unreclaimed_input_tokens = result.total_tokens
        evidence_reclaimed_tokens = 0
        if (
            policy is not None
            and admission_budget > 0
            and result.total_tokens > admission_budget
        ):
            # 用实际请求压力收紧证据配额；状态只收集一次，所有裁剪均为临时投影。
            original_messages = request_messages
            original_evidence_tokens = _estimate_messages_tokens(
                [
                    m
                    for m in original_messages
                    if isinstance(m, ToolResultMessage)
                    and m.tool_call_id not in protected_ids
                ]
            )
            evidence_budget = min(policy.evidence_budget, original_evidence_tokens)
            for _attempt in range(3):
                deficit = result.total_tokens - admission_budget
                if deficit <= 0 or evidence_budget <= 0:
                    break
                evidence_budget = max(0, evidence_budget - deficit)
                reduced, omitted = policy.project_evidence(
                    original_messages, protected_ids, token_budget=evidence_budget
                )
                assembly_input.messages = reduced
                result = self.context_assembler.assemble(assembly_input)
                evidence_omitted = tuple(dict.fromkeys((*evidence_omitted, *omitted)))
        working_omitted: tuple[str, ...] = state_omitted
        if policy is not None and admission_budget > 0:
            for _attempt in range(3):
                deficit = result.total_tokens - admission_budget
                if deficit <= 0:
                    break
                reduced, omitted = policy.project_working_set(
                    assembly_input.messages, protected_ids, deficit
                )
                if not omitted:
                    break
                working_omitted = (*working_omitted, *omitted)
                assembly_input.messages = reduced
                result = self.context_assembler.assemble(assembly_input)
        if policy is not None and admission_budget > 0:
            for _attempt in range(3):
                deficit = result.total_tokens - admission_budget
                if deficit <= 0:
                    break
                prefix_length = len(context.request_prefix)
                history, omitted = policy.project_history(
                    assembly_input.messages[prefix_length:], protected_ids, deficit
                )
                if not omitted:
                    break
                working_omitted = (*working_omitted, *omitted)
                assembly_input.messages = [
                    *assembly_input.messages[:prefix_length],
                    *history,
                ]
                result = self.context_assembler.assemble(assembly_input)
        if working_omitted and admission_budget > result.total_tokens:
            # 旧交互释放空间后，优先恢复近期原文，避免先裁正文导致优先级倒置。
            originals = {
                m.tool_call_id: m
                for m in request_messages
                if isinstance(m, ToolResultMessage)
            }
            before_restore = assembly_input.messages
            restored = list(before_restore)
            remaining = admission_budget - result.total_tokens
            for index in range(len(restored) - 1, -1, -1):
                message = restored[index]
                if not isinstance(message, ToolResultMessage):
                    continue
                original = originals.get(message.tool_call_id)
                if original is None or original.content == message.content:
                    continue
                cost = _estimate_messages_tokens(
                    [original]
                ) - _estimate_messages_tokens([message])
                if cost <= remaining:
                    restored[index] = original
                    remaining -= cost
            assembly_input.messages = restored
            candidate = self.context_assembler.assemble(assembly_input)
            if candidate.total_tokens > admission_budget:
                assembly_input.messages = before_restore
                result = self.context_assembler.assemble(assembly_input)
            else:
                result = candidate
        admitted = {
            m.tool_call_id: m
            for m in result.messages
            if isinstance(m, ToolResultMessage)
        }
        evidence_reclaimed_tokens = max(
            0,
            _estimate_messages_tokens(
                [m for m in request_messages if isinstance(m, ToolResultMessage)]
            )
            - _estimate_messages_tokens(list(admitted.values())),
        )
        evidence_omitted = tuple(
            m.tool_call_id
            for m in base_messages
            if isinstance(m, ToolResultMessage)
            and (
                m.tool_call_id not in admitted
                or admitted[m.tool_call_id].content != m.content
            )
        )
        current_context = next(
            (m for m in reversed(result.messages) if isinstance(m, ContextMessage)),
            None,
        )
        mandatory_messages = [
            *context.request_prefix,
            *mandatory_history(effective_messages, protected_ids),
            *[
                SystemMessage(content=b.content)
                if b.target == ContextBlockTarget.SYSTEM
                else UserMessage(content=_block_to_text(b))
                for b in result.blocks_used
                if b.target == ContextBlockTarget.SYSTEM
                or b.source == ContextBlockSource.SKILL
                or b.priority == ContextPriority.INSTRUCTION
            ],
            *([current_context] if current_context is not None else []),
        ]
        mandatory_tokens = _estimate_base_tokens(
            ContextAssemblyInput(
                system_prompt=context.system_prompt, tools=list(context.tools)
            ),
            mandatory_messages,
        )
        rotation_blocked_reason = None
        if policy is not None:
            if token_budget > 0 and mandatory_tokens > token_budget:
                rotation_blocked_reason = "mandatory_input_exceeds_budget"
            elif not policy.can_reclaim_history(effective_messages, protected_ids):
                rotation_blocked_reason = "no_reclaimable_history"
        messages = result.messages
        assembly_error = None
        if token_budget > 0 and result.total_tokens > token_budget:
            assembly_error = (
                f"Cannot assemble context: input {result.total_tokens} tokens exceeds "
                f"safe budget {token_budget} tokens after history/evidence reclamation; "
                f"mandatory input estimate is {mandatory_tokens} tokens. "
                "Reduce selected context or use a larger model window."
            )
        wire_messages = self.converter(messages) if assembly_error is None else []
        assembly = RequestAssembly(
            messages=tuple(messages),
            wire_messages=tuple(wire_messages),
            tools=tuple(tool_definitions),
            context_trace=(
                _context_trace(result.blocks_used) + _tool_trace(tool_definitions)
            ),
            current_step=current_step,
            context_message=result.context_message,
            estimated_tokens=result.total_tokens,
            unreclaimed_input_tokens=unreclaimed_input_tokens,
            token_budget=token_budget,
            budget_remaining=token_budget - result.total_tokens
            if token_budget > 0
            else 0,
            options=options,
            local_estimated_tokens=local_tokens or result.total_tokens,
            token_estimate_source=estimate_source,
            context_policy=policy,
            evidence_omitted=evidence_omitted,
            evidence_reclaimed_tokens=evidence_reclaimed_tokens,
            rotation_blocked_reason=rotation_blocked_reason,
            mandatory_estimated_tokens=mandatory_tokens,
            source_message_digests=(
                history_fingerprints([*effective_messages, result.context_message])
                if result.context_message is not None
                else source_digests
            ),
            working_omitted=working_omitted,
            assembly_error=assembly_error,
            context_snapshot=_context_snapshot(
                effective_context,
                result.messages,
                result.blocks_used,
                protected_ids,
                policy,
                result.total_tokens,
                estimate_source,
                token_budget,
                rotation_blocked_reason,
            ),
        )
        return assembly

    def _collect(
        self,
        context: AgentContext,
        messages: list[AgentMessage],
    ) -> list[ContextBlock]:
        if self.context_collectors is None:
            return []
        collection_input = ContextCollectionInput(
            system_prompt=context.system_prompt,
            messages=messages,
            tools=list(context.tools),
            project_root=context.project_root,
            cwd=context.cwd,
            state=dict(context.state),
        )
        return self.context_collectors.collect(collection_input)


def _context_snapshot(
    context: AgentContext,
    projected: list[AgentMessage],
    blocks: list[ContextBlock],
    protected_ids: frozenset[str],
    policy: ContextPolicy | None,
    predicted_tokens: int,
    estimate_source: str,
    token_budget: int,
    blocked_reason: str | None,
) -> ContextSnapshot:
    """复用请求计量函数按生命周期归因，整体预测与本地分项分别标注来源。"""
    latest_user = latest_task_message(context.messages)
    latest_context = next(
        (m for m in reversed(projected) if isinstance(m, ContextMessage)), None
    )
    durable_ids = {id(m) for m in protected_working_set(projected, protected_ids)}
    if latest_user is not None:
        durable_ids.add(id(latest_user))
    if latest_context is not None:
        durable_ids.add(id(latest_context))
    fixed_context = {
        _block_to_text(block)
        for block in blocks
        if block.source == ContextBlockSource.SKILL
        or block.priority == ContextPriority.INSTRUCTION
    }
    fixed: list[AgentMessage] = []
    durable: list[AgentMessage] = []
    working: list[AgentMessage] = []
    evidence: list[AgentMessage] = []
    for message in projected:
        if id(message) in durable_ids:
            durable.append(message)
        elif isinstance(message, SystemMessage):
            if message.content.startswith("<context-history-reference>"):
                working.append(message)
            else:
                fixed.append(message)
        elif (
            isinstance(message, UserMessage)
            and isinstance(message.content, str)
            and message.content in fixed_context
        ):
            fixed.append(message)
        elif isinstance(message, ToolResultMessage):
            evidence.append(message)
        else:
            working.append(message)
    fixed_tokens = _estimate_base_tokens(
        ContextAssemblyInput(
            system_prompt=context.system_prompt, tools=list(context.tools)
        ),
        fixed,
    )
    durable_tokens = _estimate_messages_tokens(durable)
    working_tokens = _estimate_messages_tokens(working)
    evidence_tokens = _estimate_messages_tokens(evidence)
    return ContextSnapshot(
        physical_window=policy.physical_window if policy else None,
        effective_input_budget=token_budget,
        fixed_prefix_tokens=fixed_tokens,
        durable_tokens=durable_tokens,
        working_tokens=working_tokens,
        evidence_tokens=evidence_tokens,
        category_total_tokens=fixed_tokens
        + durable_tokens
        + working_tokens
        + evidence_tokens,
        total_input_tokens=predicted_tokens,
        total_input_source=estimate_source,
        output_reserve_tokens=policy.output_reserve if policy else 0,
        operational_headroom_tokens=policy.headroom if policy else 0,
        next_turn_input_tokens=policy.next_input_allowance if policy else 0,
        admission_target=policy.admission_target if policy else token_budget,
        projected_next_input_tokens=predicted_tokens
        + (policy.next_input_allowance if policy else 0),
        remaining_input_budget=token_budget - predicted_tokens
        if token_budget > 0
        else 0,
        current_window_id=context_window_id(context.messages) or None,
        last_rotation_reason=context.context_manager.last_rotation_reason,
        rotation_blocked_reason=blocked_reason,
    )


def _context_trace(used: list[ContextBlock]) -> tuple[RequestContextTrace, ...]:
    return tuple(_trace(block, True) for block in used)


def _trace(block: ContextBlock, included: bool) -> RequestContextTrace:
    return RequestContextTrace(
        source=block.source.value,
        target=block.target.value,
        block_id=block.block_id,
        included=included,
        token_count=block.get_token_count(),
        content_digest=hashlib.blake2b(
            block.content.encode("utf-8"), digest_size=32
        ).hexdigest(),
        provenance=block.provenance,
        truncated=block.truncated,
        truncation_reason=block.truncation_reason,
        scope=block.scope,
    )


def _tool_trace(tools: list[ToolDefinition]) -> tuple[RequestContextTrace, ...]:
    traces: list[RequestContextTrace] = []
    for tool in tools:
        payload = json.dumps(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "builtin": tool.builtin,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        traces.append(
            RequestContextTrace(
                source="tool",
                target="tool_definition",
                block_id=tool.name,
                included=True,
                token_count=estimate_tokens(payload),
                content_digest=hashlib.blake2b(
                    payload.encode("utf-8"), digest_size=32
                ).hexdigest(),
                provenance=f"tool:{tool.name}",
                scope="runtime",
            )
        )
    return tuple(traces)


def _tools_to_definitions(tools: list[AgentTool]) -> list[ToolDefinition]:
    result: list[ToolDefinition] = []
    for tool in tools:
        description = tool.description
        examples = getattr(tool, "examples", [])
        if examples:
            example_lines = ["\n", "Examples:"]
            for example in examples:
                example_lines.append(
                    f"  - {example.get('name', '')}: "
                    f"input={json.dumps(example.get('input', {}), ensure_ascii=False)}, "
                    f'output="{example.get("output", "")}"'
                )
            description += "\n".join(example_lines)
        builtin = getattr(tool, "builtin", None)
        result.append(
            ToolDefinition(
                name=provider_function_name(tool.name),
                description=description,
                parameters=materialize_json_mapping(tool.parameters),
                builtin=builtin if isinstance(builtin, dict) else None,
            )
        )
    return result
