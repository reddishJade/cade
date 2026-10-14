from __future__ import annotations

import tempfile
from pathlib import Path

from cade.coding_agent.assembly.security import (
    external_directories_from_security,
    mode_rulesets_from_runtime_config,
    permission_policy_from_security,
    sandbox_policy_from_security,
    sensitive_path_overrides_from_security,
)
from cade.harness.config import (
    CadeRuntimeConfig,
    ExecutionModesRuntimeConfig,
    ModeRuleRuntimeConfig,
    ModeRulesetRuntimeConfig,
    SandboxMode,
    SandboxRuntimeConfig,
    SecurityExternalDirectory,
    SecurityRuntimeConfig,
    SecuritySensitivePathOverride,
)
from cade.harness.security import Action, StaticPolicyEvaluator, Target


class TestAssemblySecurityContract:
    """验证装配层对安全策略、权限展开、沙箱边界及运行模式的组装不变量。"""

    def test_permission_policy_group_expansion_and_concrete_tool_override(self) -> None:
        """测试用户级权限名展开为具体工具，且具体工具配置具备更高优先级（后定义生效）。"""
        security = SecurityRuntimeConfig(
            permissions={"read": "allow"},
            tools={"ls": "deny"},
        )
        policy = permission_policy_from_security(security)
        assert policy is not None

        # 验证 policy.rules 包含 read 组展开的所有工具，且 ls 排在最后覆盖
        tool_names = [r.tool for r in policy.rules]
        assert "read" in tool_names
        assert "glob" in tool_names
        assert "grep" in tool_names
        assert "find" in tool_names
        assert "ls" in tool_names

        # 使用 StaticPolicyEvaluator 评估：验证 last-match-wins 契约
        evaluator = StaticPolicyEvaluator(
            policy.rules, global_default=policy.global_default
        )

        # 1. 组权限展开：read 应该为 allow
        action_read = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value="test.txt", access="read"),),
            input={"path": "test.txt"},
        )
        constraints_read = evaluator.evaluate(action_read)
        assert len(constraints_read) == 1
        assert constraints_read[0].decision == "allow"

        # 2. 具体工具覆盖：ls 虽属于 read 组，但因 tools 显式指定 deny 而排在后面生效
        action_ls = Action(
            tool="ls",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value=".", access="read"),),
            input={"path": "."},
        )
        constraints_ls = evaluator.evaluate(action_ls)
        assert len(constraints_ls) == 1
        assert constraints_ls[0].decision == "deny"

    def test_external_directories_non_workspace_access_toggle(
        self, tmp_path: Path
    ) -> None:
        """测试 non_workspace_access 开关控制是否采用用户配置的外部目录。"""
        ext_path = tmp_path / "shared"
        ext_path.mkdir()

        # 1. 关闭 non_workspace_access 时，用户自定义外部目录被完全忽略
        disabled_sec = SecurityRuntimeConfig(
            non_workspace_access=False,
            external_directories=(
                SecurityExternalDirectory(path=str(ext_path), access="read"),
            ),
        )
        dirs_disabled = external_directories_from_security(disabled_sec)
        assert not any(d.path == ext_path.resolve() for d in dirs_disabled)

        # 2. 开启 non_workspace_access 时，用户自定义外部目录正常包含
        enabled_sec = SecurityRuntimeConfig(
            non_workspace_access=True,
            external_directories=(
                SecurityExternalDirectory(path=str(ext_path), access="read"),
            ),
        )
        dirs_enabled = external_directories_from_security(enabled_sec)
        assert any(
            d.path == ext_path.resolve() and d.access == "read" for d in dirs_enabled
        )

    def test_mode_rulesets_from_runtime_config(self) -> None:
        """测试各执行模式（plan, build, act）的 ruleset 转换与集合属性保留。"""
        plan_rule = ModeRuleRuntimeConfig(
            action="bash",
            effect="deny",
            command="rm",
            subcommand_in=("rf", "fr"),
            flags_any=("-f", "--force"),
        )
        cfg = CadeRuntimeConfig(
            execution_modes=ExecutionModesRuntimeConfig(
                plan=ModeRulesetRuntimeConfig(rules=(plan_rule,)),
            )
        )
        rulesets = mode_rulesets_from_runtime_config(cfg)
        assert "plan" in rulesets
        assert len(rulesets["plan"]) == 1
        r = rulesets["plan"][0]
        assert r.action == "bash"
        assert r.effect == "deny"
        assert r.command == "rm"
        assert r.subcommand_in == {"rf", "fr"}
        assert r.flags_any == {"-f", "--force"}

    def test_sandbox_policy_from_security_writable_roots(self, tmp_path: Path) -> None:
        """测试沙箱模式提取可写目录：仅将可写外部目录与系统临时目录纳入 writable_roots。"""
        writable_ext = tmp_path / "writable_ext"
        writable_ext.mkdir()
        readonly_ext = tmp_path / "readonly_ext"
        readonly_ext.mkdir()

        security = SecurityRuntimeConfig(
            non_workspace_access=True,
            sandbox=SandboxRuntimeConfig(mode=SandboxMode.WORKSPACE_WRITE),
            external_directories=(
                SecurityExternalDirectory(path=str(writable_ext), access="write"),
                SecurityExternalDirectory(path=str(readonly_ext), access="read"),
            ),
        )
        policy = sandbox_policy_from_security(tmp_path, security)
        assert policy.mode == SandboxMode.WORKSPACE_WRITE
        tempdir = Path(tempfile.gettempdir())
        if tempdir.is_dir():
            assert tempdir in policy.writable_roots
        assert writable_ext.resolve() in policy.writable_roots
        assert readonly_ext.resolve() not in policy.writable_roots

    def test_sensitive_path_overrides_normalization(self, tmp_path: Path) -> None:
        """测试相对路径的敏感路径例外根据 project_root 解析为绝对路径。"""
        sec = SecurityRuntimeConfig(
            sensitive_path_overrides=(
                SecuritySensitivePathOverride(path=".env.local", access="read"),
            ),
        )
        overrides = sensitive_path_overrides_from_security(sec, tmp_path)
        assert len(overrides) == 1
        assert overrides[0].path == (tmp_path / ".env.local").resolve()
        assert overrides[0].access == "read"
