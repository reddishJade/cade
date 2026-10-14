"""保护上下文预算策略 (ContextPolicy) 算力窗口、安全余量与准入目标契约。

控制性回归使用真实 ContextPolicy 模型。
断言物理窗口输入预算抵扣、安全余量上限截断、准入软目标计算，以及非法预算配置拒绝。
"""

from __future__ import annotations

import pytest

from cade.agent.context_policy import ContextPolicy


def test_context_policy_math_and_budget_invariants() -> None:
    """验证 ContextPolicy 的输入预算、安全余量 (2%) 及准入目标推导契约。"""
    # 物理窗口 100k，预留输出 10k，余量 2% = 2k
    policy = ContextPolicy(
        physical_window=100_000,
        output_reserve=10_000,
        trigger_ratio=0.9,
        next_turn_input_tokens=1_000,
    )

    # 1. 安全余量推导：100,000 // 50 = 2,000 (未超过 8192 上限)
    assert policy.headroom == 2_000

    # 2. 有效输入预算 = 物理窗口 - 输出预留 - 安全余量
    assert policy.input_budget == 100_000 - 10_000 - 2_000  # 88,000

    # 3. 轮换触发阈值 = min(input_budget, physical_window * trigger_ratio)
    # min(88,000, 90,000) = 88,000
    assert policy.rotation_threshold == 88_000

    # 4. 下轮输入保留量与准入目标
    expected_allowance = min(1_000, policy.rotation_threshold // 8)
    assert policy.next_input_allowance == expected_allowance
    assert policy.admission_target == policy.rotation_threshold - expected_allowance


def test_context_policy_headroom_capping() -> None:
    """验证超大上下文模型（如 100 万 token 窗口）的安全余量严格截断在 8192 上限。"""
    huge_policy = ContextPolicy(
        physical_window=1_000_000,
        output_reserve=20_000,
    )
    # 1,000,000 // 50 = 20,000，但必须被 min(8192, ...) 限制在 8192
    assert huge_policy.headroom == 8192
    assert huge_policy.input_budget == 1_000_000 - 20_000 - 8192


def test_context_policy_rejection_of_impossible_budgets() -> None:
    """验证当预留输出与余量超出物理窗口时，或者比率非法时严格拒绝。"""
    # 1. 输出预留过大，导致无剩余输入预算
    with pytest.raises(
        ValueError, match="Output reserve and headroom leave no input budget"
    ):
        ContextPolicy(physical_window=20_000, output_reserve=20_000)

    # 2. 非法的 trigger_ratio (> 1 或 <= 0)
    with pytest.raises(
        ValueError, match="Invalid output reserve or rollover trigger ratio"
    ):
        ContextPolicy(physical_window=100_000, trigger_ratio=1.5)

    with pytest.raises(
        ValueError, match="Invalid output reserve or rollover trigger ratio"
    ):
        ContextPolicy(physical_window=100_000, trigger_ratio=0.0)

    # 3. 负数余量
    with pytest.raises(ValueError, match="Context headroom must be nonnegative"):
        ContextPolicy(physical_window=100_000, headroom_tokens=-10)
