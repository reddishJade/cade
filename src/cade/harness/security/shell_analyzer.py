"""保守的 Shell 命令分类。

这里不模拟命令的完整文件副作用。分类器只识别少量确定的只读命令；
动态语法、未知命令和写操作由执行模式决定，明确危险的宿主操作直接拒绝。
真正的文件和网络边界必须由 OS sandbox 提供。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .permission_model import Action, Constraint, Target, UnresolvedEffect

ShellType = Literal["posix", "powershell", "cmd"]
ShellUnresolvedPolicy = Literal["allow", "ask", "deny"]

_POSIX_READ_COMMANDS = frozenset(
    {
        "ack",
        "cat",
        "dir",
        "grep",
        "head",
        "less",
        "ls",
        "more",
        "realpath",
        "rg",
        "tail",
    }
)
_POSIX_NO_EFFECT_COMMANDS = frozenset({"false", "pwd", "true", "uname", "whoami"})
_POSIX_MUTATING_COMMANDS = frozenset({"cp", "mkdir", "mv", "rm", "touch"})
_POWERSHELL_READ_COMMANDS = frozenset({"get-childitem", "get-content", "select-string"})
_POWERSHELL_NO_EFFECT_COMMANDS = frozenset({"get-date", "get-location"})
_CMD_READ_COMMANDS = frozenset({"dir", "more", "type"})
_CMD_NO_EFFECT_COMMANDS = frozenset({"cls", "echo", "ver"})

_SEPARATORS = frozenset({";", "&&", "||", "|"})
_UNSAFE_CONTROL = frozenset({"&", "(", ")"})
_REDIRECTIONS = frozenset({"<", ">", "<<", ">>", "<<<"})
_FIND_EXECUTORS = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir"})
_FIND_FILE_OUTPUT_ACTIONS = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
_FD_READ_FLAGS = frozenset(
    {
        "-H",
        "--hidden",
        "-I",
        "--no-ignore",
        "--no-require-git",
        "-g",
        "--glob",
        "-s",
        "--case-sensitive",
        "-i",
        "--ignore-case",
        "-a",
        "--absolute-path",
        "-p",
        "--full-path",
        "-0",
        "--print0",
    }
)
_FD_READ_OPTIONS_WITH_VALUES = frozenset(
    {
        "-d",
        "--max-depth",
        "--min-depth",
        "-e",
        "--extension",
        "-E",
        "--exclude",
        "--max-results",
        "-t",
        "--type",
        "--color",
    }
)
_RG_EXTERNAL_PROGRAM_OPTIONS = frozenset({"--pre", "--hostname-bin"})
_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "grep",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "blame",
        "cat-file",
        "describe",
    }
)
_GIT_GLOBAL_OPTIONS_WITH_VALUES = frozenset({"-C", "--git-dir", "--work-tree"})
_GIT_UNSAFE_READ_OPTIONS = frozenset(
    {
        "-c",
        "--ext-diff",
        "--textconv",
        "--output",
        "--open-files-in-pager",
    }
)
_GLOB_CHARS = frozenset("*?[")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)


@dataclass(frozen=True)
class ShellAnalysis:
    """权限层消费的最小 Shell 分类结果。"""

    resolved_paths: tuple[Target, ...]
    unresolved_effects: tuple[UnresolvedEffect, ...]
    primary_command: str | None
    shell_type: ShellType
    parse_error: bool
    classification_available: bool


class ShellAnalysisPolicyEvaluator:
    """把保守分类结果转换为权限约束。"""

    def evaluate(
        self,
        action: Action,
        unresolved_policy: ShellUnresolvedPolicy = "ask",
        mutation_policy: ShellUnresolvedPolicy = "ask",
    ) -> tuple[Constraint, ...]:
        constraints: list[Constraint] = []
        for effect in action.unresolved_effects:
            if effect.reason == "dangerous_command":
                decision: ShellUnresolvedPolicy = "deny"
            elif effect.reason == "mutation":
                decision = mutation_policy
            else:
                decision = unresolved_policy
            if decision == "allow":
                continue
            constraints.append(
                Constraint(
                    decision=decision,
                    source="shell_policy",
                    reason=f"{effect.reason}: {effect.fragment}",
                )
            )
        return tuple(constraints)


class PosixAnalyzer:
    def analyze(self, command: str) -> ShellAnalysis:
        return _analyze_posix(command)


class PowerShellAnalyzer:
    def analyze(self, command: str) -> ShellAnalysis:
        return _analyze_simple_shell(command, "powershell")


class CmdAnalyzer:
    def analyze(self, command: str) -> ShellAnalysis:
        return _analyze_simple_shell(command, "cmd")


def analyze_shell_command(
    command: str,
    shell_type: str = "posix",
) -> ShellAnalysis:
    """按 Shell 类型执行保守分类。"""
    if shell_type == "powershell":
        return PowerShellAnalyzer().analyze(command)
    if shell_type == "cmd":
        return CmdAnalyzer().analyze(command)
    return PosixAnalyzer().analyze(command)


def _analyze_posix(command: str) -> ShellAnalysis:
    try:
        tokens = _posix_tokens(command)
    except ValueError:
        return _unresolved(command, "posix", "parse_error", "invalid quoting", True)
    if not tokens:
        return _empty("posix")

    primary = _primary_command(tokens)
    dangerous = _dangerous_posix(tokens)
    if dangerous is not None:
        return _result(
            "posix",
            primary,
            unresolved=(
                UnresolvedEffect(
                    reason="dangerous_command",
                    fragment=dangerous,
                ),
            ),
        )

    dynamic = _dynamic_effect(command, tokens)
    if dynamic is not None:
        return _result("posix", primary, unresolved=(dynamic,))

    segments, unsupported = _segments(tokens)
    if unsupported is not None:
        return _result(
            "posix",
            primary,
            unresolved=(
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment=unsupported,
                ),
            ),
        )

    paths: list[Target] = []
    unresolved: list[UnresolvedEffect] = []
    for segment in segments:
        name, args = _command_and_args(segment)
        if name is None:
            continue
        if name == "fd":
            fd_paths, fd_effect = _fd_analysis(args)
            paths.extend(fd_paths)
            if fd_effect is not None:
                unresolved.append(fd_effect)
            continue
        if name == "find":
            find_paths, find_effects = _find_analysis(args)
            paths.extend(find_paths)
            unresolved.extend(find_effects)
            continue
        if name == "git":
            git_paths, git_effect = _git_read_analysis(args)
            paths.extend(git_paths)
            if git_effect is not None:
                unresolved.append(git_effect)
            continue
        if name in _POSIX_NO_EFFECT_COMMANDS:
            continue
        if name in _POSIX_READ_COMMANDS:
            if name == "rg":
                external_option = next(
                    (
                        option
                        for arg in args
                        if (option := arg.split("=", 1)[0])
                        in _RG_EXTERNAL_PROGRAM_OPTIONS
                    ),
                    None,
                )
                if external_option is not None:
                    unresolved.append(
                        UnresolvedEffect(
                            reason="wrapper_command",
                            fragment=f"rg option executes a program: {external_option}",
                        )
                    )
                    continue
            paths.extend(_read_paths(name, args))
            continue
        if name in _POSIX_MUTATING_COMMANDS:
            paths.extend(_mutating_paths(name, args))
            unresolved.append(
                UnresolvedEffect(
                    reason="mutation",
                    fragment=f"shell command mutates filesystem state: {name}",
                )
            )
            continue
        unresolved.append(
            UnresolvedEffect(
                reason="wrapper_command",
                fragment=f"command requires approval: {name}",
            )
        )

    return _result(
        "posix",
        primary,
        paths=_deduplicate_paths(paths),
        unresolved=tuple(unresolved),
    )


def _analyze_simple_shell(
    command: str, shell_type: Literal["powershell", "cmd"]
) -> ShellAnalysis:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return _unresolved(
            command,
            shell_type,
            "parse_error",
            "invalid quoting",
            True,
        )
    if not tokens:
        return _empty(shell_type)

    primary = _basename(tokens[0])
    lowered = command.lower()
    if _dangerous_simple(primary, tokens[1:]):
        return _result(
            shell_type,
            primary,
            unresolved=(
                UnresolvedEffect(
                    reason="dangerous_command",
                    fragment=command,
                ),
            ),
        )
    if any(marker in command for marker in ("$", "`", "|", ";", ">", "<", "&")):
        return _result(
            shell_type,
            primary,
            unresolved=(
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment="dynamic or compound shell syntax",
                ),
            ),
        )

    read_commands = (
        _POWERSHELL_READ_COMMANDS if shell_type == "powershell" else _CMD_READ_COMMANDS
    )
    no_effect_commands = (
        _POWERSHELL_NO_EFFECT_COMMANDS
        if shell_type == "powershell"
        else _CMD_NO_EFFECT_COMMANDS
    )
    if primary in no_effect_commands:
        return _result(shell_type, primary)
    if primary in read_commands:
        paths = [
            _path_target(token.strip("\"'"))
            for token in tokens[1:]
            if token and not token.startswith(("-", "/"))
        ]
        return _result(
            shell_type,
            primary,
            paths=_deduplicate_paths(paths),
        )
    return _result(
        shell_type,
        primary,
        unresolved=(
            UnresolvedEffect(
                reason="wrapper_command",
                fragment=f"command requires approval: {lowered}",
            ),
        ),
    )


def _posix_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _segments(tokens: list[str]) -> tuple[list[list[str]], str | None]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            if segments[-1]:
                segments.append([])
            continue
        if token in _UNSAFE_CONTROL:
            return (segments, f"unsupported shell control operator: {token}")
        if token in _REDIRECTIONS or any(char in token for char in "<>"):
            return (segments, "shell redirection requires approval")
        segments[-1].append(token)
    return ([segment for segment in segments if segment], None)


def _primary_command(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in _SEPARATORS | _UNSAFE_CONTROL | _REDIRECTIONS:
            continue
        if _ASSIGNMENT.fullmatch(token):
            continue
        return _basename(token)
    return None


def _command_and_args(segment: list[str]) -> tuple[str | None, list[str]]:
    index = 0
    while index < len(segment) and _ASSIGNMENT.fullmatch(segment[index]):
        index += 1
    if index >= len(segment):
        return (None, [])
    return (_basename(segment[index]), segment[index + 1 :])


def _dynamic_effect(
    command: str,
    tokens: list[str],
) -> UnresolvedEffect | None:
    if "$(" in command or "`" in command:
        return UnresolvedEffect(
            reason="command_substitution",
            fragment="command substitution",
        )
    if "$" in command:
        return UnresolvedEffect(reason="variable_expansion", fragment="shell variable")
    if _has_unquoted_glob(command):
        return UnresolvedEffect(reason="glob", fragment="shell glob")
    return None


def _has_unquoted_glob(command: str) -> bool:
    """Return whether the shell itself may expand a glob in the command text."""
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char == "\\":
                escaped = True
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in _GLOB_CHARS:
            return True
    return False


def _dangerous_posix(tokens: list[str]) -> str | None:
    segments, _ = _segments(tokens)
    for segment in segments:
        name, args = _command_and_args(segment)
        lowered = [arg.lower() for arg in args]
        if name in {"mkfs", "poweroff", "reboot", "shutdown"}:
            return "host-level destructive command"
        if name in {"sudo", "doas", "su"}:
            return "privilege escalation command"
        if name == "git" and "reset" in lowered and "--hard" in lowered:
            return "git reset --hard discards working tree changes"
        if (
            name == "git"
            and "clean" in lowered
            and any("f" in arg.lstrip("-") for arg in lowered if arg.startswith("-"))
        ):
            return "git clean -f deletes untracked files"
        if name == "rm" and _is_root_recursive_delete(lowered):
            return "recursive deletion of the filesystem root"
    return None


def _dangerous_simple(primary: str, args: list[str]) -> bool:
    lowered = {arg.lower() for arg in args}
    if primary in {"format", "shutdown"}:
        return True
    return bool(
        primary == "remove-item" and "-recurse" in lowered and "-force" in lowered
    )


def _is_root_recursive_delete(args: list[str]) -> bool:
    recursive = any(
        arg in {"--recursive", "--force"}
        or (arg.startswith("-") and "r" in arg and "f" in arg)
        for arg in args
    )
    targets = {arg for arg in args if not arg.startswith("-") and arg != "--"}
    return recursive and bool(targets & {"/", "/*"})


def _read_paths(command: str, args: list[str]) -> list[Target]:
    positional: list[str] = []
    skip_next = False
    options_with_values = _read_option_values(command)
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        option = arg.split("=", 1)[0]
        if option in options_with_values:
            if "=" not in arg:
                skip_next = True
            continue
        if arg and not arg.startswith("-"):
            positional.append(arg)
    if command in {"grep", "rg", "ack"} and positional:
        positional = positional[1:]
    return [_path_target(arg) for arg in positional]


def _read_option_values(command: str) -> frozenset[str]:
    if command in {"head", "tail"}:
        return frozenset({"-c", "--bytes", "-n", "--lines"})
    if command == "rg":
        return frozenset(
            {
                "-A",
                "--after-context",
                "-B",
                "--before-context",
                "-C",
                "--context",
                "-e",
                "--regexp",
                "-f",
                "--file",
                "-g",
                "--glob",
                "-t",
                "--type",
                "-T",
                "--type-not",
                "--iglob",
                "--max-count",
                "--max-columns",
                "--max-depth",
            }
        )
    if command in {"grep", "ack"}:
        return frozenset(
            {
                "-A",
                "--after-context",
                "-B",
                "--before-context",
                "-C",
                "--context",
                "-e",
                "--regexp",
                "-f",
                "--file",
                "--include",
                "--exclude",
                "--include-dir",
                "--exclude-dir",
                "-m",
                "--max-count",
            }
        )
    return frozenset()


def _mutating_paths(command: str, args: list[str]) -> list[Target]:
    positional = [arg for arg in args if arg and not arg.startswith("-")]
    if command == "cp" and len(positional) >= 2:
        return [
            _path_target(positional[-2]),
            _path_target(positional[-1], access="write"),
        ]
    access: Literal["write", "delete"] = "delete" if command == "rm" else "write"
    return [_path_target(arg, access=access) for arg in positional]


def _git_read_analysis(
    args: list[str],
) -> tuple[list[Target], UnresolvedEffect | None]:
    paths: list[Target] = []
    index = 0
    subcommand: str | None = None
    while index < len(args):
        arg = args[index]
        option = arg.split("=", 1)[0]
        if option in _GIT_UNSAFE_READ_OPTIONS:
            return (
                paths,
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment=f"git option requires approval: {option}",
                ),
            )
        if option in _GIT_GLOBAL_OPTIONS_WITH_VALUES:
            if "=" in arg:
                value = arg.split("=", 1)[1]
                if option in {"-C", "--git-dir", "--work-tree"} and value:
                    paths.append(_path_target(value))
                index += 1
                continue
            if index + 1 >= len(args):
                return (
                    paths,
                    UnresolvedEffect(
                        reason="wrapper_command",
                        fragment=f"git option requires a value: {arg}",
                    ),
                )
            value = args[index + 1]
            if option in {"-C", "--git-dir", "--work-tree"}:
                paths.append(_path_target(value))
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        subcommand = arg.lower()
        break

    if subcommand in _GIT_READ_SUBCOMMANDS:
        remaining = args[index + 1 :]
        unsafe = next(
            (
                arg.split("=", 1)[0]
                for arg in remaining
                if arg.split("=", 1)[0] in _GIT_UNSAFE_READ_OPTIONS
            ),
            None,
        )
        if unsafe is not None:
            return (
                paths,
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment=f"git option requires approval: {unsafe}",
                ),
            )
        return (paths, None)
    return (
        paths,
        UnresolvedEffect(
            reason="wrapper_command",
            fragment=(
                "git command requires approval"
                if subcommand is None
                else f"git subcommand requires approval: {subcommand}"
            ),
        ),
    )


def _find_paths(args: list[str]) -> list[Target]:
    paths: list[Target] = []
    for arg in args:
        if arg.startswith(("-", "!", "(")):
            break
        paths.append(_path_target(arg))
    return paths


def _fd_analysis(args: list[str]) -> tuple[list[Target], UnresolvedEffect | None]:
    """仅对已知只读参数提取 fd 搜索根，其他形式交给审查。"""
    positional: list[str] = []
    options_enabled = True
    index = 0
    while index < len(args):
        arg = args[index]
        if options_enabled and arg == "--":
            options_enabled = False
            index += 1
            continue
        if options_enabled and arg.startswith("-") and arg != "-":
            option, separator, value = arg.partition("=")
            if option in _FD_READ_FLAGS and not separator:
                index += 1
                continue
            if option in _FD_READ_OPTIONS_WITH_VALUES:
                if separator and value:
                    index += 1
                    continue
                if (
                    not separator
                    and index + 1 < len(args)
                    and not args[index + 1].startswith("-")
                ):
                    index += 2
                    continue
            return (
                [],
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment=f"fd option requires approval: {arg}",
                ),
            )
        positional.append(arg)
        index += 1
    return ([_path_target(path) for path in positional[1:]], None)


def _find_analysis(args: list[str]) -> tuple[list[Target], list[UnresolvedEffect]]:
    """提取 find 搜索根和文件输出目标，并保守标记执行动作。"""
    paths = _find_paths(args)
    effects: list[UnresolvedEffect] = []
    index = 0
    while index < len(args):
        action = args[index].lower()
        if action in _FIND_FILE_OUTPUT_ACTIONS:
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                effects.append(
                    UnresolvedEffect(
                        reason="wrapper_command",
                        fragment=f"find action lacks output path: {action}",
                    )
                )
                index += 1
                continue
            paths.append(_path_target(args[index + 1], access="write"))
            effects.append(
                UnresolvedEffect(
                    reason="mutation",
                    fragment=f"find writes output file: {action}",
                )
            )
            index += 3 if action == "-fprintf" else 2
            continue
        if action == "-delete":
            effects.append(
                UnresolvedEffect(
                    reason="mutation",
                    fragment="find deletes paths",
                )
            )
        elif action in _FIND_EXECUTORS:
            effects.append(
                UnresolvedEffect(
                    reason="wrapper_command",
                    fragment="find executes commands",
                )
            )
        index += 1
    return paths, effects


def _path_target(
    path: str,
    *,
    access: Literal["read", "write", "delete"] = "read",
) -> Target:
    return Target(
        kind="path",
        value=path,
        access=access,
        provenance="shell_literal",
    )


def _deduplicate_paths(paths: list[Target]) -> tuple[Target, ...]:
    seen: set[str] = set()
    result: list[Target] = []
    for target in paths:
        if target.value in seen:
            continue
        seen.add(target.value)
        result.append(target)
    return tuple(result)


def _basename(command: str) -> str:
    return Path(command.strip("\"'")).name.lower()


def _empty(shell_type: ShellType) -> ShellAnalysis:
    return _result(shell_type, None)


def _unresolved(
    command: str,
    shell_type: ShellType,
    reason: Literal["parse_error", "unsupported_shell"],
    fragment: str,
    parse_error: bool,
) -> ShellAnalysis:
    return ShellAnalysis(
        resolved_paths=(),
        unresolved_effects=(
            UnresolvedEffect(reason=reason, fragment=f"{fragment}: {command}"),
        ),
        primary_command=None,
        shell_type=shell_type,
        parse_error=parse_error,
        classification_available=True,
    )


def _result(
    shell_type: ShellType,
    primary: str | None,
    *,
    paths: tuple[Target, ...] = (),
    unresolved: tuple[UnresolvedEffect, ...] = (),
) -> ShellAnalysis:
    return ShellAnalysis(
        resolved_paths=paths,
        unresolved_effects=unresolved,
        primary_command=primary,
        shell_type=shell_type,
        parse_error=False,
        classification_available=True,
    )
