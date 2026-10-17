"""MCP 工具注册与配置。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from cade.agent.types import ToolInput, ToolSpec

from . import client as _mcp_mod
from .results import convert_mcp_tool_result

_log = logging.getLogger(__name__)


# ── 数据模型 ──


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    command: tuple[str, ...]
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    enabled: bool = True
    timeout: float | None = None

    @field_validator("command", mode="before")
    @classmethod
    def _coerce_command(cls, v: object) -> object:
        if isinstance(v, str):
            return (v,)
        return v

    @field_validator("command")
    @classmethod
    def _validate_command(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v or not v[0].strip():
            raise ValueError("command must be a non-empty string")
        return v

    @field_validator("timeout", mode="before")
    @classmethod
    def _coerce_timeout(cls, v: object) -> object:
        if not isinstance(v, (int, float)):
            return None
        return v

    @field_validator("env", mode="before")
    @classmethod
    def _coerce_env(cls, v: object) -> object:
        if not isinstance(v, dict):
            return None
        return v

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, v: object) -> object:
        if not isinstance(v, list | tuple):
            return ()
        return v


@dataclass(frozen=True)
class McpToolMetadata:
    """ToolSpec 中保留的 MCP 工具元数据。"""

    server_name: str
    server_slug: str
    tool_name: str
    tool_slug: str
    host_tool_id: str


class McpToolOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str | None = None
    read_only: bool | None = None
    concurrency_safe: bool | None = None
    enabled: bool | None = None
    description: str | None = None


@dataclass(frozen=True)
class McpServerStatus:
    """供 REPL 与诊断读取的 MCP server 运行时状态。"""

    server_name: str
    state: str
    enabled: bool
    tool_count: int
    protocol_version: str | None = None
    server_info: dict[str, Any] | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class _ServerRuntime:
    """同一 server 的配置、发现结果与客户端所有权。"""

    raw: dict[str, Any] | None = None
    validated: McpServerConfig | None = None
    tool_count: int = 0
    cache_metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    client_ref: _mcp_mod.LazyClientRef | None = None


class McpRuntimeRegistry:
    """管理 MCP 发现缓存、状态和持久客户端生命周期。"""

    def __init__(self) -> None:
        """初始化空的运行时注册状态。"""
        self._lock = threading.Lock()
        self._servers: dict[str, _ServerRuntime] = {}
        self._workspace_roots: tuple[Path, ...] = ()
        self._cancel_event: threading.Event | None = None

    def configure_runtime(
        self,
        *,
        workspace_roots: tuple[Path, ...],
        cancel_event: threading.Event | None,
    ) -> None:
        """配置与当前宿主运行绑定的共享上下文。"""
        with self._lock:
            self._workspace_roots = tuple(
                path.resolve() for path in workspace_roots if path.exists()
            )
            self._cancel_event = cancel_event

    def update_runtime_snapshot(
        self,
        *,
        raw_servers: dict[str, dict[str, Any]],
        validated_servers: dict[str, McpServerConfig],
        tool_counts: dict[str, int],
        cache_metadata: dict[str, dict[str, Any]],
        server_errors: dict[str, str],
    ) -> None:
        """保存当前配置与发现结果，供状态接口读取。"""
        with self._lock:
            # 配置移除后不展示 server，但其客户端仍需随运行时关闭。
            servers = {
                name: _ServerRuntime(client_ref=server.client_ref)
                for name, server in self._servers.items()
                if server.client_ref is not None
            }
            for name, raw in raw_servers.items():
                previous = servers.get(name)
                servers[name] = _ServerRuntime(
                    raw=raw,
                    validated=validated_servers.get(name),
                    tool_count=tool_counts.get(name, 0),
                    cache_metadata=cache_metadata.get(name, {}),
                    error=server_errors.get(name),
                    client_ref=previous.client_ref if previous is not None else None,
                )
            self._servers = servers

    def update_discovery(
        self, server_name: str, tool_count: int, cache_metadata: dict[str, Any]
    ) -> None:
        """更新一个 server 的发现结果，不重新发布配置或客户端。"""
        with self._lock:
            server = self._servers[server_name]
            self._servers[server_name] = replace(
                server, tool_count=tool_count, cache_metadata=cache_metadata
            )

    def status_snapshot(self) -> tuple[McpServerStatus, ...]:
        """返回所有已配置 server 的只读运行时状态。"""
        with self._lock:
            servers = dict(self._servers)
        statuses: list[McpServerStatus] = []
        for server_name, server in servers.items():
            raw = server.raw
            if raw is None:
                continue
            enabled = bool(raw.get("enabled", True))
            last_error = server.error
            ref = server.client_ref
            protocol_version = None
            server_info = None
            state = "configured"
            if (
                ref is not None
                and ref.client is not None
                and ref.client.status == "connected"
            ):
                state = "connected"
                protocol_version = ref.client.protocol_version
                server_info = ref.client.server_info
                last_error = ref.last_error or last_error
            else:
                cached = server.cache_metadata
                protocol_version = cached.get("protocol_version")
                server_info = cached.get("server_info")
                if last_error or (ref is not None and ref.last_error):
                    state = "failed"
                    if ref is not None and ref.last_error:
                        last_error = ref.last_error
            if server.validated is None:
                state = "failed"
            statuses.append(
                McpServerStatus(
                    server_name=server_name,
                    state=state,
                    enabled=enabled,
                    tool_count=server.tool_count,
                    protocol_version=protocol_version,
                    server_info=server_info,
                    last_error=last_error,
                )
            )
        return tuple(sorted(statuses, key=lambda item: item.server_name))

    @property
    def workspace_roots(self) -> tuple[Path, ...]:
        with self._lock:
            return self._workspace_roots

    @property
    def cancel_event(self) -> threading.Event | None:
        with self._lock:
            return self._cancel_event

    def update_workspace_roots(self, workspace_roots: tuple[Path, ...]) -> None:
        """更新 roots 并通知所有已连接 server。"""
        normalized = tuple(path.resolve() for path in workspace_roots if path.exists())
        with self._lock:
            self._workspace_roots = normalized
            refs = tuple(
                server.client_ref
                for server in self._servers.values()
                if server.client_ref is not None
            )
        for ref in refs:
            ref.update_workspace_roots(normalized)

    def track_client_ref(self, server_name: str, ref: _mcp_mod.LazyClientRef) -> None:
        """登记需要随应用关闭的延迟客户端。"""
        with self._lock:
            server = self._servers.get(server_name, _ServerRuntime())
            self._servers[server_name] = replace(server, client_ref=ref)

    def close(self) -> None:
        """关闭所有已启动的 MCP 持久客户端。"""
        with self._lock:
            refs = tuple(
                server.client_ref
                for server in self._servers.values()
                if server.client_ref is not None
            )
            self._servers = {
                name: replace(server, client_ref=None)
                for name, server in self._servers.items()
                if server.raw is not None
            }
        for ref in refs:
            ref.stop()


# ── 名称清理 ──


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _warn(msg: str) -> None:
    _log.warning("MCP: %s", msg)


# ── 配置校验 ──


def _validate_server_config(name: str, raw: object) -> McpServerConfig | None:
    if not isinstance(raw, dict):
        _warn(f"server {name!r} config is not a dict; skipped")
        return None

    slug = _sanitize(name)
    if not slug:
        _warn(f"server {name!r} produces empty slug after sanitize; skipped")
        return None

    server_fields = McpServerConfig.model_fields
    filtered = {k: v for k, v in raw.items() if k in server_fields}
    try:
        return McpServerConfig.model_validate({"name": name, **filtered})
    except ValueError:
        _warn(f"server {name!r} config is invalid; skipped")
        return None


def _parse_overrides(raw: object) -> dict[str, McpToolOverride]:
    """解析宿主侧 tool overrides，仅接受显式白名单字段。"""
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, McpToolOverride] = {}
    for tool_name, tool_override in raw.items():
        if not isinstance(tool_name, str) or not isinstance(tool_override, dict):
            continue
        try:
            override = McpToolOverride.model_validate(tool_override)
        except ValueError:
            _log.debug("invalid MCP tool override for %s", tool_name, exc_info=True)
        else:
            overrides[tool_name] = override
    return overrides


def _tool_override(
    raw_server: dict[str, Any],
    tool_name: str,
) -> McpToolOverride:
    overrides = _parse_overrides(raw_server.get("overrides"))
    default_override = overrides.get("*", McpToolOverride())
    specific_override = overrides.get(tool_name, McpToolOverride())
    return McpToolOverride(
        risk=specific_override.risk or default_override.risk,
        read_only=(
            specific_override.read_only
            if specific_override.read_only is not None
            else default_override.read_only
        ),
        concurrency_safe=(
            specific_override.concurrency_safe
            if specific_override.concurrency_safe is not None
            else default_override.concurrency_safe
        ),
        enabled=(
            specific_override.enabled
            if specific_override.enabled is not None
            else default_override.enabled
        ),
        description=specific_override.description or default_override.description,
    )


def _warn_unknown_overrides(
    server_name: str,
    raw_server: dict[str, Any],
    tools: list[dict[str, Any]],
) -> None:
    """诊断无法匹配 server 工具目录的精确 override。"""
    overrides = _parse_overrides(raw_server.get("overrides"))
    known_names = {
        tool_name for tool in tools if isinstance((tool_name := tool.get("name")), str)
    }
    for tool_name in sorted(overrides.keys() - known_names - {"*"}):
        _warn(
            f"server {server_name!r} override references unknown tool "
            f"{tool_name!r}; ignored"
        )


# ── 配置与缓存路径 ──


CANONICAL_CONFIG_PATH = Path(".cade") / "mcp_config.json"


def _mcp_config_path(project_root: Path) -> Path | None:
    canonical = project_root / CANONICAL_CONFIG_PATH
    if canonical.exists():
        return canonical
    return None


def _cache_path(project_root: Path) -> Path:
    return project_root / ".cade" / "mcp_cache.json"


# ── 缓存工具 ──


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            _log.warning("failed to load MCP cache from %s", cache_path, exc_info=True)
    return {"servers": {}}


def _save_cache(cache_path: Path, cache_data: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── 配置哈希 ──


def compute_config_hash(server_config: dict[str, Any]) -> str:
    data = {
        "command": server_config.get("command"),
        "args": server_config.get("args", []),
        "env": server_config.get("env", {}),
    }
    timeout = server_config.get("timeout")
    if timeout is not None:
        data["timeout"] = timeout
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.blake2b(serialized.encode("utf-8"), digest_size=32).hexdigest()


def _cache_metadata(client: _mcp_mod.McpClient) -> dict[str, Any]:
    """提取可用于验证缓存来源的协商元数据。"""
    protocol_version = client.protocol_version
    if protocol_version is None:
        raise RuntimeError("MCP client has no negotiated protocol version")
    return {
        "protocol_version": protocol_version,
        "server_info": client.server_info,
    }


def _validated_cached_tools(
    cached_entry: object,
    config_hash: str,
) -> list[dict[str, Any]] | None:
    """返回来源可验证且配置未变化的缓存工具列表。"""
    if not isinstance(cached_entry, dict):
        return None
    if cached_entry.get("config_hash") != config_hash:
        return None
    if cached_entry.get("protocol_version") not in (
        _mcp_mod.SUPPORTED_PROTOCOL_VERSIONS
    ):
        return None
    server_info = cached_entry.get("server_info")
    if not isinstance(server_info, dict):
        return None
    server_name = server_info.get("name")
    server_version = server_info.get("version")
    if not isinstance(server_name, str) or not server_name.strip():
        return None
    if not isinstance(server_version, str) or not server_version.strip():
        return None
    tools = cached_entry.get("tools")
    return tools if isinstance(tools, list) else None


# ── 工具执行包装 ──


def _redact_and_truncate(text: str, max_len: int = 200) -> str:
    return _mcp_mod.truncate_redact(text, max_len=max_len)


def _make_handler(
    ref: _mcp_mod.LazyClientRef,
    original_tool_name: str,
    output_schema: object,
    runtime_registry: McpRuntimeRegistry,
) -> Any:
    def handler(
        args: ToolInput,
        on_update: Callable[[str], None] | None = None,
    ) -> str:
        client_instance = ref.get_or_create()
        progress_updates: list[dict[str, object]] = []

        def record_progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            update = {
                "progress": progress,
                "total": total,
                "message": message,
            }
            progress_updates.append(update)
            if on_update is not None:
                on_update(_format_progress_update(progress, total, message))

        response = client_instance.call_tool(
            original_tool_name,
            args,
            timeout=ref.config.get("timeout"),
            progress_callback=record_progress,
            cancel_event=runtime_registry.cancel_event,
        )
        result = convert_mcp_tool_result(response, output_schema)
        if not progress_updates:
            return result
        metadata = getattr(result, "metadata", {})
        merged = dict(metadata)
        merged["mcp_progress"] = progress_updates
        return type(result)(str(result), metadata=merged, is_error=result.is_error)

    return handler


def _format_progress_update(
    progress: float,
    total: float | None,
    message: str | None,
) -> str:
    """将 MCP progress notification 转为稳定的工具更新文本。"""
    position = f"{progress:g}"
    if total is not None:
        position = f"{position}/{total:g}"
    suffix = f" {message}" if message else ""
    return f"MCP progress {position}{suffix}"


# ── 碰撞检测 ──


def _detect_collisions(
    tools_by_server: dict[str, list[dict[str, Any]]],
) -> set[str]:
    """检测 host_tool_id 冲突，返回需要禁用的工具集合。

    集合元素为 “server_name:original_tool_name”，包含所有冲突工具。
    """
    host_ids: dict[str, list[tuple[str, str]]] = {}
    for server_name, tool_list in tools_by_server.items():
        server_slug = _sanitize(server_name)
        for tool in tool_list:
            tool_slug = _sanitize(tool["name"])
            host_id = f"mcp__{server_slug}__{tool_slug}"
            host_ids.setdefault(host_id, []).append((server_name, tool["name"]))

    disabled: set[str] = set()
    for host_id, entries in host_ids.items():
        if len(entries) > 1:
            identities = "; ".join(f"{s}:{t}" for s, t in entries)
            _warn(
                f"tool ID collision: {host_id!r} produced by "
                f"{identities}; all conflicting tools disabled"
            )
            for s, t in entries:
                disabled.add(f"{s}:{t}")

    return disabled


# ── 主入口 ──


def build_mcp_tools(
    project_root: Path,
    runtime_registry: McpRuntimeRegistry | None = None,
) -> tuple[ToolSpec, ...]:
    """在应用启动时发现并构建固定的 MCP 工具快照。"""
    runtime_registry = runtime_registry or McpRuntimeRegistry()
    config = _read_mcp_config(project_root)
    if config is None:
        runtime_registry.update_runtime_snapshot(
            raw_servers={},
            validated_servers={},
            tool_counts={},
            cache_metadata={},
            server_errors={},
        )
        return ()
    if config.error is not None:
        _warn(f"failed to load MCP config {config.config_path}: {config.error}")
        runtime_registry.update_runtime_snapshot(
            raw_servers={},
            validated_servers={},
            tool_counts={},
            cache_metadata={},
            server_errors={"<config>": _redact_and_truncate(config.error)},
        )
        return ()

    mcp_servers = config.servers
    validated_servers, server_errors = _validate_mcp_servers(mcp_servers)

    cache_path = _cache_path(project_root)
    cache_data = _load_cache(cache_path)
    collected = _collect_mcp_tools(
        mcp_servers,
        validated_servers,
        server_errors,
        cache_path,
        cache_data,
        runtime_registry,
    )

    refresh = _build_mcp_refresh_fn(
        validated_servers=validated_servers,
        mcp_servers=mcp_servers,
        cache_path=cache_path,
        runtime_registry=runtime_registry,
    )

    registered_tools = _build_runtime_mcp_tools(
        all_tools_by_server=collected.all_tools_by_server,
        validated_servers=validated_servers,
        raw_servers=mcp_servers,
        runtime_registry=runtime_registry,
        refresh_server_tools=refresh,
    )
    runtime_registry.update_runtime_snapshot(
        raw_servers=mcp_servers,
        validated_servers=validated_servers,
        tool_counts={
            name: len(tools) for name, tools in collected.all_tools_by_server.items()
        },
        cache_metadata=collected.cache_metadata_by_server,
        server_errors=server_errors,
    )
    return registered_tools


@dataclass(frozen=True)
class _McpConfigReadResult:
    """_read_mcp_config 的返回值。"""

    servers: dict[str, dict[str, Any]]
    config_path: Path | None
    error: str | None


def _read_mcp_config(project_root: Path) -> _McpConfigReadResult | None:
    """读取并解析 MCP 配置文件。返回 None 表示无配置。"""
    config_path = _mcp_config_path(project_root)
    if config_path is None:
        return None
    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        return _McpConfigReadResult(
            servers={},
            config_path=config_path,
            error=str(exc),
        )
    mcp_servers = {
        name: raw
        for name, raw in config_data.get("mcpServers", {}).items()
        if isinstance(name, str) and isinstance(raw, dict)
    }
    return _McpConfigReadResult(
        servers=mcp_servers,
        config_path=config_path,
        error=None,
    )


def _validate_mcp_servers(
    mcp_servers: dict[str, dict[str, Any]],
) -> tuple[dict[str, McpServerConfig], dict[str, str]]:
    """验证所有 MCP server 配置。"""
    validated_servers: dict[str, McpServerConfig] = {}
    server_errors: dict[str, str] = {}
    for server_name, raw in mcp_servers.items():
        validated = _validate_server_config(server_name, raw)
        if validated is not None:
            validated_servers[server_name] = validated
        else:
            server_errors[server_name] = "invalid config"
    return validated_servers, server_errors


@dataclass(frozen=True)
class _McpToolsCollection:
    """_collect_mcp_tools 的返回值。"""

    all_tools_by_server: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    cache_metadata_by_server: dict[str, dict[str, Any]] = field(default_factory=dict)


def _collect_mcp_tools(
    mcp_servers: dict[str, dict[str, Any]],
    validated_servers: dict[str, McpServerConfig],
    server_errors: dict[str, str],
    cache_path: Path,
    cache_data: dict[str, Any],
    runtime_registry: McpRuntimeRegistry,
) -> _McpToolsCollection:
    """遍历所有已验证的 MCP server，收集工具列表。"""
    all_tools_by_server: dict[str, list[dict[str, Any]]] = {}
    cache_metadata_by_server: dict[str, dict[str, Any]] = {}

    for server_name, validated in validated_servers.items():
        if not validated.enabled:
            continue
        tools_list = _tools_for_server(
            cache_path,
            cache_data,
            server_name,
            mcp_servers[server_name],
            validated,
            runtime_registry.workspace_roots,
            cache_metadata_by_server,
            server_errors,
        )
        if tools_list:
            all_tools_by_server[server_name] = tools_list

    return _McpToolsCollection(
        all_tools_by_server=all_tools_by_server,
        cache_metadata_by_server=cache_metadata_by_server,
    )


def _build_mcp_refresh_fn(
    *,
    validated_servers: dict[str, McpServerConfig],
    mcp_servers: dict[str, dict[str, Any]],
    cache_path: Path,
    runtime_registry: McpRuntimeRegistry,
) -> Callable[[str, _mcp_mod.McpClient], None]:
    """构建 MCP server 工具刷新回调。"""
    refresh_lock = threading.Lock()

    def _refresh(
        server_name: str,
        client: _mcp_mod.McpClient,
    ) -> None:
        validated = validated_servers[server_name]
        tools_list = client.list_tools(timeout=validated.timeout)
        cache_metadata = _cache_metadata(client)
        config_hash = compute_config_hash(mcp_servers[server_name])
        with refresh_lock:
            current_cache = _load_cache(cache_path)
            current_cache.setdefault("servers", {})[server_name] = {
                "config_hash": config_hash,
                "tools": tools_list,
                **cache_metadata,
            }
            _save_cache(cache_path, current_cache)
            _warn_unknown_overrides(server_name, mcp_servers[server_name], tools_list)
            runtime_registry.update_discovery(
                server_name, len(tools_list), cache_metadata
            )

    return _refresh


def _build_runtime_mcp_tools(
    *,
    all_tools_by_server: dict[str, list[dict[str, Any]]],
    validated_servers: dict[str, McpServerConfig],
    raw_servers: dict[str, dict[str, Any]],
    runtime_registry: McpRuntimeRegistry,
    refresh_server_tools: Callable[[str, _mcp_mod.McpClient], None],
) -> tuple[ToolSpec, ...]:
    """根据最新服务器 schema 构建无冲突的运行时 MCP 工具。"""
    disabled = _detect_collisions(all_tools_by_server)
    registered: list[ToolSpec] = []

    for server_name, tools_list in all_tools_by_server.items():
        validated = validated_servers[server_name]
        _warn_unknown_overrides(
            server_name,
            raw_servers[server_name],
            tools_list,
        )

        def on_tools_changed(
            client: _mcp_mod.McpClient,
            current_server: str = server_name,
        ) -> None:
            """刷新缓存和诊断，供后续应用启动发现工具。"""
            refresh_server_tools(current_server, client)

        lazy_ref = _mcp_mod.LazyClientRef(
            server_name,
            raw_servers[server_name],
            tools_changed_callback=on_tools_changed,
            workspace_roots=runtime_registry.workspace_roots,
        )
        runtime_registry.track_client_ref(server_name, lazy_ref)

        server_slug = _sanitize(server_name)
        for tool in tools_list:
            tool_key = f"{server_name}:{tool['name']}"
            if tool_key in disabled:
                continue
            tool_override = _tool_override(raw_servers[server_name], tool["name"])
            if tool_override.enabled is False:
                continue

            tool_slug = _sanitize(tool["name"])
            metadata = McpToolMetadata(
                server_name=server_name,
                server_slug=server_slug,
                tool_name=tool["name"],
                tool_slug=tool_slug,
                host_tool_id=f"mcp__{server_slug}__{tool_slug}",
            )
            registered.append(
                _build_registered_mcp_tool(
                    validated,
                    tool,
                    lazy_ref,
                    metadata=metadata,
                    tool_override=tool_override,
                    runtime_registry=runtime_registry,
                )
            )

    return tuple(registered)


def _tools_for_server(
    cache_path: Path,
    cache_data: dict[str, Any],
    server_name: str,
    raw_config: dict[str, Any],
    validated: McpServerConfig,
    workspace_roots: tuple[Path, ...],
    cache_metadata_by_server: dict[str, dict[str, Any]],
    server_errors: dict[str, str],
) -> list[dict[str, Any]]:
    config_hash = compute_config_hash(raw_config)
    cached_entry = cache_data.get("servers", {}).get(server_name, {})
    cached_tools = _validated_cached_tools(cached_entry, config_hash)
    if cached_tools is not None:
        cache_metadata_by_server[server_name] = {
            "protocol_version": cached_entry.get("protocol_version"),
            "server_info": cached_entry.get("server_info"),
        }
        return cached_tools

    return _query_server_tools(
        cache_path,
        cache_data,
        server_name,
        validated,
        config_hash,
        cached_entry,
        workspace_roots,
        cache_metadata_by_server,
        server_errors,
    )


def _query_server_tools(
    cache_path: Path,
    cache_data: dict[str, Any],
    server_name: str,
    validated: McpServerConfig,
    config_hash: str,
    cached_entry: dict[str, Any],
    workspace_roots: tuple[Path, ...],
    cache_metadata_by_server: dict[str, dict[str, Any]],
    server_errors: dict[str, str],
) -> list[dict[str, Any]]:
    client: _mcp_mod.McpClient | None = None
    try:
        command = [validated.command[0]] + list(validated.args)
        client = _mcp_mod.McpClient(
            command,
            validated.env,
            timeout=validated.timeout,
            workspace_roots=workspace_roots,
        )
        client.start()
        tools_list = client.list_tools()
        cache_metadata = _cache_metadata(client)
        cache_metadata_by_server[server_name] = cache_metadata

        cache_data.setdefault("servers", {})[server_name] = {
            "config_hash": config_hash,
            "tools": tools_list,
            **cache_metadata,
        }
        _save_cache(cache_path, cache_data)
        return tools_list
    except (
        LookupError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        ValueError,
    ) as e:
        redacted = _redact_and_truncate(str(e), max_len=200)
        server_errors[server_name] = redacted
        _warn(f"error querying tools from MCP server {server_name!r}: {redacted}")
        cached_tools = _validated_cached_tools(cached_entry, config_hash)
        if cached_tools is not None:
            cache_metadata_by_server[server_name] = {
                "protocol_version": cached_entry.get("protocol_version"),
                "server_info": cached_entry.get("server_info"),
            }
        return cached_tools or []
    finally:
        if client is not None:
            client.stop()


def _build_registered_mcp_tool(
    validated: McpServerConfig,
    tool: dict[str, Any],
    lazy_ref: _mcp_mod.LazyClientRef,
    metadata: McpToolMetadata,
    tool_override: McpToolOverride,
    runtime_registry: McpRuntimeRegistry,
) -> ToolSpec:
    desc = tool.get("description", "")
    tool_description = (
        f"{desc} [mcp: {validated.name}]" if desc else f"[mcp: {validated.name}]"
    )
    description = tool_override.description or tool_description
    mcp_handler = _make_handler(
        lazy_ref,
        tool["name"],
        tool.get("outputSchema"),
        runtime_registry,
    )
    return ToolSpec(
        name=metadata.host_tool_id,
        description=description,
        input_hint=_mcp_tool_input_hint(tool.get("inputSchema", {})),
        handler=mcp_handler,
        schema=tool.get("inputSchema", {}),
    )


def _mcp_tool_input_hint(input_schema: dict[str, Any]) -> str:
    props = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    hints = [
        f"{p_name}: {p_info.get('type', 'any')}"
        f"{' (required)' if p_name in required else ''}"
        for p_name, p_info in props.items()
    ]
    return ", ".join(hints) if hints else "no arguments"
