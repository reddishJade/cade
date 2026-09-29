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


# 失效情形：baseline 中的行数统计和行号显示触发不必要的审查。
@pytest.mark.parametrize("command", ["wc -l src/main.py", "nl -ba src/main.py"])
def test_simple_line_inspection_is_read_only(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert analysis.unresolved_effects == ()
    assert [target.value for target in analysis.resolved_paths] == ["src/main.py"]


# 失效情形：只读行范围显示被审查，写入或执行脚本却被误放行。
@pytest.mark.parametrize(
    "command",
    ["sed -n '1,20p' src/main.py", "sed -n '1,20p;30,40p' src/main.py"],
)
def test_sed_numeric_print_ranges_are_read_only(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert analysis.unresolved_effects == ()
    assert [target.value for target in analysis.resolved_paths] == ["src/main.py"]


@pytest.mark.parametrize(
    "command",
    [
        "sed -i 's/a/b/' src/main.py",
        "sed -n '1,20p' -i src/main.py",
        "sed -n '1w report.txt' src/main.py",
        "sed -n '1e touch report.txt' src/main.py",
    ],
)
def test_sed_other_forms_still_require_review(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert [effect.reason for effect in analysis.unresolved_effects] == [
        "wrapper_command"
    ]


# 失效情形：等号形式或分离参数形式的外部程序选项被误判为只读。
@pytest.mark.parametrize(
    "command",
    [
        "rg --pre=helper needle src",
        "rg --pre helper needle src",
        "rg --hostname-bin=helper needle src",
        "rg --hostname-bin helper needle src",
    ],
)
def test_rg_external_program_options_require_review(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert [effect.reason for effect in analysis.unresolved_effects] == [
        "wrapper_command"
    ]


def test_rg_external_program_option_reaches_permission_review() -> None:
    action = ActionExtractor().extract(
        "bash",
        {"command": "rg --pre=helper needle src"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(action)

    assert [constraint.decision for constraint in constraints] == ["ask"]


# 失效情形：常见只读搜索反复审查，或选项值被误认为搜索根。
@pytest.mark.parametrize(
    "command",
    [
        "fd parser src",
        "fd -e py parser src",
        "fd --type file --max-results 20 parser src",
        "fd --color=never --glob '*.py' src",
        "fd -- -x src",
    ],
)
def test_fd_known_read_only_forms_run_without_review(command: str) -> None:
    analysis = analyze_shell_command(command)

    assert analysis.unresolved_effects == ()
    assert [target.value for target in analysis.resolved_paths] == ["src"]


# 失效情形：执行动作、执行别名或未知选项被部分参数解析器放行。
@pytest.mark.parametrize(
    "command",
    [
        "fd parser src -x echo {}",
        "fd --exec=echo parser src",
        "fd -X echo {}",
        "fd --list-details parser src",
        "fd --unknown parser src",
    ],
)
def test_fd_effectful_or_unknown_options_require_review(command: str) -> None:
    analysis = analyze_shell_command(command)

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
