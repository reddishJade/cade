"""交互式配置注册表单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.cli.config_registry import (
    SETTING_SPECS,
    apply_setting,
    commit_setting_value,
    find_setting,
    format_setting,
    parse_setting,
    save_setting_text,
)
from cade.harness.config import CadeRuntimeConfig


def _spec(key: str):
    matches = [item for item in SETTING_SPECS if item.key == key]
    assert len(matches) == 1, f"missing spec: {key}"
    return matches[0]


class TestApprovalChoiceMapping:
    def test_router_user_overrides_build_mode(self) -> None:
        config = CadeRuntimeConfig.model_validate(
            {
                "execution_modes": {"default_mode": "build"},
                "security": {
                    "approval_policy": "on-request",
                    "approval_router": "user",
                },
            }
        )
        assert (
            format_setting(_spec("security.approval_policy"), config)
            == "asks for review"
        )

    def test_writer_roundtrip_all_choices(self) -> None:
        spec = _spec("security.approval_policy")
        for token, policy, router in (
            ("always proceeds", "never", None),
            ("agent decides", "on-request", "auto"),
            ("asks for review", "on-request", "user"),
        ):
            raw: dict = {}
            apply_setting(raw, spec, token)
            parsed = CadeRuntimeConfig.model_validate(raw)
            assert parsed.security.approval_policy == policy
            if router is None:
                assert "approval_router" not in raw["security"]
            else:
                assert parsed.security.approval_router == router
            assert format_setting(spec, parsed) == token

    def test_parse_rejects_unknown_tokens(self) -> None:
        spec = _spec("security.approval_policy")
        assert parse_setting(spec, "Agent Decides") == "agent decides"
        with pytest.raises(ValueError):
            parse_setting(spec, "sometimes")

    def test_non_workspace_access_bool_roundtrip(self) -> None:
        spec = _spec("security.non_workspace_access")
        raw: dict = {}
        apply_setting(raw, spec, False)
        parsed = CadeRuntimeConfig.model_validate(raw)
        assert parsed.security.non_workspace_access is False
        assert format_setting(spec, parsed) == "off"

    def test_sandbox_mode_roundtrip(self) -> None:
        spec = _spec("security.sandbox.mode")
        raw: dict = {}
        apply_setting(raw, spec, "read-only")
        parsed = CadeRuntimeConfig.model_validate(raw)
        assert parsed.security.sandbox.mode == "read-only"
        assert format_setting(spec, parsed) == "read-only"


class TestParseSetting:
    def test_enum_rejects_unknown_choice(self) -> None:
        spec = _spec("execution_modes.default_mode")
        assert parse_setting(spec, "PLAN") == "plan"
        with pytest.raises(ValueError):
            parse_setting(spec, "yolo")


class TestApplySetting:
    def test_none_pops_leaf_key(self) -> None:
        raw: dict = {
            "execution_modes": {
                "default_mode": "build",
                "other_key": 1,
            }
        }
        apply_setting(raw, _spec("execution_modes.default_mode"), None)
        assert raw["execution_modes"] == {"other_key": 1}


class TestCommitSettingValue:
    def _write(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_type_mismatch_not_saved(self, tmp_path: Path) -> None:
        config_path = self._write(
            tmp_path / "cade.config.json", {"tools": {"shell": "bash"}}
        )
        ok, _ = commit_setting_value(
            config_path, _spec("execution_modes.default_mode"), 123
        )
        assert not ok
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved == {"tools": {"shell": "bash"}}

    def test_existing_fields_preserved(self, tmp_path: Path) -> None:
        config_path = self._write(
            tmp_path / "cade.config.json",
            {
                "provider": {
                    "model_profiles": {
                        "main": {"transport": "deepseek_chat", "api_key": "sk-x"}
                    }
                },
                "agent": {"max_steps": 10},
            },
        )
        ok, _ = save_setting_text(config_path, _spec("tools.shell"), "bash")
        assert ok
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["provider"]["model_profiles"]["main"]["transport"] == (
            "deepseek_chat"
        )
        assert saved["agent"] == {"max_steps": 10}
        assert saved["tools"]["shell"] == "bash"

    def test_save_text_reports_parse_errors(self, tmp_path: Path) -> None:
        config_path = tmp_path / "cade.config.json"
        ok, message = save_setting_text(
            config_path, _spec("security.approval_policy"), "sometimes"
        )
        assert not ok
        assert "Expected" in message
        assert not config_path.exists()


class TestLookupAndDetails:
    def test_find_by_label_and_key(self) -> None:
        assert find_setting("default mode") is not None
        assert find_setting("tools.shell") is not None
        assert find_setting("nonexistent-keyword-xyz") is None
