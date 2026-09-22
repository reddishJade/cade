from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .sandbox import (
    CommandSandbox,
    NetworkAccess,
    SandboxedCommand,
    SandboxMode,
    SandboxPolicy,
)


class SandboxUnavailableError(RuntimeError):
    """当前主机无法提供请求的 OS sandbox。"""


def _require_linux_host() -> None:
    """拒绝在非 Linux 主机上构造 bubblewrap sandbox。"""
    if sys.platform != "linux":
        raise SandboxUnavailableError("Linux bubblewrap sandbox requires Linux")


class LinuxBubblewrapSandbox(CommandSandbox):
    """使用 bubblewrap 为 Agent 子进程构造 Linux OS sandbox。"""

    _policy: SandboxPolicy
    _bwrap_path: Path

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        bwrap_path: Path | None = None,
    ) -> None:
        _require_linux_host()
        self._policy = _normalize_policy(policy)
        self._bwrap_path = _resolve_bwrap_path(bwrap_path)

    @property
    def policy(self) -> SandboxPolicy:
        return self._policy

    def wrap(self, argv: list[str], cwd: Path) -> SandboxedCommand:
        if not argv:
            raise ValueError("sandbox command argv must not be empty")

        command_cwd = cwd.resolve(strict=True)
        if not _is_relative_to(command_cwd, self._policy.project_root):
            raise ValueError("sandbox command cwd must stay inside the project root")

        placeholders = _prepare_protected_placeholders(self._policy)
        unreadable_file = _prepare_unreadable_file_placeholder(self._policy)
        finalize = _combined_finalizer(placeholders, unreadable_file)
        try:
            args = [str(self._bwrap_path), "--new-session", "--die-with-parent"]
            args.extend(
                self._filesystem_args(
                    unreadable_file.path if unreadable_file is not None else None
                )
            )
            args.extend(
                (
                    "--unshare-user",
                    "--disable-userns",
                    "--unshare-pid",
                    "--unshare-ipc",
                    "--unshare-uts",
                )
            )
            if self._policy.network_access is NetworkAccess.DENY:
                args.append("--unshare-net")
            args.extend(("--proc", "/proc"))
            args.extend(("--chdir", str(command_cwd)))
            args.extend(("--cap-drop", "ALL", "--"))
            args.extend(argv)
        except (OSError, ValueError):
            finalize()
            raise
        return SandboxedCommand(
            argv=tuple(args),
            cwd=command_cwd,
            finalize=finalize if placeholders or unreadable_file is not None else None,
        )

    def _filesystem_args(self, empty_file: Path | None) -> list[str]:
        policy = self._policy
        if policy.mode is SandboxMode.DANGER_FULL_ACCESS:
            return ["--bind", "/", "/", "--dev", "/dev"]

        args = ["--ro-bind", "/", "/", "--dev", "/dev"]
        if policy.mode is SandboxMode.WORKSPACE_WRITE:
            writable_roots = _deduplicate_roots(
                (policy.project_root, *policy.writable_roots)
            )
            for root in writable_roots:
                args.extend(("--bind", str(root), str(root)))
            for protected in _existing_protected_paths(policy):
                args.extend(("--ro-bind", str(protected), str(protected)))
        for unreadable in policy.unreadable_roots:
            if unreadable.is_dir():
                args.extend(("--tmpfs", str(unreadable)))
            else:
                if empty_file is None:
                    raise RuntimeError("missing unreadable file placeholder")
                args.extend(("--ro-bind", str(empty_file), str(unreadable)))
        return args


def _resolve_bwrap_path(explicit: Path | None) -> Path:
    raw_path = str(explicit) if explicit is not None else shutil.which("bwrap")
    if raw_path is None:
        raise SandboxUnavailableError(
            "bubblewrap executable not found; install 'bwrap' or select "
            "danger-full-access"
        )
    path = Path(raw_path).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SandboxUnavailableError(f"bubblewrap is not executable: {path}")
    return path


def _normalize_policy(policy: SandboxPolicy) -> SandboxPolicy:
    project_root = policy.project_root.resolve(strict=True)
    if not project_root.is_dir():
        raise ValueError("sandbox project root must be a directory")

    writable_roots: list[Path] = []
    for raw_root in policy.writable_roots:
        root = raw_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"sandbox writable root must be a directory: {root}")
        writable_roots.append(root)

    unreadable_roots: list[Path] = []
    for raw_root in policy.unreadable_roots:
        root = raw_root.resolve(strict=True)
        unreadable_roots.append(root)

    for relative in policy.protected_workspace_paths:
        protected = Path(relative)
        if protected.is_absolute() or ".." in protected.parts:
            raise ValueError(
                f"protected workspace path must be project-relative: {relative}"
            )

    return SandboxPolicy(
        project_root=project_root,
        mode=policy.mode,
        network_access=policy.network_access,
        writable_roots=tuple(writable_roots),
        unreadable_roots=tuple(_minimal_roots(unreadable_roots)),
        protected_workspace_paths=policy.protected_workspace_paths,
    )


