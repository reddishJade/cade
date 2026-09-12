"""模型注册表与选择语法单元测试。"""

from __future__ import annotations

import pytest

from xcode.ai.models import (
    ModelMode,
    effective_rollover_threshold,
    get_codex_models,
    get_model,
    get_model_reasoning_efforts,
    get_models,
    get_providers,
    parse_model_mode,
    resolve_model,
)
from xcode.ai.resolver import ModelResolver


class TestParseModelMode:
    def test_basic_model(self) -> None:
        assert parse_model_mode("gpt-4") == ModelMode(model="gpt-4")

    def test_provider_model(self) -> None:
        assert parse_model_mode("openai/gpt-4") == ModelMode(
            model="gpt-4", provider="openai"
        )

    def test_provider_model_thinking(self) -> None:
        assert parse_model_mode("openai/gpt-4:low") == ModelMode(
            model="gpt-4", provider="openai", thinking_level="low"
        )

    def test_model_thinking_no_provider(self) -> None:
        assert parse_model_mode("gpt-4:high") == ModelMode(
            model="gpt-4", thinking_level="high"
        )

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            parse_model_mode("")

    def test_whitespace_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            parse_model_mode("   ")

    def test_invalid_thinking_level_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid thinking level"):
            parse_model_mode("gpt-4:ultra")

    def test_all_valid_thinking_levels(self) -> None:
        for level in (
            "off",
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            result = parse_model_mode(f"gpt-4:{level}")
            assert result.thinking_level == level

    def test_provider_only_no_model_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            parse_model_mode("openai/")

    def test_thinking_level_case_normalized(self) -> None:
        assert parse_model_mode("gpt-4:HIGH").thinking_level == "high"


class TestRolloverThreshold:
    @pytest.mark.parametrize(
        "provider_name",
        ("openai", "chatglm"),
    )
    def test_known_model_uses_its_registered_context_window(
        self, provider_name: str
    ) -> None:
        model = get_models(provider_name)[0]
        threshold = effective_rollover_threshold(
            model.id,
            reserve_tokens=16_384,
            trigger_ratio=0.95,
        )

        expected = min(int(model.context_window * 0.95), model.context_window - 16_384)
        assert threshold == expected

    def test_reserve_remains_hard_upper_bound(self) -> None:
        model = get_models("chatglm")[0]
        threshold = effective_rollover_threshold(
            f"chatglm/{model.id}",
            reserve_tokens=80_000,
            trigger_ratio=0.95,
        )

        assert threshold == model.context_window - 80_000

    def test_unknown_model_keeps_fallback_threshold(self) -> None:
        threshold = effective_rollover_threshold(
            "unknown-model",
            fallback_threshold=32_000,
            trigger_ratio=0.95,
        )

        assert threshold == 32_000

    def test_context_window_override_wins(self) -> None:
        model = get_models("openai")[0]
        threshold = effective_rollover_threshold(
            model.id,
            reserve_tokens=16_384,
            trigger_ratio=0.95,
            context_window_override=262_144,
        )

        assert threshold == 245_760

    def test_context_window_override_respects_reserve(self) -> None:
        model = get_models("openai")[0]
        threshold = effective_rollover_threshold(
            model.id,
            reserve_tokens=200_000,
            context_window_override=262_144,
        )

        assert threshold == 62_144

    def test_non_positive_override_falls_back_to_registry(self) -> None:
        model = get_models("openai")[0]
        threshold = effective_rollover_threshold(
            model.id,
            reserve_tokens=16_384,
            trigger_ratio=0.95,
            context_window_override=0,
        )

        expected = min(int(model.context_window * 0.95), model.context_window - 16_384)
        assert threshold == expected

    def test_specific_model_id_wins_over_prefix_model(self) -> None:
        model = get_models("openai")[-1]
        threshold = effective_rollover_threshold(
            f"openai/{model.id}",
            reserve_tokens=0,
            trigger_ratio=1,
        )

        assert threshold == model.context_window


class TestResolveModel:
    def test_exact_match(self) -> None:
        registered = get_models("openai")[0]
        model = resolve_model("openai", registered.id)
        assert model is not None
        assert model.id == registered.id

    def test_fallback_to_first(self) -> None:
        first = get_models("openai")[0]
        model = resolve_model("openai", "nonexistent-model")
        assert model is not None
        assert model.id == first.id

    def test_unknown_provider_returns_generic(self) -> None:
        model = resolve_model("unknown_provider", "some-model")
        assert model.id == "some-model"
        assert model.provider == "unknown_provider"

    def test_empty_model_id(self) -> None:
        model = resolve_model("unknown_provider", "")
        assert model.id == ""


class TestRegistryAccess:
    def test_get_providers(self) -> None:
        providers = get_providers()
        assert "openai" in providers
        assert "deepseek" in providers
        assert "chatglm" in providers
        assert "mimo" in providers

    def test_get_models_openai(self) -> None:
        models = get_models("openai")
        ids = [m.id for m in models]
        assert models
        assert len(ids) == len(set(ids))
        assert all(model.provider == "openai" for model in models)

    def test_get_codex_models_returns_unique_registered_models(self) -> None:
        models = get_codex_models()
        ids = [model.id for model in models]
        assert models
        assert len(ids) == len(set(ids))
        assert all(model.provider == "openai" for model in models)

    def test_get_models_unknown_provider(self) -> None:
        assert get_models("nonexistent") == []

    def test_get_model_existing(self) -> None:
        registered = get_models("deepseek")[0]
        model = get_model("deepseek", registered.id)
        assert model is not None
        assert model.name == registered.name

    def test_get_model_nonexistent(self) -> None:
        assert get_model("openai", "does-not-exist") is None

    def test_openai_models_declare_supported_reasoning_efforts(self) -> None:
        assert get_model_reasoning_efforts("gpt-5.6-luna") == (
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        )
        assert "minimal" not in get_model_reasoning_efforts("gpt-5.5")
        assert "none" not in get_model_reasoning_efforts("gpt-6-astra")
        assert get_model_reasoning_efforts("unknown-model") == ()


class TestModelResolver:
    def test_resolve_alias(self) -> None:
        codex_model = ModelResolver.resolve_alias("codex")
        assert codex_model == ModelResolver.resolve_alias("CODEX")
        assert codex_model == ModelResolver.resolve_alias("openai-codex")
        assert codex_model in {model.id for model in get_codex_models()}
        assert ModelResolver.resolve_alias("unaliased-model") == "unaliased-model"

    def test_infer_provider(self) -> None:
        assert ModelResolver.infer_provider(get_codex_models()[0].id) == "openai"
        assert ModelResolver.infer_provider("codex") == "openai"
        for provider_name in ("deepseek", "chatglm", "mimo"):
            model = get_models(provider_name)[0]
            assert ModelResolver.infer_provider(model.id) == provider_name

    def test_is_codex_supported(self) -> None:
        current_model = get_codex_models()[0].id
        non_codex_model = get_models("deepseek")[0].id
        assert ModelResolver.is_codex_supported(current_model) is True
        assert ModelResolver.is_codex_supported("codex") is True
        assert ModelResolver.is_codex_supported(non_codex_model) is False
        assert ModelResolver.is_codex_supported("unregistered-model") is False

    def test_get_default_base_url(self) -> None:
        assert (
            ModelResolver.get_default_base_url("openai_codex")
            == "https://chatgpt.com/backend-api"
        )
        assert (
            ModelResolver.get_default_base_url("openai_chat")
            == "https://api.openai.com/v1"
        )
        assert (
            ModelResolver.get_default_base_url("deepseek_chat")
            == "https://api.deepseek.com"
        )

    def test_resolve_one_stop(self) -> None:
        res_codex = ModelResolver.resolve("codex")
        assert res_codex.model == ModelResolver.resolve_alias("codex")
        assert res_codex.provider == "openai"
        assert res_codex.transport == "openai_codex"
        assert res_codex.default_base_url == "https://chatgpt.com/backend-api"

        deepseek_model = get_models("deepseek")[0]
        res_deepseek = ModelResolver.resolve(f"deepseek/{deepseek_model.id}")
        assert res_deepseek.model == deepseek_model.id
        assert res_deepseek.provider == "deepseek"
        assert res_deepseek.transport == "deepseek_chat"

        current_model = get_codex_models()[-1].id
        res_with_oauth = ModelResolver.resolve(current_model, has_oauth=True)
        assert res_with_oauth.model == current_model
        assert res_with_oauth.transport == "openai_codex"
