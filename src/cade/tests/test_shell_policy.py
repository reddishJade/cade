"""Shell 权限分类的行为测试。"""

import pytest

from cade.harness.security import (
    ActionExtractor,
    ShellAnalysisPolicyEvaluator,
    analyze_shell_command,
)


def test_read_only_pipeline_exposes_literal_paths_without_approval() -> None:
    analysis = analyze_shell_command("rg needle src tests | head -n 20")

    assert [target.value for target in analysis.resolved_paths] == ["src", "tests"]
    assert analysis.unresolved_effects == ()


def test_quoted_rg_glob_is_not_treated_as_shell_expansion() -> None:
    analysis = analyze_shell_command("rg needle src -g '*.py'")

    assert analysis.unresolved_effects == ()
    assert [target.value for target in analysis.resolved_paths] == ["src"]


@pytest.mark.parametrize(
    "command",
    ["fd parser src", "fd parser src -x echo {}", "fd --exec=echo parser src"],
)
def test_fd_requires_review_without_a_partial_option_parser(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert analysis.resolved_paths == ()
    assert [effect.reason for effect in analysis.unresolved_effects] == [
        "wrapper_command"
    ]


# 失效情形：文件输出漏判、输出路径漏提取、参数缺失误放行、标准输出误判。
@pytest.mark.parametrize("option", ["-fprint", "-fprint0", "-fprintf", "-fls"])
def test_find_file_output_actions_are_mutations(option: str) -> None:
    suffix = " '%p'" if option == "-fprintf" else ""
    analysis = analyze_shell_command(f"find . {option} report.txt{suffix}")

    assert [effect.reason for effect in analysis.unresolved_effects] == ["mutation"]
    assert [(path.value, path.access) for path in analysis.resolved_paths] == [
        (".", "read"),
        ("report.txt", "write"),
    ]


@pytest.mark.parametrize("option", ["-fprint", "-fprint0", "-fprintf", "-fls"])
def test_find_file_output_without_operand_requires_review(option: str) -> None:
    analysis = analyze_shell_command(f"find . {option}")

    assert analysis.unresolved_effects


@pytest.mark.parametrize("action", ["-print", "-print0", "-printf '%p'"])
def test_find_standard_output_actions_remain_read_only(action: str) -> None:
    analysis = analyze_shell_command(f"find . {action}")

    assert analysis.unresolved_effects == ()


@pytest.mark.parametrize("action", ["-delete", "-exec", "-execdir", "-ok", "-okdir"])
def test_find_execution_and_deletion_actions_require_review(action: str) -> None:
    analysis = analyze_shell_command(f"find . {action} echo hello")

    assert analysis.unresolved_effects


def test_read_only_git_commands_do_not_require_review() -> None:
    for command in (
        "git status --short",
        "git diff --stat",
        "git log --oneline -10",
        "git show HEAD",
        "git -C src status",
    ):
        analysis = analyze_shell_command(command)
        assert analysis.unresolved_effects == ()


def test_git_read_subcommand_with_effectful_option_requires_review() -> None:
    for command in (
        "git diff --output=changes.patch",
        "git diff --ext-diff",
        "git -c diff.external=helper diff",
    ):
        analysis = analyze_shell_command(command)
        assert [effect.reason for effect in analysis.unresolved_effects] == [
            "wrapper_command"
        ]


def test_mutating_git_command_requires_review() -> None:
    analysis = analyze_shell_command("git commit -am 'update'")

    assert [effect.reason for effect in analysis.unresolved_effects] == [
        "wrapper_command"
    ]


def test_unknown_command_requires_approval_without_guessing_side_effects() -> None:
    action = ActionExtractor().extract(
        "bash",
        {"command": "pytest -q"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(action)

    assert action.targets[0].value == "pytest -q"
    assert [constraint.decision for constraint in constraints] == ["ask"]
    assert not any(target.kind == "path" for target in action.targets)


def test_explicit_unresolved_allow_skips_unknown_command_constraint() -> None:
    action = ActionExtractor().extract(
        "bash",
        {"command": "pytest -q"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(
        action,
        unresolved_policy="allow",
    )

    assert constraints == ()


def test_redirection_requires_approval() -> None:
    analysis = analyze_shell_command("rg needle src > result.txt")

    assert [effect.reason for effect in analysis.unresolved_effects] == [
        "wrapper_command"
    ]
    assert analysis.resolved_paths == ()


def test_git_clean_is_denied_even_with_alternate_working_directory() -> None:
    action = ActionExtractor().extract(
        "bash",
        {"command": "git -C /tmp/repo clean -fdx"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(action)

    assert [constraint.decision for constraint in constraints] == ["deny"]
    assert "git clean" in constraints[0].reason


def test_unresolved_allow_does_not_override_dangerous_command_denial() -> None:
    action = ActionExtractor().extract(
        "bash",
        {"command": "git -C /tmp/repo clean -fdx"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(
        action,
        unresolved_policy="allow",
    )

    assert [constraint.decision for constraint in constraints] == ["deny"]


def test_recursive_root_delete_is_denied_but_scoped_delete_requires_approval() -> None:
    root_action = ActionExtractor().extract(
        "bash",
        {"command": "rm -rf /"},
        ("shell", "none"),
    )
    scoped_action = ActionExtractor().extract(
        "bash",
        {"command": "rm -rf build"},
        ("shell", "none"),
    )

    root_constraints = ShellAnalysisPolicyEvaluator().evaluate(root_action)
    build_constraints = ShellAnalysisPolicyEvaluator().evaluate(
        scoped_action,
        mutation_policy="ask",
    )
    plan_constraints = ShellAnalysisPolicyEvaluator().evaluate(
        scoped_action,
        mutation_policy="deny",
    )

    assert [constraint.decision for constraint in root_constraints] == ["deny"]
    assert [constraint.decision for constraint in build_constraints] == ["ask"]
    assert [constraint.decision for constraint in plan_constraints] == ["deny"]
    assert [effect.reason for effect in scoped_action.unresolved_effects] == [
        "mutation"
    ]
