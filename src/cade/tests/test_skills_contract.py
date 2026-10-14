"""保护技能定义解析、Frontmatter 规范化与注册表发现优先级契约。

控制性回归使用真实 parse_skill 与 SkillRegistry。
在独立 tmp_path 空间断言合法/非法 YAML 边界、目录名与技能名一致性约束，
以及多层级技能搜索目录的 first-wins 优先级覆盖。
"""

from __future__ import annotations

from pathlib import Path

from cade.harness.skills.parsing import parse_skill
from cade.harness.skills.registry import SkillRegistry


def test_parse_skill_valid_frontmatter_and_body() -> None:
    """验证标准 SKILL.md 能够正确提取结构化元数据与 Markdown 正文。"""
    raw = """---
name: code-review
description: Perform comprehensive code review.
---
# Instructions
Always check for security and performance.
"""
    result = parse_skill(raw, skill_directory_name="code-review")
    assert result is not None
    frontmatter, body = result
    assert frontmatter["name"] == "code-review"
    assert frontmatter["description"] == "Perform comprehensive code review."
    assert body == "# Instructions\nAlways check for security and performance."


def test_parse_skill_invalid_frontmatters_rejected() -> None:
    """验证缺少闭合分界、缺少必要字段或非法 YAML 时直接拒绝返回 None。"""
    # 1. 缺少闭合 --- 分界
    unclosed = """---
name: test-skill
description: Broken YAML
# No closing delimiter
"""
    assert parse_skill(unclosed, skill_directory_name="test-skill") is None

    # 2. YAML 语法严重损坏
    broken_yaml = """---
: : : invalid yaml syntax %%%%
---
Body
"""
    assert parse_skill(broken_yaml, skill_directory_name="broken") is None

    # 3. 缺少必填的 description 字段
    no_desc = """---
name: valid-name
---
Body
"""
    assert parse_skill(no_desc, skill_directory_name="valid-name") is None

    # 4. description 为纯空白
    empty_desc = """---
name: valid-name
description: "   "
---
Body
"""
    assert parse_skill(empty_desc, skill_directory_name="valid-name") is None


def test_skill_registry_discovery_and_priority_override(tmp_path: Path) -> None:
    """验证 SkillRegistry 能够递归扫描目录，且相同技能名按目录优先级 (first-wins) 覆盖。"""
    # 模拟用户工作区技能目录（优先级更高，数值更小）
    workspace_skills = tmp_path / "workspace_skills"
    # 模拟内置技能目录（优先级更低，数值更大）
    builtin_skills = tmp_path / "builtin_skills"

    # 在内置技能库定义 review 技能
    builtin_review = builtin_skills / "review"
    builtin_review.mkdir(parents=True)
    (builtin_review / "SKILL.md").write_text(
        "---\nname: review\ndescription: Builtin review skill\n---\nBuiltin body",
        encoding="utf-8",
    )

    # 在工作区技能库定制同名 review 技能（用于覆盖内置）
    ws_review = workspace_skills / "review"
    ws_review.mkdir(parents=True)
    (ws_review / "SKILL.md").write_text(
        "---\nname: review\ndescription: Workspace customized review\n---\nCustom body",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    # 搜索目录带优先级：数值越小优先级越高 (workspace=10, builtin=20)
    registry.discover([(builtin_skills, 20), (workspace_skills, 10)])

    summaries = registry.list_summaries()
    assert len(summaries) == 1
    assert summaries[0].name == "review"
    # 必须命中更高优先级的 workspace 定制描述
    assert summaries[0].description == "Workspace customized review"

    loaded = registry.load("review")
    assert loaded is not None
    assert loaded.content == "Custom body"