def _existing_protected_paths(policy: SandboxPolicy) -> tuple[Path, ...]:
    paths: list[Path] = []
    for relative in policy.protected_workspace_paths:
        path = policy.project_root / relative
        if path.is_symlink():
            raise ValueError(f"protected workspace path must not be a symlink: {path}")
        if path.exists():
            paths.append(path)
    return tuple(paths)


@dataclass(frozen=True)
class _ProtectedPlaceholder:
    path: Path
    device: int
    inode: int
    descriptor: int


@dataclass(frozen=True)
class _UnreadableFilePlaceholder:
    path: Path
    device: int
    inode: int
    descriptor: int


def _prepare_protected_placeholders(
    policy: SandboxPolicy,
) -> tuple[_ProtectedPlaceholder, ...]:
    if policy.mode is not SandboxMode.WORKSPACE_WRITE:
        return ()
    placeholders: list[_ProtectedPlaceholder] = []
    try:
        for relative in policy.protected_workspace_paths:
            path = policy.project_root / relative
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                if path.is_symlink():
                    raise ValueError(
                        f"protected workspace path must not be a symlink: {path}"
                    ) from None
                continue
            try:
                descriptor = os.open(path, _linux_directory_open_flags())
            except OSError:
                path.rmdir()
                raise
            metadata = os.fstat(descriptor)
            placeholders.append(
                _ProtectedPlaceholder(
                    path=path,
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                    descriptor=descriptor,
                )
            )
    except (OSError, ValueError):
        _protected_placeholder_finalizer(tuple(placeholders))()
        raise
    return tuple(placeholders)


def _linux_directory_open_flags() -> int:
    """读取 Linux 专属目录标志，缺失时拒绝降低保护强度。"""
    flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        value = getattr(os, name, None)
        if not isinstance(value, int):
            raise SandboxUnavailableError(f"Linux open flag is unavailable: {name}")
        flags |= value
    return flags


def _prepare_unreadable_file_placeholder(
    policy: SandboxPolicy,
) -> _UnreadableFilePlaceholder | None:
    """为敏感普通文件创建空的普通文件视图，保持 Git 等工具兼容。"""
    if not any(not path.is_dir() for path in policy.unreadable_roots):
        return None
    descriptor, raw_path = tempfile.mkstemp(prefix="cade-sandbox-empty-")
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        return _UnreadableFilePlaceholder(
            path=path,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            descriptor=descriptor,
        )
    except OSError:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _combined_finalizer(
    placeholders: tuple[_ProtectedPlaceholder, ...],
    unreadable_file: _UnreadableFilePlaceholder | None,
) -> Callable[[], str | None]:
    finalize_protected = _protected_placeholder_finalizer(placeholders)
    completed = False

    def finalize() -> str | None:
        nonlocal completed
        if completed:
            return None
        completed = True
        violations: list[str] = []
        protected_error = finalize_protected()
        if protected_error is not None:
            violations.append(protected_error)
        if unreadable_file is not None:
            try:
                try:
                    metadata = unreadable_file.path.lstat()
                except FileNotFoundError:
                    metadata = None
                if metadata is not None and (
                    metadata.st_dev != unreadable_file.device
                    or metadata.st_ino != unreadable_file.inode
                ):
                    violations.append("sandbox empty-file placeholder was replaced")
                elif metadata is not None:
                    unreadable_file.path.unlink()
            except OSError as exc:
                violations.append(
                    f"sandbox failed to remove empty-file placeholder: {exc}"
                )
            finally:
                os.close(unreadable_file.descriptor)
        return "; ".join(violations) if violations else None

    return finalize


def _protected_placeholder_finalizer(
    placeholders: tuple[_ProtectedPlaceholder, ...],
) -> Callable[[], str | None]:
    completed = False

    def finalize() -> str | None:
        nonlocal completed
        if completed:
            return None
        completed = True
        violations: list[str] = []
        for placeholder in reversed(placeholders):
            path = placeholder.path
            try:
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if (
                    metadata.st_dev != placeholder.device
                    or metadata.st_ino != placeholder.inode
                ):
                    violations.append(f"{path} (placeholder identity changed)")
                    continue
                try:
                    path.rmdir()
                except OSError as exc:
                    violations.append(f"{path} (cleanup failed: {exc})")
            finally:
                os.close(placeholder.descriptor)
        if not violations:
            return None
        return "sandbox failed to clean protected path placeholder: " + ", ".join(
            violations
        )

    return finalize


def _deduplicate_roots(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    unique: list[Path] = []
    for root in sorted(set(roots), key=lambda path: (len(path.parts), str(path))):
        if not any(root == existing for existing in unique):
            unique.append(root)
    return tuple(unique)


def _minimal_roots(roots: list[Path]) -> tuple[Path, ...]:
    minimal: list[Path] = []
    for root in sorted(set(roots), key=lambda path: (len(path.parts), str(path))):
        if any(_is_relative_to(root, parent) for parent in minimal):
            continue
        minimal.append(root)
    return tuple(minimal)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
