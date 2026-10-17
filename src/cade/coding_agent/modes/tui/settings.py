from __future__ import annotations

from pathlib import Path
from typing import TypeGuard

from cade.ai.models import parse_model_mode
from cade.ai.resolver import ModelResolver
from cade.coding_agent.interaction.app_contract import ModelControlApp
from cade.coding_agent.interaction.context_stats import compute_context_summary
from cade.coding_agent.interaction.models import (
    AvailableModelEntry,
    get_available_model_entries,
)
from cade.coding_agent.interaction.reasoning_effort import (
    reasoning_effort_levels_for_transport,
)
from cade.coding_agent.interaction.token_count import (
    format_token_count,
    parse_token_count,
)
from cade.harness.agent_runtime.config import resolve_context_policy
from cade.harness.security import (
    FileGrantStore,
    InMemoryGrantStore,
    PermissionPolicy,
)


def _is_model_control_app(app: object) -> TypeGuard[ModelControlApp]:
    return hasattr(app, "get_model_info") and hasattr(app, "set_model")


def _model_info(app: object) -> dict[str, str]:
    return app.get_model_info() if _is_model_control_app(app) else {}


def _approval_info(app: object | None) -> tuple[str, str]:
    agent = getattr(app, "agent", None)
    policy = str(getattr(agent, "approval_policy", "on-request"))
    reviewer = str(getattr(agent, "approvals_reviewer", "user"))
    return policy, reviewer


def handle_permissions(
    command: str,
    session_grant_store: InMemoryGrantStore | None,
    permanent_grant_store: FileGrantStore | None,
    static_policy: PermissionPolicy | None = None,
    restricted_dirs: tuple[str, ...] = (),
    app: object | None = None,
) -> None:
    """列出或清除权限规则与授权；不带参数时等同 list。"""
    parts = command.split(maxsplit=2)
    sub = parts[1] if len(parts) >= 2 else ""
    if sub == "clear" and session_grant_store is not None:
        session_grant_store.clear()
        print("Session permissions cleared.")
        return
    if sub and sub != "list":
        print("Usage: /permissions [list|clear]")
        return
    list_permissions(
        session_grant_store,
        permanent_grant_store,
        static_policy,
        restricted_dirs,
        approval_info=_approval_info(app),
    )


def _format_rules(rules: tuple) -> list[str]:
    lines = []
    for rule in rules:
        input_part = _format_rule_input(rule)
        target_part = _format_rule_target(rule)
        decision = "review" if rule.decision == "ask" else rule.decision
        lines.append(f"  - tool `{rule.tool}` -> {decision}{input_part}{target_part}")
    return lines


def _format_rule_input(rule: object) -> str:
    parts = []
    ic = getattr(rule, "input_contains", None)
    ip = getattr(rule, "input_prefix", None)
    ir = getattr(rule, "input_regex", None)
    if ip:
        parts.append(f"prefix={ip}")
    elif ic:
        parts.append(f"contains={ic}")
    if ir:
        parts.append(f"regex={ir}")
    return f" ({'; '.join(parts)})" if parts else ""


def _format_rule_target(rule: object) -> str:
    target = getattr(rule, "target", None)
    target_type = getattr(rule, "target_type", None)
    if target and target_type:
        return f" [{target_type}:{target}]"
    if target:
        return f" [{target}]"
    if target_type:
        return f" [{target_type}]"
    return ""


def list_permissions(
    session_grant_store: InMemoryGrantStore | None,
    permanent_grant_store: FileGrantStore | None,
    static_policy: PermissionPolicy | None = None,
    restricted_dirs: tuple[str, ...] = (),
    approval_info: tuple[str, str] = ("on-request", "user"),
) -> None:
    approval_policy, approvals_reviewer = approval_info
    lines = [
        "Permission Status",
        "",
        (
            "Decision order: hard boundaries -> mode/static rules -> saved grants -> "
            "approval policy -> reviewer."
        ),
        f"Approval policy: {approval_policy}",
        f"Approval reviewer: {approvals_reviewer}",
        "",
    ]

    if static_policy is not None:
        if static_policy.rules:
            lines.append(f"Static rules ({len(static_policy.rules)})")
            lines.extend(_format_rules(static_policy.rules))
            lines.append("")
        else:
            lines.append("Static rules: none")
        if static_policy.global_default is not None:
            lines.append(f"Default from config: {static_policy.global_default}")
        else:
            lines.append("Default from config: not set")
    else:
        lines.append("Static rules: none")
        lines.append("Default from config: not set")
    lines.append("Implicit fallback: selected by the active execution mode")
    lines.append("")

    if restricted_dirs:
        lines.append(f"Restricted directories ({len(restricted_dirs)})")
        for d in restricted_dirs:
            lines.append(f"  - `{d}`")
        lines.append("")
    else:
        lines.append("Restricted directories: none")
        lines.append("")

    if session_grant_store is not None:
        records = session_grant_store.records()
        if records:
            lines.append(f"Session grants ({len(records)})")
            for rec in records:
                lines.append(_format_grant(rec))
            lines.append("")
        else:
            lines.append("Session grants: none")
            lines.append("")

    if permanent_grant_store is not None:
        records = permanent_grant_store.records()
        if records:
            lines.append(f"Persistent grants ({len(records)})")
            for rec in records:
                lines.append(_format_grant(rec))
            lines.append("")
        else:
            lines.append("Persistent grants: none")
            lines.append("")

    lines.append("Use `/permissions clear` to remove session grants.")
    print("\n".join(lines))


