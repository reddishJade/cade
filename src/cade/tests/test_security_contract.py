"""保护权限求值器后定义优先 (findLast) 语义、Shell 静态语义分类与路径边界隔离契约。

控制性回归使用真实 RuleMatcher、ShellAnalyzer 与 PathUtils。
断言系统规则覆盖顺序、高危命令拦截、动态语法识别以及越界路径防护。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.coding_agent.tools.path_utils import is_path_blocked, resolve_project_path
from cade.harness.security.permission_model import Action, Rule, Target
from cade.harness.security.rule_matcher import (
    evaluate,
    first_match,
    matches,
    merge_rulesets,
)
from cade.harness.security.shell_analyzer import analyze_shell_command


def test_rule_matcher_find_last_and_user_rule_override() -> None:
    """验证规则匹配严格遵循后定义优先 (findLast)，确保用户规则能精确覆盖默认全局规则。"""
    # 默认规则：拒绝所有 git 命令
    default_rules = [
        Rule(action="bash", effect="deny", command="git"),
    ]
    # 用户定制规则：放行只读的 git status 子命令
    user_rules = [
        Rule(action="bash", effect="allow", command="git", subcommand="status"),
    ]

    merged = merge_rulesets(user_rules, default_rules)
    action = Action(
        tool="bash",
        capability="shell",
        operation="execute",
        targets=(Target(kind="command", value="git", access="execute"),),
        input={"command": "git status"},
    )

    # 1. git status 应被用户规则覆盖放行 (allow)
    matched_status = first_match(action, merged, shell_command="git status")
    assert matched_status is not None
    assert matched_status.effect == "allow"
    assert evaluate(action, merged, shell_command="git status") == "allow"

    # 2. git push 不匹配用户规则，命中前置的默认规则被拒绝 (deny)
    matched_push = first_match(action, merged, shell_command="git push")
    assert matched_push is not None
    assert matched_push.effect == "deny"
    assert evaluate(action, merged, shell_command="git push") == "deny"

    # 3. 未命中任何规则时，验证 fallback 行为
    unmatched_action = Action(
        tool="bash",
        capability="shell",
        operation="execute",
        targets=(Target(kind="command", value="python", access="execute"),),
        input={"command": "python main.py"},
    )
    assert (
        evaluate(
            unmatched_action,
            merged,
            fallback="deny",
            shell_command="python main.py",
        )
        == "deny"
    )
    assert (
        evaluate(
            unmatched_action,
            merged,
            fallback="ask",
            shell_command="python main.py",
        )
        == "ask"
    )


def test_rule_matcher_flags_matching() -> None:
    """验证 Shell 命令 flags 排序归一化与 flags_any / flags_all 匹配契约。"""
    # 规则：匹配含 force 标志的 rm 命令
    rule_any = Rule(
        action="bash",
        effect="deny",
        command="rm",
        flags_any={"--force", "-fr"},
    )
    action = Action(
        tool="bash",
        capability="shell",
        operation="execute",
        targets=(Target(kind="command", value="rm", access="execute"),),
        input={"command": "rm -rf /tmp/test"},
    )

    # -rf 会被归一化为 -fr 并成功匹配 flags_any
    assert matches(action, rule_any, shell_command="rm -rf /tmp/test")
    assert matches(action, rule_any, shell_command="rm --force file.txt")
    assert not matches(action, rule_any, shell_command="rm -r file.txt")


@pytest.mark.parametrize(
    ("command", "expected_reason"),
    [
        ("rm -rf /", "dangerous_command"),
        ("sudo apt install vim", "dangerous_command"),
        ("git reset --hard HEAD~1", "dangerous_command"),
        ("echo foo > /tmp/target.txt", "wrapper_command"),
        ("cat $(which python)", "command_substitution"),
        ("grep foo *.txt", "glob"),
    ],
)
def test_posix_shell_analyzer_hazards_and_dynamics(
    command: str, expected_reason: str
) -> None:
    """验证 Shell 分析器能够静态识别高危破坏性命令、重定向、命令替换及通配扩展。"""
    analysis = analyze_shell_command(command, shell_type="posix")
    reasons = {effect.reason for effect in analysis.unresolved_effects}
    assert expected_reason in reasons, (
        f"Command {command!r} should have triggered {expected_reason!r}, got {reasons!r}"
    )


def test_posix_shell_analyzer_safe_read_commands() -> None:
    """验证已知安全的只读命令不产生高危未决影响，并准确提取读操作目标。"""
    analysis = analyze_shell_command("cat src/main.py", shell_type="posix")
    assert not any(
        effect.reason == "dangerous_command" for effect in analysis.unresolved_effects
    )
    assert analysis.primary_command == "cat"
    assert any(t.value == "src/main.py" for t in analysis.resolved_paths)


def test_path_traversal_and_project_containment(tmp_path: Path) -> None:
    """验证路径解析工具严密防护父目录逃逸 (../) 与绝对路径越界，并阻断敏感目录访问。"""
    # 1. 相对路径内的父目录回溯必须直接拒绝
    with pytest.raises(ValueError, match="parent-directory paths are not allowed"):
        resolve_project_path(tmp_path, "../../etc/passwd")

    with pytest.raises(ValueError, match="parent-directory paths are not allowed"):
        resolve_project_path(tmp_path, "foo/../../bar")

    # 2. 逃出 project_root 的绝对路径必须直接拒绝
    outside_dir = tmp_path.parent / "outside_project"
    outside_dir.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="path escapes project root"):
        resolve_project_path(tmp_path, str(outside_dir / "secret.txt"))

    # 3. 项目内部路径正常解析为绝对路径
    valid_path = resolve_project_path(tmp_path, "src/index.py")
    assert valid_path == (tmp_path / "src" / "index.py").resolve()

    # 4. 敏感路径阻断检测 (.git, .env, .venv)
    assert is_path_blocked(tmp_path, tmp_path / ".git" / "config")
    assert is_path_blocked(tmp_path, tmp_path / ".env")
    assert is_path_blocked(tmp_path, tmp_path / ".venv" / "bin" / "python")
    assert not is_path_blocked(tmp_path, tmp_path / "src" / "main.py")
