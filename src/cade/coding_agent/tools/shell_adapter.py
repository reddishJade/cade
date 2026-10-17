"""Shell 检测与适配。

提供跨平台的 shell 发现、规格化参数构建和拒绝列表。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ._constants import DEFAULT_TIMEOUT_SECONDS

SHELL_NAMES = frozenset(
    {
        "auto",
        "pwsh",
        "powershell",
        "cmd",
        "bash",
        "zsh",
        "sh",
        "fish",
        "dash",
        "ksh",
    }
)


@dataclass(frozen=True)
class ShellSpec:
    name: str
    command_prefix: tuple[str, ...]
    syntax: str  # "powershell" | "cmd" | "posix"
    login: bool = False
    """是否用 login shell（加载 profile/rc）。"""
    deny: bool = False
    """明确拒绝此 shell（fish 等不兼容 shell）。"""
    ps_kind: str | None = None
    """PowerShell 版本: "pwsh" | "powershell" | None。"""


_KNOWN_SHELLS: dict[str, ShellSpec] = {
    "pwsh": ShellSpec(
        name="pwsh",
        command_prefix=("pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
        syntax="powershell",
        ps_kind="pwsh",
    ),
    "powershell": ShellSpec(
        name="powershell",
        command_prefix=(
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
        ),
        syntax="powershell",
        ps_kind="powershell",
    ),
    "cmd": ShellSpec(
        name="cmd",
        command_prefix=("cmd", "/d", "/c"),
        syntax="cmd",
    ),
    "bash": ShellSpec(
        name="bash",
        command_prefix=("bash", "--noprofile", "--norc", "-c"),
        syntax="posix",
        login=True,
    ),
    "zsh": ShellSpec(
        name="zsh",
        command_prefix=("zsh", "-f", "-c"),
        syntax="posix",
        login=True,
    ),
    "sh": ShellSpec(
        name="sh",
        command_prefix=("sh", "-c"),
        syntax="posix",
    ),
    "dash": ShellSpec(
        name="dash",
        command_prefix=("dash", "-c"),
        syntax="posix",
    ),
    "ksh": ShellSpec(
        name="ksh",
        command_prefix=("ksh", "-c"),
        syntax="posix",
        login=True,
    ),
    "fish": ShellSpec(
        name="fish",
        command_prefix=("fish", "-c"),
        syntax="posix",
        deny=True,
    ),
}


def detect_shell(config: str = "auto") -> ShellSpec:
    """检测并返回当前环境可用的 ShellSpec。

    参数:
        config: "auto" 自动检测，或显式指定 shell 名称。

    返回:
        对应的 ShellSpec。

    异常:
        ValueError: 未知的 shell 名称。
        RuntimeError: 配置的 shell 被拒绝或不在 PATH 上。
    """
    if config != "auto":
        return _resolve_explicit_shell(config)
    if sys.platform == "win32":
        return _detect_windows_shell()
    spec = _detect_posix_shell()
    if spec.deny:
        return _KNOWN_SHELLS["sh"]
    return spec


def build_shell_argv(spec: ShellSpec, command: str) -> list[str]:
    return [*spec.command_prefix, command]


def build_shell_environment(spec: ShellSpec) -> dict[str, str] | None:
    if spec.name != "zsh":
        return None
    env = os.environ.copy()
    env["TMPPREFIX"] = str(Path(tempfile.gettempdir()) / "zsh")
    return env


def _resolve_explicit_shell(config: str) -> ShellSpec:
    if config not in SHELL_NAMES:
        raise ValueError(
            f"unknown shell: {config!r}. "
            f"Valid options: {', '.join(sorted(SHELL_NAMES))}"
        )
    spec = _KNOWN_SHELLS[config]
    if spec.deny:
        raise RuntimeError(
            f"configured shell {config!r} is explicitly unsupported. "
            "Choose bash, zsh, sh, pwsh, powershell, or cmd."
        )
    if not _is_on_path(spec):
        raise RuntimeError(
            f"configured shell {config!r} not found on PATH. "
            "Install it or switch the 'shell' config back to 'auto'."
        )
    return spec


def _is_on_path(spec: ShellSpec) -> bool:
    return shutil.which(spec.command_prefix[0]) is not None


def _detect_windows_shell() -> ShellSpec:
    for name in ("pwsh", "powershell", "bash", "cmd"):
        spec = _KNOWN_SHELLS[name]
        if spec.deny:
            continue
        if shutil.which(spec.command_prefix[0]):
            return spec
    # Git Bash 发现：通过 git 路径推导
    git_path = shutil.which("git")
    if git_path:
        git_bash = Path(git_path).resolve().parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            return ShellSpec(
                name="bash",
                command_prefix=(str(git_bash), "--noprofile", "--norc", "-c"),
                syntax="posix",
                login=True,
            )
    return _KNOWN_SHELLS["cmd"]


def _detect_posix_shell() -> ShellSpec:
    shell_env = os.environ.get("SHELL")
    if shell_env:
        name = os.path.basename(shell_env)
        if name in _KNOWN_SHELLS:
            spec = _KNOWN_SHELLS[name]
            if not spec.deny and shutil.which(spec.command_prefix[0]):
                return spec
    for name in ("bash", "zsh", "sh"):
        spec = _KNOWN_SHELLS[name]
        if spec.deny:
            continue
        if shutil.which(spec.command_prefix[0]):
            return spec
    return _KNOWN_SHELLS["sh"]


# ── 提示词 ──


def build_shell_prompt_guidelines(
    spec: ShellSpec,
    syntax: str,
    root: Path,
) -> tuple[str, ...]:
    """按 shell 类型生成详细使用指引。"""
    guidelines: list[str] = [
        "Bash output may be truncated; use the reported full output path when present.",
    ]

    if syntax == "powershell":
        ps_ver = spec.ps_kind or "powershell"
        if ps_ver == "powershell":
            # PowerShell 5.1 不支持 &&
            guidelines.extend(
                [
                    "PowerShell 5.1: use `;` to chain commands (`&&` is unsupported).",
                    "Example: `cd src; dir` or `Set-Location src; Get-ChildItem`",
                ]
            )
        else:
            # PowerShell 7 及以上版本。
            guidelines.extend(
                [
                    "PowerShell 7+ detected: use `&&` or `;` to chain commands.",
                    "Example: `cd src && dir` or `cd src; dir`",
                ]
            )
        guidelines.extend(
            [
                "Use `|` for pipelines (Out-File, Select-Object, etc.).",
                "Quote paths with spaces using single quotes (').",
                "Use `cd` to change directory before running commands.",
                (
                    "Common file commands: Get-Content (read), Set-Content (write), "
                    "Remove-Item (del), Copy-Item, Move-Item, New-Item, Out-File"
                ),
            ]
        )
    elif syntax == "cmd":
        guidelines.extend(
            [
                "Use `&&` or `&` to chain commands in cmd.exe.",
                "Example: `cd src && dir`",
                "Use `cd /d` to change drives (e.g. `cd /d D:/projects`).",
                (
                    "Common file commands: type (read), copy (read+write), "
                    "del/erase (delete), move/ren (write), mkdir (write), dir (list)"
                ),
            ]
        )
    else:
        guidelines.extend(
            [
                "Chain commands with `&&` (stop on error) or `;` (always continue).",
                "Example: `cd src && cat file.txt`",
                "Use `cd` to change directory before running commands.",
                "Use single quotes (') for paths with spaces in bash.",
                (
                    "Common file commands: cat (read), cp (read+write), mv (write), "
                    "rm (delete), rg (content search), fd (file search), "
                    "curl/wget (download), tar (archive)"
                ),
            ]
        )

    # workdir 指引
    guidelines.append(
        "Use the `workdir` parameter to run commands inside the project."
        f" Project root: {root.as_posix()}"
    )
    guidelines.append(
        "Set `purpose` to `validation` for test, lint, type, build, or other "
        "checks whose outcomes you will cite as verification. Use commands "
        "that fail if a check fails."
    )

    # 运行时变量
    import sys as _sys
    import tempfile as _tempfile

    guidelines.append(
        f"If not specified, commands time out after {DEFAULT_TIMEOUT_SECONDS * 1000}ms."
    )
    guidelines.append(
        f"OS: {_sys.platform}, Shell: {spec.name}, Temp dir: {_tempfile.gettempdir()}"
    )

    return tuple(guidelines)
