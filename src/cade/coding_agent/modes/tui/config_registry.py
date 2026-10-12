"""交互式配置注册表：声明 `/config` 可浏览和修改的设置项。

每个 `SettingSpec` 描述一行设置：点路径、展示名、值类型、说明与格式化/解析
规则。TUI 与配置命令共用同一份注册表，写入统一经过
`CadeRuntimeConfig` 校验后再落盘。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from enum import StrEnum
from operator import attrgetter
from pathlib import Path
from typing import Any

import questionary
from pydantic import ValidationError

from cade.ai.models import get_codex_models, get_models
from cade.ai.resolver import PROVIDER_TRANSPORTS, ModelResolver
from cade.coding_agent.assembly.config import default_env_files
from cade.coding_agent.interaction.models import configured_provider_names
from cade.coding_agent.interaction.reasoning_effort import (
    reasoning_effort_levels_for_transport,
)
from cade.coding_agent.interaction.token_count import (
    format_token_count,
    parse_token_count,
)
from cade.harness.config import CadeRuntimeConfig, load_runtime_config

from .ptk_patch import safe_select, safe_text
from .setup_wizard import _load_existing_config, _save_config

ChoicesSource = Callable[[CadeRuntimeConfig, tuple[Path, ...]], tuple[str, ...]]


class SettingKind(StrEnum):
    """设置项的编辑形态。"""

    BOOL = "bool"
    ENUM = "enum"
    INT = "int"
    FLOAT = "float"
    STR = "str"
    PATH = "path"
    STR_LIST = "str_list"
    INFO = "info"


@dataclass(frozen=True)
class SettingSpec:
    """单行设置的声明。"""

    key: str
    label: str
    kind: SettingKind
    description: str = ""
    choices: tuple[str, ...] = ()
    nullable: bool = False
    none_choice: str | None = None
    getter: Callable[[CadeRuntimeConfig], Any] | None = None
    formatter: Callable[[Any], str] | None = None
    choice_descriptions: dict[str, str] = dataclasses_field(default_factory=dict)
    writer: Callable[[dict[str, Any], Any], None] | None = None
    parser: Callable[[str], Any] | None = None
    # 动态选项来源：依据当前配置与 .env 位置计算可选值（如已配置的 provider）
    choices_source: ChoicesSource | None = None
    # 动态清单不含当前值时，是否把当前值补进菜单并标记 (current)
    pin_current: bool = True
    # 菜单末尾是否提供"其他"入口，允许输入清单之外的值
    allows_custom: bool = False

    @property
    def is_menu(self) -> bool:
        """是否以选择菜单编辑：布尔、枚举或声明了动态选项。"""
        return self.kind in (SettingKind.BOOL, SettingKind.ENUM) or (
            self.choices_source is not None
        )

    def read(self, config: CadeRuntimeConfig) -> Any:
        """从生效配置中读取当前值；INFO/复合行必须提供自定义 getter。"""
        if self.getter is not None:
            return self.getter(config)
        return attrgetter(self.key)(config)

    def describe_choice(self, token: str) -> str:
        """返回枚举选项的灰色说明文本；未声明时回退到整体说明。"""
        return self.choice_descriptions.get(token) or self.description


_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})
_CLEAR_TOKENS = frozenset({"none", "unset", "default", "unlimited"})


def _read_approval_choice(config: CadeRuntimeConfig) -> str:
    """把 approval_policy + approval_router 组合读回三选项 token。"""
    if config.security.approval_policy == "never":
        return "always proceeds"
    router = config.security.approval_router
    if router == "auto":
        return "agent decides"
    if router == "user":
        return "asks for review"
    return (
        "agent decides"
        if config.execution_modes.default_mode == "build"
        else ("asks for review")
    )


def _write_approval_choice(raw: dict[str, Any], value: Any) -> None:
    """把三选项 token 落到 approval_policy + approval_router 两个字段。"""
    security = raw.setdefault("security", {})
    if not isinstance(security, dict):
        security = {}
        raw["security"] = security
    if value == "always proceeds":
        security["approval_policy"] = "never"
        security.pop("approval_router", None)
    elif value == "agent decides":
        security["approval_policy"] = "on-request"
        security["approval_router"] = "auto"
    else:
        security["approval_policy"] = "on-request"
        security["approval_router"] = "user"


def _transport_for_provider(config: CadeRuntimeConfig, provider: str) -> str:
    """返回 provider 对应的 transport；自定义连接优先于内置映射。"""
    connection = config.provider.connections.get(provider)
    if connection is not None:
        return connection.transport
    return PROVIDER_TRANSPORTS.get(provider, "")


def _default_provider_name(config: CadeRuntimeConfig) -> str:
    return ModelResolver.normalize_provider(config.default_provider) or ""


def _provider_choices(
    config: CadeRuntimeConfig, env_files: tuple[Path, ...]
) -> tuple[str, ...]:
    """仅列出已配置凭据（登录或 API Key）的 provider。"""
    return configured_provider_names(config, env_files)


def _model_choices(
    config: CadeRuntimeConfig, _env_files: tuple[Path, ...]
) -> tuple[str, ...]:
    """默认 provider 下已注册的模型 ID；ChatGPT 登录只允许 Codex 清单。"""
    provider = _default_provider_name(config)
    if _transport_for_provider(config, provider) == "openai_codex":
        models = get_codex_models()
    else:
        models = get_models(provider)
    return tuple(model.id for model in models)


def _effort_choices(
    config: CadeRuntimeConfig, _env_files: tuple[Path, ...]
) -> tuple[str, ...]:
    """默认 provider 与默认模型支持的 reasoning effort 档位。"""
    transport = _transport_for_provider(config, _default_provider_name(config))
    return reasoning_effort_levels_for_transport(transport, config.default_model)


def _follow_default_provider(raw: dict[str, Any], value: Any) -> None:
    """切换默认 provider 时，把不再适用的模型和推理档位一并修正。

    模型不在新 provider 清单里时改为清单首项；推理档位不被新模型支持时清除。
    raw 无法通过校验时只写入 provider，由调用方的校验给出错误。
    """
    raw["default_provider"] = value
    try:
        config = CadeRuntimeConfig.model_validate(raw)
    except ValidationError:
        return
    models = _model_choices(config, ())
    if models and config.default_model not in models:
        raw["default_model"] = models[0]
        config = CadeRuntimeConfig.model_validate(raw)
    effort = config.default_reasoning_effort
    if effort is not None and effort not in _effort_choices(config, ()):
        raw["default_reasoning_effort"] = None


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="default_provider",
        label="Default Provider",
        kind=SettingKind.STR,
        description="Provider for new sessions; credentials are stored separately.",
        choices_source=_provider_choices,
        pin_current=False,
        writer=_follow_default_provider,
    ),
    SettingSpec(
        key="default_model",
        label="Default Model",
        kind=SettingKind.STR,
        description="Model ID for new sessions on the selected default provider.",
        choices_source=_model_choices,
        allows_custom=True,
    ),
    SettingSpec(
        key="default_reasoning_effort",
        label="Default Reasoning Effort",
        kind=SettingKind.STR,
        nullable=True,
        description="Startup reasoning effort supported by the selected model.",
        choices_source=_effort_choices,
    ),
    SettingSpec(
        key="provider.options.context_window",
        label="Context Window",
        kind=SettingKind.INT,
        nullable=True,
        description=(
            "Context window cap for new sessions, e.g. 200k or 1m; 'none' uses "
            "the model's own window. Use /context-window to change the "
            "running session."
        ),
        formatter=lambda value: (
            "(model default)" if value is None else format_token_count(value)
        ),
        parser=parse_token_count,
    ),
    SettingSpec(
        key="execution_modes.default_mode",
        label="Default Mode",
        kind=SettingKind.ENUM,
        choices=("act", "build", "plan"),
        description=(
            "Behavior for writes and shell commands in new sessions. "
            "'act': read allowed, writes and shell ask the user. "
            "'build': workspace mutations auto-run, boundary actions "
            "auto-reviewed. 'plan': research and plan without making changes."
        ),
        choice_descriptions={
            "act": "Maximizes interactivity: every write or shell command asks.",
            "build": (
                "Maximizes autonomy inside the workspace; risky actions are "
                "reviewed by a reviewer model."
            ),
            "plan": "Read-only research mode; nothing is written to disk.",
        },
    ),
    SettingSpec(
        key="provider.options.thinking",
        label="Thinking",
        kind=SettingKind.BOOL,
        description="Whether model requests in new sessions enable thinking.",
    ),
    SettingSpec(
        key="provider.options.tool_stream",
        label="Tool Streaming",
        kind=SettingKind.BOOL,
        description=(
            "Stream tool-call arguments as they are generated, where the provider "
            "supports it."
        ),
    ),
    SettingSpec(
        key="provider.options.clear_thinking",
        label="Clear Thinking",
        kind=SettingKind.BOOL,
        description="Clear earlier thinking from requests, where the provider supports it.",
    ),
    SettingSpec(
        key="agent.automatic_rollover",
        label="Automatic Rollover",
        kind=SettingKind.BOOL,
        description=(
            "Roll over into a fresh context window with a NOTE.md handoff when the "
            "context reaches the threshold."
        ),
    ),
    SettingSpec(
        key="agent.max_steps",
        label="Max Steps",
        kind=SettingKind.INT,
        nullable=True,
        description="Agent steps allowed per turn; 'none' removes the limit.",
    ),
    SettingSpec(
        key="agent.max_llm_calls",
        label="Max LLM Calls",
        kind=SettingKind.INT,
        nullable=True,
        description="Model requests allowed per turn; 'none' removes the limit.",
    ),
    SettingSpec(
        key="agent.tool_workers",
        label="Tool Workers",
        kind=SettingKind.INT,
        description="Maximum tool calls run in parallel within one step.",
    ),
    SettingSpec(
        key="agent.tool_timeout_seconds",
        label="Tool Timeout (s)",
        kind=SettingKind.FLOAT,
        description="Seconds a single tool call may run before it times out.",
    ),
    SettingSpec(
        key="agent.watchdog_repeated_tool_limit",
        label="Repeated Tool Limit",
        kind=SettingKind.INT,
        description=(
            "Identical tool calls in a row before the agent is stopped; 0 disables "
            "the check."
        ),
    ),
    SettingSpec(
        key="security.auto_review_timeout_seconds",
        label="Auto Review Timeout (s)",
        kind=SettingKind.FLOAT,
        description=(
            "Seconds the reviewer model may take on one boundary action; "
            "must be at most 300."
        ),
    ),
    SettingSpec(
        key="skills.trust_project_skills",
        label="Trust Project Skills",
        kind=SettingKind.BOOL,
        description=(
            "Load skills from the project's .cade/skills and .agents/skills folders."
        ),
    ),
    SettingSpec(
        key="security.approval_policy",
        label="Approval Policy",
        kind=SettingKind.ENUM,
        choices=("always proceeds", "agent decides", "asks for review"),
        description=(
            "What happens when the agent wants to edit files or run commands "
            "beyond the current mode's free tier. 'always proceeds': never "
            "asks (maximizes autonomy, risk of unsafe actions). "
            "'agent decides': a reviewer model reviews based on complexity. "
            "'asks for review': always asks for your confirmation."
        ),
        choice_descriptions={
            "always proceeds": (
                "Agent never asks; boundary actions run without review."
            ),
            "agent decides": (
                "Reviewer model approves low-risk actions and escalates risky ones."
            ),
            "asks for review": (
                "Every boundary action waits for explicit user approval."
            ),
        },
        getter=_read_approval_choice,
        writer=_write_approval_choice,
    ),
    SettingSpec(
        key="security.non_workspace_access",
        label="Non-Workspace Access",
        kind=SettingKind.BOOL,
        description=(
            "Whether directories outside the workspace may be accessed at "
            "all. When off, the external directory whitelist is ignored."
        ),
        choice_descriptions={
            "on": "External paths follow the security.external_directories whitelist.",
            "off": "Everything outside the workspace is denied.",
        },
    ),
    SettingSpec(
        key="security.sandbox.mode",
        label="Sandbox Mode",
        kind=SettingKind.ENUM,
        choices=("workspace-write", "read-only", "danger-full-access"),
        description="Linux OS sandbox policy used by Agent shell commands.",
        choice_descriptions={
            "workspace-write": (
                "Project and approved writable roots can change; other host paths "
                "are read-only."
            ),
            "read-only": "The shell cannot write to host filesystem paths.",
            "danger-full-access": "The shell can write anywhere as the current user.",
        },
    ),
    SettingSpec(
        key="security.sandbox.network_access",
        label="Sandbox Network",
        kind=SettingKind.ENUM,
        choices=("deny", "allow"),
        description="Whether Linux sandboxed shell commands can use host networking.",
        choice_descriptions={
            "deny": "Create an isolated network namespace with no external access.",
            "allow": "Keep the host network namespace.",
        },
    ),
    SettingSpec(
        key="tools.shell",
        label="Shell",
        kind=SettingKind.ENUM,
        choices=("auto", "bash", "zsh", "sh", "fish", "pwsh", "powershell", "cmd"),
        description=(
            "Shell used by the bash tool. 'auto' detects the login shell at "
            "runtime; pick an explicit value to pin behavior across machines."
        ),
    ),
)

_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}


def find_setting(query: str) -> SettingSpec | None:
    """按标签或点路径查找设置项；无歧义时返回，否则 None。"""
    needle = query.strip().lower()
    if not needle:
        return None
    exact = _BY_KEY.get(needle)
    if exact is not None:
        return exact
    matches = [
        spec
        for spec in SETTING_SPECS
        if needle in spec.label.lower() or needle in spec.key
    ]
    return matches[0] if len(matches) == 1 else None


def matching_settings(query: str) -> list[SettingSpec]:
    """返回所有标签或路径包含查询词的设置项。"""
    needle = query.strip().lower()
    if not needle:
        return []
    return [
        spec
        for spec in SETTING_SPECS
        if needle in spec.label.lower() or needle in spec.key
    ]


def setting_detail(spec: SettingSpec, config: CadeRuntimeConfig) -> list[str]:
    """INFO 行展开的多行详情；无自定义详情时回退到值的字符串形式。"""
    return [f"  {spec.read(config)}"]


def format_setting(spec: SettingSpec, config: CadeRuntimeConfig) -> str:
    """渲染当前生效值的展示文本。"""
    value = spec.read(config)
    if spec.formatter is not None:
        return spec.formatter(value)
    if value is None:
        return "(unset)"
    if spec.kind is SettingKind.BOOL:
        return "on" if value else "off"
    if isinstance(value, float | int):
        return f"{float(value):g}"
    if isinstance(value, tuple | list):
        items = list(value)
        return ", ".join(items) if items else "(none)"
    return str(value)


def parse_setting(spec: SettingSpec, text: str) -> Any:
    """把用户输入解析为目标 Python 值；非法输入抛 ValueError。"""
    stripped = text.strip()
    lowered = stripped.lower()
    clearable = spec.nullable or spec.none_choice is not None
    if lowered in _CLEAR_TOKENS and clearable:
        return None
    if spec.kind is SettingKind.BOOL:
        if lowered in _TRUE_TOKENS:
            return True
        if lowered in _FALSE_TOKENS:
            return False
        raise ValueError(f"Expected on/off, got: {text!r}")
    if spec.kind is SettingKind.ENUM:
        if lowered not in spec.choices:
            expected = "/".join(spec.choices)
            raise ValueError(f"Expected {expected}, got: {text!r}")
        return None if lowered == spec.none_choice else lowered
    if spec.parser is not None:
        return spec.parser(stripped)
    if spec.kind is SettingKind.INT:
        return int(stripped)
    if spec.kind is SettingKind.FLOAT:
        return float(stripped)
    if spec.kind is SettingKind.PATH:
        if not stripped:
            raise ValueError("Path cannot be empty; use 'none' to clear.")
        return stripped
    if spec.kind is SettingKind.STR_LIST:
        parts = [part.strip() for part in stripped.split(",")]
        items = tuple(part for part in parts if part)
        return items or None
    if spec.kind is SettingKind.STR:
        if not stripped:
            raise ValueError("Value cannot be empty.")
        return stripped
    raise ValueError(f"Setting '{spec.key}' does not accept text input.")


def apply_setting(raw: dict[str, Any], spec: SettingSpec, value: Any) -> None:
    """把解析后的值写进原始 dict；None 表示清除该键以恢复默认。

    复合行（writer 非空）由 writer 决定写入哪些字段。
    """
    if spec.writer is not None:
        spec.writer(raw, value)
        return
    parts = spec.key.split(".")
    node = raw
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    leaf = parts[-1]
    restores_default = value is None or (
        spec.kind is SettingKind.STR_LIST and not value
    )
    if restores_default:
        node.pop(leaf, None)
    else:
        node[leaf] = value


def _first_validation_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    path = ".".join(str(part) for part in error["loc"])
    return f"{path}: {error['msg']}" if path else error["msg"]


def commit_setting_value(
    config_path: Path, spec: SettingSpec, value: Any
) -> tuple[bool, str]:
    """写入并校验；校验失败时不落盘。返回 (是否成功, 反馈消息)。"""
    try:
        raw = _load_existing_config(config_path)
    except (json.JSONDecodeError, ValueError) as exc:
        return False, f"Cannot read {config_path.name}: {exc}"

    apply_setting(raw, spec, value)
    try:
        CadeRuntimeConfig.model_validate(raw)
    except ValidationError as exc:
        reason = _first_validation_error(exc)
        return False, f"Rejected {spec.label}: {reason}"

    _save_config(raw, config_path)
    display = format_setting(spec, CadeRuntimeConfig.model_validate(raw))
    return True, f"{spec.label} set to {display} (saved to {config_path.name})"


def save_setting_text(
    config_path: Path, spec: SettingSpec, text: str
) -> tuple[bool, str]:
    """解析用户输入后提交；返回 (是否成功, 反馈消息)。"""
    if spec.kind is SettingKind.INFO:
        return False, f"'{spec.label}' is read-only here; edit JSON directly."
    try:
        value = parse_setting(spec, text)
    except ValueError as exc:
        return False, f"Invalid value for {spec.label}: {exc}"
    return commit_setting_value(config_path, spec, value)


def load_effective_config(config_path: Path) -> CadeRuntimeConfig:
    """加载用于展示的生效配置（文件内容 + 环境覆盖，缺失用默认值）。"""
    return load_runtime_config(config_path)


def _row_title(spec: SettingSpec, config: CadeRuntimeConfig) -> str:
    return f"{spec.label:<28}{format_setting(spec, config)}"


CUSTOM_TITLE = "other…"


@dataclass(frozen=True)
class MenuItem:
    """选择菜单的一行。custom 行不带值，选中后改用文本输入。"""

    title: str
    value: Any = None
    description: str = ""
    custom: bool = False


def setting_choices(
    spec: SettingSpec, config: CadeRuntimeConfig, env_files: tuple[Path, ...]
) -> tuple[str, ...]:
    """枚举类设置的可选值；动态清单不含当前值时按 pin_current 决定是否补入。"""
    options = (
        spec.choices_source(config, env_files)
        if spec.choices_source is not None
        else spec.choices
    )
    current = spec.read(config)
    if (
        spec.pin_current
        and isinstance(current, str)
        and current
        and current not in options
    ):
        return (current, *options)
    return tuple(options)


def setting_menu(
    spec: SettingSpec, config: CadeRuntimeConfig, env_files: tuple[Path, ...]
) -> list[MenuItem]:
    """为菜单类设置生成选项行；当前值标记为 (current)，可清除项为 (unset)。

    没有任何可选值时返回空列表，由调用方提示用户先完成凭据配置。
    """
    current = spec.read(config)

    def titled(token: str, is_current: bool) -> str:
        return f"{token} (current)" if is_current else token

    items: list[MenuItem] = []
    if spec.kind is SettingKind.BOOL:
        for value, token in ((True, "on"), (False, "off")):
            items.append(
                MenuItem(
                    title=titled(token, current == value),
                    value=value,
                    description=spec.describe_choice(token),
                )
            )
    else:
        for token in setting_choices(spec, config, env_files):
            items.append(
                MenuItem(
                    title=titled(token, current == token),
                    value=token,
                    description=spec.describe_choice(token),
                )
            )
    if not items and not spec.allows_custom:
        return []
    if spec.nullable:
        items.append(
            MenuItem(
                title=titled("(unset)", current is None),
                value=None,
                description="Clear the saved value; the built-in default applies.",
            )
        )
    if spec.allows_custom:
        items.append(
            MenuItem(
                title=CUSTOM_TITLE,
                description="Type a value that is not listed.",
                custom=True,
            )
        )
    return items


def _ask_text(spec: SettingSpec, config_path: Path) -> tuple[bool, str] | None:
    """文本输入一个设置值；取消或留空返回 None。"""
    hint = "Type value, enter to save, esc to cancel"
    if spec.nullable:
        hint += "; 'none' clears"
    text = safe_text(f"{spec.label} — {hint}:", default="")
    if text is None or not text.strip():
        return None
    return save_setting_text(config_path, spec, text)


def _choose_menu_item(
    spec: SettingSpec, config: CadeRuntimeConfig, env_files: tuple[Path, ...]
) -> MenuItem | None:
    """弹出选择菜单；取消返回 None。无可选项时打印提示并返回 None。"""
    items = setting_menu(spec, config, env_files)
    if not items:
        print(f"  No options available for {spec.label}; set up credentials first.")
        return None
    choices = [questionary.Choice(title=item.title, value=item) for item in items]
    picked = safe_select(f"{spec.label}:", choices=choices)
    return picked if isinstance(picked, MenuItem) else None


def edit_setting_interactive(
    config_path: Path, spec: SettingSpec, config: CadeRuntimeConfig
) -> None:
    """单个设置项的编辑流程：菜单选值或文本输入。"""
    current = format_setting(spec, config)
    if spec.kind is SettingKind.INFO:
        print(f"{spec.label} ({current}):")
        if spec.description:
            print(f"  {spec.description}")
        for line in setting_detail(spec, config):
            print(line)
        return

    print(f"  {spec.key} = {current}")
    if spec.description:
        print(f"  {spec.description}")

    if not spec.is_menu:
        result = _ask_text(spec, config_path)
    else:
        item = _choose_menu_item(spec, config, default_env_files(config_path.parent))
        if item is None:
            return
        if item.custom:
            result = _ask_text(spec, config_path)
        else:
            result = commit_setting_value(config_path, spec, item.value)

    if result is None:
        return
    saved, message = result
    prefix = "  " if saved else "  ! "
    print(f"{prefix}{message}")


def run_config_browser(config_path: Path) -> None:
    """交互式配置浏览器：回车进入编辑，esc 返回上级或退出。"""
    while True:
        config = load_effective_config(config_path)
        choices = [
            questionary.Choice(title=_row_title(spec, config), value=spec)
            for spec in SETTING_SPECS
        ]
        choices.append(questionary.Choice(title="Exit", value=None))
        selected = safe_select(
            f"Config ({config_path.name}) — enter to change, esc to exit:",
            choices=choices,
        )
        if selected is None:
            return
        edit_setting_interactive(config_path, selected, config)


__all__ = [
    "SETTING_SPECS",
    "MenuItem",
    "SettingKind",
    "SettingSpec",
    "apply_setting",
    "commit_setting_value",
    "edit_setting_interactive",
    "find_setting",
    "format_setting",
    "load_effective_config",
    "matching_settings",
    "parse_setting",
    "run_config_browser",
    "save_setting_text",
    "setting_choices",
    "setting_detail",
    "setting_menu",
]