def _format_grant(record: object) -> str:
    """格式化 GrantRecord 为可读行。"""
    cap = getattr(record, "capability", "?")
    op = getattr(record, "operation", "?")
    target = getattr(record, "target_pattern", "?")
    acc = getattr(record, "access", "?")
    dec = getattr(record, "decision", "?")
    target_kind = getattr(record, "target_kind", "?")
    scope = getattr(record, "scope", "?")
    grant_id = getattr(record, "grant_id", "?")
    return (
        f"  - {dec} {cap}/{op} {acc} on {target_kind} `{target}` "
        f"({scope}, id={grant_id})"
    )


def _format_model_entry(
    entry: AvailableModelEntry,
) -> str:
    """统一显示模型名称和凭据来源标签。"""
    return f"{entry.model:20} {entry.source_label}"


def handle_model_command(command: str, app: object) -> None:
    parts = command.split(maxsplit=3)
    if len(parts) == 1:
        info = _model_info(app)
        if info:
            print(f"  Model    : {info.get('model', 'unknown')}")
            print(f"  Transport: {info.get('transport', 'unknown')}")
            print(f"  Base URL : {info.get('base_url', '')}")
        else:
            print("Model info not available.")
        available = get_available_model_entries(app)
        if available:
            print("\nAvailable models (authenticated/configured):")
            for entry in available:
                print("  - " + _format_model_entry(entry))
        print("\nUsage: /model <model_name>")
        return

    try:
        parsed = parse_model_mode(parts[1])
    except ValueError as exc:
        print(str(exc))
        return

    resolution = ModelResolver.resolve(
        model_or_alias=parsed.model,
        provider=parsed.provider,
    )
    model_name = resolution.model
    transport: str | None = resolution.transport

    profile = "main"
    thinking: bool | None = None
    reasoning_effort: str | None = None
    if parsed.thinking_level is not None:
        if parsed.thinking_level == "off":
            thinking = False
            reasoning_effort = None
        else:
            thinking = True
            reasoning_effort = parsed.thinking_level

    if len(parts) >= 4 and parts[2] != "--thinking":
        print(f"Warning: unrecognized option '{parts[2]}' ignored.")
    if len(parts) >= 4 and parts[2] == "--thinking":
        level = parts[3].lower()
        if level not in (
            "off",
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            print(
                f"Invalid thinking level: {level}. "
                "Use off/none/minimal/low/medium/high/xhigh/max."
            )
            return
        if level == "off":
            thinking = False
            reasoning_effort = None
        else:
            thinking = True
            reasoning_effort = level

    if not _is_model_control_app(app):
        print("Model switching is not supported in this app.")
        return

    if reasoning_effort is not None:
        supported_levels = reasoning_effort_levels_for_transport(
            transport or "",
            model_name,
        )
        if supported_levels and reasoning_effort not in supported_levels:
            print(
                f"Invalid effort level for {model_name}. "
                f"Use: {'/'.join(supported_levels)}"
            )
            return

    try:
        cap = _context_window_cap(_model_info(app))
        new_model = app.set_model(
            model=f"{parsed.provider}/{model_name}" if parsed.provider else model_name,
            transport=transport,
            profile=profile,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        info = _model_info(app)
        t_name = info.get("transport", transport or "")
        t_info = f" (transport: {t_name})" if t_name else ""
        print(f"Switched to model: {new_model}{t_info}")
        limit = int(info.get("model_context_window") or 0)
        if cap and limit and cap > limit and _context_window_cap(info) is None:
            print(
                f"Context window cap {format_token_count(cap)} exceeds this "
                f"model's {format_token_count(limit)}; using the model default."
            )
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Failed to switch model: {exc}")


def handle_effort_command(command: str, app: object) -> None:
    parts = command.split(maxsplit=1)
    info = _model_info(app)
    transport = info.get("transport", "") if info else ""
    model = info.get("model", "") if info else ""
    supported_levels = reasoning_effort_levels_for_transport(transport, model)
    if len(parts) == 1:
        current = info.get("reasoning_effort", "not set") if info else "unknown"
        print(f"  Reasoning effort: {current}")
        if supported_levels:
            print(f"  Supported: {'/'.join(supported_levels)}")
        else:
            print("  Supported: not available for current provider")
        return

    level = parts[1].lower()
    if not supported_levels:
        print("Current provider does not support reasoning effort.")
        return
    if level not in supported_levels:
        print(
            f"Invalid effort level for {transport or 'current provider'}. "
            f"Use: {'/'.join(supported_levels)}"
        )
        return

    if not _is_model_control_app(app):
        print("Model switching is not supported in this app.")
        return

    info = app.get_model_info()
    current_model = info.get("model", "unknown") if info else "unknown"

    try:
        app.set_model(
            model=current_model,
            thinking=(
                None
                if transport in {"openai_responses", "openai_codex"}
                else level != "off"
            ),
            reasoning_effort=level,
        )
        print(f"Reasoning effort set to: {level}")
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Failed to set reasoning effort: {exc}")


_CONTEXT_WINDOW_USAGE = (
    "Usage: /context-window <size|default>\n"
    "  size     token count: 200k, 200000, 1m, 1.5m (k=1,000, m=1,000,000)\n"
    "  default  use the model's own window\n"
    "Applies to this session only, from the next turn. Larger than the model "
    "supports is rejected. Use /config to save a default for new sessions."
)


def _context_window_cap(info: dict[str, str]) -> int | None:
    """返回显式设置的窗口上限；窗口等于模型默认值时视为未设置。"""
    window = info.get("context_window")
    if not window or window == info.get("model_context_window"):
        return None
    return int(window)


def _rollover_notice(app: object) -> str | None:
    """已用上下文达到新窗口的换窗阈值时，说明下一回合会自动换窗。"""
    agent = getattr(app, "agent", None)
    if agent is None:
        return None
    active = agent.provider
    policy = resolve_context_policy(active, agent.composition.config)
    snapshot = compute_context_summary(agent).snapshot
    used = snapshot.total_input_tokens if snapshot is not None else 0
    if not policy.automatic_rollover or used < policy.rotation_threshold:
        return None
    return (
        f"Current context (~{format_token_count(used)}) is above this window's "
        f"rollover threshold ({format_token_count(policy.rotation_threshold)}); "
        "the next turn will roll over into a fresh window with a NOTE.md handoff."
    )


def handle_context_window_command(
    command: str, app: object, project_root: Path
) -> None:
    """显示或设置当前会话的上下文窗口；只影响当前运行，不写配置文件。"""
    parts = command.split(maxsplit=1)
    info = _model_info(app)
    if len(parts) == 1:
        current = info.get("context_window")
        limit = info.get("model_context_window")
        print(
            "  Context window: "
            + (format_token_count(int(current)) if current else "unknown")
        )
        if limit:
            print(f"  Model limit   : {format_token_count(int(limit))}")
        print(_CONTEXT_WINDOW_USAGE)
        return

    text = parts[1].strip().lower()
    try:
        window = 0 if text == "default" else parse_token_count(text)
    except ValueError as exc:
        print(f"{exc}\n{_CONTEXT_WINDOW_USAGE}")
        return
    if not _is_model_control_app(app):
        print("Model switching is not supported in this app.")
        return
    try:
        app.set_model(model=info["model"], context_window=window)
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Failed to set context window: {exc}")
        return
    label = format_token_count(window) if window else "model default"
    print(f"Context window set to: {label}")
    notice = _rollover_notice(app)
    if notice:
        print(notice)


def handle_thinking_command(command: str, app: object) -> None:
    """仅控制 Responses 协议的推理摘要。"""
    info = _model_info(app)
    if info.get("transport") not in {"openai_responses", "openai_codex"}:
        print("/thinking controls Responses reasoning summaries only. Use /effort.")
        return
    parts = command.split(maxsplit=1)
    if len(parts) == 1:
        print(f"  Reasoning summary: {info.get('thinking', 'unknown')}")
        print(f"  Reasoning effort : {info.get('reasoning_effort', 'not set')}")
        return
    state = parts[1].lower()
    if state not in {"on", "off"}:
        print("Usage: /thinking on|off")
        return
    if not _is_model_control_app(app):
        print("Model switching is not supported in this app.")
        return
    try:
        app.set_model(model=info["model"], thinking=state == "on")
        label = "enabled" if state == "on" else "disabled"
        print(f"Reasoning summary {label}; effort unchanged.")
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Failed to set reasoning summary: {exc}")
