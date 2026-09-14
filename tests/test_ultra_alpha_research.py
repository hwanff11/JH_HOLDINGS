from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.core.v322_allocation import (
    V322Policy,
    build_qqq_features,
    build_rs_features,
    semiconductor_wins,
)
from jd_holdings.research.ultra_alpha import (
    leverage_policy,
    rs63_policy,
    stepped_hwm_75_82_budget,
    ultra_alpha_policy,
    v33_leverage_candidates,
    v33_refinement_candidates,
)


def test_stepped_hwm_budget_contract():
    assert stepped_hwm_75_82_budget(50_000, 50_000, 50_000) == 50_000
    assert stepped_hwm_75_82_budget(50_000, 80_000, 80_000) == 72_500
    assert stepped_hwm_75_82_budget(50_000, 100_000, 100_000) == 87_500
    assert stepped_hwm_75_82_budget(50_000, 150_000, 150_000) == 128_500
    assert stepped_hwm_75_82_budget(50_000, 150_000, 90_000) == 90_000
    with pytest.raises(ValueError, match="50,000"):
        stepped_hwm_75_82_budget(40_000, 40_000, 40_000)


def test_ultra_alpha_policy_changes_only_declared_parameters(config):
    baseline = V322Policy.from_config(config)
    leverage = leverage_policy(baseline)
    rs63 = rs63_policy(baseline)
    ultra = ultra_alpha_policy(baseline)
    assert (leverage.volatility_brake, leverage.leverage_trend, leverage.leverage_strong) == (
        0.28,
        1.30,
        1.75,
    )
    assert (rs63.rs_lookback, rs63.rs_sleeve_fraction) == (63, 0.60)
    assert ultra.jdss_overlay_weight == baseline.jdss_overlay_weight == 0.05
    assert ultra.initial_capital == baseline.initial_capital == Decimal("50000")


def test_rs_comparison_uses_same_63_session_horizon(config):
    policy = rs63_policy(V322Policy.from_config(config))
    index = pd.date_range("2020-01-01", periods=130, freq="B")
    qqq = pd.DataFrame({"close": range(100, 230)}, index=index)
    soxx = pd.DataFrame({"close": range(100, 360, 2)}, index=index)
    qqq_features = build_qqq_features(qqq, policy)
    soxx_features = build_rs_features(soxx, policy)
    qqq_row = qqq_features.iloc[-1]
    semi_row = soxx_features.iloc[-1]
    expected_qqq = qqq.iloc[-1, 0] / qqq.iloc[-64, 0] - 1
    assert qqq_row["rs_return"] == pytest.approx(expected_qqq)
    assert semiconductor_wins(qqq_row, semi_row)


def test_v33_candidate_grid_changes_only_leverage_ladder(config):
    baseline = V322Policy.from_config(config)
    candidates = v33_leverage_candidates()
    assert len(candidates) == 20
    assert len({candidate.name for candidate in candidates}) == 20
    for candidate in candidates:
        policy = candidate.apply(baseline)
        assert policy.rs_lookback == baseline.rs_lookback == 126
        assert policy.rs_sleeve_fraction == baseline.rs_sleeve_fraction == 0.50
        assert policy.hwm_reinvestment_fraction == Decimal("0.75")
        assert policy.jdss_overlay_weight == baseline.jdss_overlay_weight == 0.05

    refinement = v33_refinement_candidates()
    assert len(refinement) == 10
    assert all(candidate.volatility_brake == 0.30 for candidate in refinement)
    assert any(candidate.leverage_strong == 1.50 for candidate in refinement)
    assert any(candidate.leverage_strong == 1.525 for candidate in refinement)
