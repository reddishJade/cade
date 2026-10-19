"""从最终请求生成独立审计快照，不读取或修改会话状态。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass

from cade.agent.request import RequestAssembly
from cade.ai.providers.base import ModelProvider
from cade.ai.types import StreamOptions
from cade.harness.observability import (
    EventCorrelation,
    HookRecord,
    hook_correlation_fields,
)

_TRANSPORT_OPTIONS = {"signal", "api_key", "headers", "on_payload", "on_response"}


def build_request_audit(
    assembly: RequestAssembly,
    provider: ModelProvider,
    composition_id: str,
    prompt_version: str,
    correlation: EventCorrelation,
) -> HookRecord:
    """复制模型输入；认证信息和进程内传输对象不进入快照。"""
    messages = deepcopy(list(assembly.wire_messages))
    tools = [asdict(tool) for tool in assembly.tools]
    provider_info = {
        "model": provider.model,
        "base_url": provider.base_url,
        "transport": provider.transport,
        "thinking": provider.thinking,
        "reasoning_effort": provider.reasoning_effort,
    }
    options = _request_options_payload(assembly.options)
    request = {
        "messages": messages,
        "tools": tools,
        "provider": provider_info,
        "options": options,
        "composition_id": composition_id,
    }
    request_bytes = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    system_prompt = "\n\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "system"
    )
    policy = assembly.context_policy
    return HookRecord(
        "before_provider_request",
        metadata={
            **request,
            "assembly": {
                "current_step": assembly.current_step,
                "estimated_tokens": assembly.estimated_tokens,
                "unreclaimed_input_tokens": assembly.unreclaimed_input_tokens,
                "local_estimated_tokens": assembly.local_estimated_tokens,
                "token_estimate_source": assembly.token_estimate_source,
                "context_policy": (
                    {
                        "physical_window": policy.physical_window,
                        "output_reserve": policy.output_reserve,
                        "headroom": policy.headroom,
                        "input_budget": policy.input_budget,
                        "rotation_threshold": policy.rotation_threshold,
                        "evidence_budget": policy.evidence_budget,
                        "working_budget": policy.working_budget,
                        "next_input_allowance": policy.next_input_allowance,
                        "admission_target": policy.admission_target,
                        "output_limit_supported": policy.output_limit_supported,
                    }
                    if policy is not None
                    else None
                ),
                "evidence_omitted": list(assembly.evidence_omitted),
                "working_omitted": list(assembly.working_omitted),
                "evidence_reclaimed_tokens": assembly.evidence_reclaimed_tokens,
                "rotation_blocked_reason": assembly.rotation_blocked_reason,
                "mandatory_estimated_tokens": assembly.mandatory_estimated_tokens,
                "context_snapshot": (
                    asdict(assembly.context_snapshot)
                    if assembly.context_snapshot is not None
                    else None
                ),
                "token_budget": assembly.token_budget,
                "budget_remaining": assembly.budget_remaining,
                "context_trace": [asdict(trace) for trace in assembly.context_trace],
            },
            "prompt_version": prompt_version,
            "prompt_digest": hashlib.blake2b(
                system_prompt.encode("utf-8"), digest_size=32
            ).hexdigest(),
            "request_digest": hashlib.blake2b(
                request_bytes, digest_size=32
            ).hexdigest(),
            "request_bytes": len(request_bytes),
            "system_prompt_bytes": len(system_prompt.encode("utf-8")),
            "budget_reminder_present": "<context-budget-reminder>" in system_prompt,
        },
        **hook_correlation_fields(correlation),
    )


def _request_options_payload(options: StreamOptions | None) -> dict[str, object]:
    if options is None:
        return {}
    return {
        option.name: _request_option_value(value)
        for option in fields(options)
        if option.name not in _TRANSPORT_OPTIONS
        and (value := getattr(options, option.name)) is not None
    }


def _request_option_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _request_option_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_request_option_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _request_option_value(getattr(value, item.name))
            for item in fields(value)
        }
    return f"<{type(value).__module__}.{type(value).__qualname__}>"
