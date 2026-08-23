from __future__ import annotations

from dataclasses import replace

from jd_holdings.core.v322_allocation import V322Policy

ULTRA_ALPHA_ID = "JDSS-3.3.0-ULTRA-ALPHA"


def stepped_hwm_75_82_budget(
    initial_capital: float,
    high_water: float,
    current_equity: float,
) -> float:
    """Risk budget from the proposed stepped 75%/82% HWM contract."""
    if abs(initial_capital - 50_000.0) > 1e-9:
        raise ValueError("Ultra Alpha initial capital must be USD 50,000")
    high_water = max(initial_capital, high_water)
    if high_water <= 100_000.0:
        cap = initial_capital + 0.75 * (high_water - initial_capital)
    else:
        cap = initial_capital + 0.75 * 50_000.0 + 0.82 * (
            high_water - 100_000.0
        )
    return max(0.0, min(current_equity, cap))


def leverage_policy(baseline: V322Policy) -> V322Policy:
    return replace(
        baseline,
        volatility_brake=0.28,
        leverage_trend=1.30,
        leverage_strong=1.75,
    )


def rs63_policy(baseline: V322Policy) -> V322Policy:
    return replace(
        baseline,
        rs_lookback=63,
        rs_sleeve_fraction=0.60,
    )


def ultra_alpha_policy(baseline: V322Policy) -> V322Policy:
    return rs63_policy(leverage_policy(baseline))
