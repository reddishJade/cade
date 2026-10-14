from __future__ import annotations

from pathlib import Path

from cade.harness.security.permission_model.evaluators import (
    PathBoundaryPolicyEvaluator,
)
from cade.harness.security.permission_model.stores import (
    FileGrantStore,
    InMemoryGrantStore,
    _path_pattern_matches,
)
from cade.harness.security.permission_model.types import (
    Action,
    BoundaryContext,
    ExternalDirectory,
    GrantRecord,
    GrantScope,
    Target,
)


def _make_grant(
    grant_id: str,
    *,
    capability: str = "fs",
    operation: str = "read",
    target_kind: str = "path",
    target_pattern: str = "src/cade",
    access: str = "read",
    decision: str = "allow",
    scope: GrantScope = "session",
) -> GrantRecord:
    return GrantRecord(
        grant_id=grant_id,
        capability=capability,
        operation=operation,
        target_kind=target_kind,
        target_pattern=target_pattern,
        access=access,
        decision=decision,
        scope=scope,
    )


class TestGrantStoresContract:
    """验证 GrantStore 的原子持久化、更新去重与 deny 绝对优先契约。"""

    def test_in_memory_store_deduplicates_by_grant_id(self) -> None:
        store = InMemoryGrantStore()
        g1 = _make_grant("g1", decision="allow")
        g1_updated = _make_grant("g1", decision="deny")

        store.add(g1)
        assert len(store.records()) == 1
        assert store.records()[0].decision == "allow"

        store.add(g1_updated)
        assert len(store.records()) == 1
        assert store.records()[0].decision == "deny"

    def test_deny_decision_takes_absolute_priority_over_allow(self) -> None:
        """当存在多条匹配的 grant 时，deny 必须优先于 allow 胜出。"""
        store = InMemoryGrantStore()
        # g1 允许读取 src/cade
        g1 = _make_grant("g1", target_pattern="src/cade", decision="allow")
        # g2 显式拒绝读取 src/cade
        g2 = _make_grant("g2", target_pattern="src/cade", decision="deny")

        store.add(g1)
        store.add(g2)

        action = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value="src/cade/main.py", access="read"),),
            input={"path": "src/cade/main.py"},
        )

        matched = store.lookup(action, action.targets[0])
        assert matched is not None
        assert matched.grant_id == "g2"
        assert matched.decision == "deny"

    def test_path_pattern_prefix_matching(self) -> None:
        """测试路径前缀匹配：目录必须以边界分界，禁止子串误伤。"""
        assert _path_pattern_matches("src/cade", "src/cade") is True
        assert _path_pattern_matches("src/cade", "src/cade/sub/file.py") is True
        assert _path_pattern_matches("src/cade", "src/cade_extra/file.py") is False

    def test_file_grant_store_lifecycle_and_fault_tolerance(
        self, tmp_path: Path
    ) -> None:
        """测试 FileGrantStore 原子落盘、重载与坏文件容错。"""
        grant_file = tmp_path / ".cade" / "approval_grants.json"
        store = FileGrantStore(grant_file)

        # 1. 初始空文件
        assert store.records() == ()

        # 2. 添加记录并落盘
        g1 = _make_grant("g1", target_pattern="src/cade")
        store.add(g1)
        assert grant_file.exists()
        reloaded = FileGrantStore(grant_file)
        assert len(reloaded.records()) == 1
        assert reloaded.records()[0].grant_id == "g1"

        # 3. 损坏文件容错：当文件损坏时不崩溃，优雅返回空元组
        grant_file.write_text("{corrupted-json-content", encoding="utf-8")
        assert reloaded.records() == ()

        # 4. 后续写入能够自愈恢复
        g2 = _make_grant("g2", target_pattern="tests")
        reloaded.add(g2)
        assert len(reloaded.records()) == 1
        assert reloaded.records()[0].grant_id == "g2"


class TestPathBoundaryEvaluatorContract:
    """验证路径边界求值器对外部目录、.git 元数据与敏感越界的不变量保护。"""

    def test_git_metadata_is_strictly_denied(self, tmp_path: Path) -> None:
        context = BoundaryContext(project_root=tmp_path)
        evaluator = PathBoundaryPolicyEvaluator(context)

        action = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(Target(kind="path", value=".git/config", access="read"),),
            input={"path": ".git/config"},
        )
        constraints = evaluator.evaluate(action)
        assert len(constraints) == 1
        c = constraints[0]
        assert c.decision == "deny"
        assert c.metadata.get("reason_code") == "git_metadata"
        assert c.metadata.get("overrideable") is False

    def test_external_directory_whitelist_enforcement(self, tmp_path: Path) -> None:
        """测试未授权外部目录被拒绝，而在白名单中的外部目录根据权限放行。"""
        project_root = tmp_path / "workspace"
        project_root.mkdir()
        allowed_ext = tmp_path / "external_allowed"
        allowed_ext.mkdir()
        forbidden_ext = tmp_path / "external_forbidden"
        forbidden_ext.mkdir()

        # 配置允许 allowed_ext 只读
        context = BoundaryContext(
            project_root=project_root,
            external_directories=(ExternalDirectory(path=allowed_ext, access="read"),),
        )
        evaluator = PathBoundaryPolicyEvaluator(context)

        # 1. 访问未在白名单的 forbidden_ext
        action_forbidden = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(
                Target(
                    kind="path",
                    value=(forbidden_ext / "file.txt").as_posix(),
                    access="read",
                ),
            ),
            input={"path": (forbidden_ext / "file.txt").as_posix()},
        )
        c_forbidden = evaluator.evaluate(action_forbidden)[0]
        assert c_forbidden.decision == "deny"
        assert c_forbidden.metadata.get("reason_code") == "outside_approved_roots"

        # 2. 访问白名单中的 allowed_ext（read 请求 vs read 授权 -> 放行）
        action_allowed_read = Action(
            tool="read",
            capability="fs",
            operation="read",
            targets=(
                Target(
                    kind="path",
                    value=(allowed_ext / "doc.txt").as_posix(),
                    access="read",
                ),
            ),
            input={"path": (allowed_ext / "doc.txt").as_posix()},
        )
        constraints_allowed = evaluator.evaluate(action_allowed_read)
        # 放行时没有产生 deny constraint
        assert not any(c.decision == "deny" for c in constraints_allowed)

        # 3. 访问白名单中的 allowed_ext（write 请求 vs read 授权 -> 权限不足拒绝）
        action_disallowed_write = Action(
            tool="write",
            capability="fs",
            operation="write",
            targets=(
                Target(
                    kind="path",
                    value=(allowed_ext / "doc.txt").as_posix(),
                    access="write",
                ),
            ),
            input={"path": (allowed_ext / "doc.txt").as_posix()},
        )
        c_disallowed = evaluator.evaluate(action_disallowed_write)[0]
        assert c_disallowed.decision == "deny"
