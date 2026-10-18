"""模型角色按需构建：省略的角色复用 main，显式角色单独装配。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from cade.ai.providers import registry
from cade.ai.providers.base import ModelProvider
from cade.ai.providers.registry import (
    ModelProfileConfig,
    ModelProfileProto,
    ProviderSettings,
    build_provider_bundle,
)
from cade.coding_agent.assembly import providers as profile_assembly
from cade.harness.config import CadeRuntimeConfig


def test_bundle_does_not_create_implicit_role_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    def fake_build(
        _profile: ModelProfileProto,
        name: str,
        _env_files: tuple[Path, ...],
        _runtime: object,
    ) -> ModelProvider:
        created.append(name)
        return cast("ModelProvider", object())

    monkeypatch.setattr(registry, "_build_llm_profile", fake_build)
    bundle = build_provider_bundle(ProviderSettings(env_files=(), model_profiles={}))
    assert created == ["main"]
    assert set(bundle.llms) == {"main"}
    assert bundle.llm is bundle.llms["main"]


def test_explicit_roles_are_built_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    def fake_build(
        _profile: ModelProfileProto,
        name: str,
        _env_files: tuple[Path, ...],
        _runtime: object,
    ) -> ModelProvider:
        created.append(name)
        return cast("ModelProvider", object())

    monkeypatch.setattr(registry, "_build_llm_profile", fake_build)
    main = ModelProfileConfig(chat_model="main-model")
    reviewer = ModelProfileConfig(chat_model="review-model")
    bundle = build_provider_bundle(
        ProviderSettings(
            env_files=(),
            model_profiles={"main": main, "reviewer": reviewer},
        )
    )
    assert created == ["main", "reviewer"]
    assert set(bundle.llms) == {"main", "reviewer"}
    assert bundle.llms["main"] is not bundle.llms["reviewer"]


def test_profile_resolution_does_not_invent_subagent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(*_args: object, **_kwargs: object) -> ModelProfileConfig:
        return ModelProfileConfig(chat_model="main-model")

    monkeypatch.setattr(profile_assembly, "resolve_model_profile", resolve)
    profiles = profile_assembly.resolve_model_profiles(CadeRuntimeConfig(), ())
    assert set(profiles) == {"main"}
