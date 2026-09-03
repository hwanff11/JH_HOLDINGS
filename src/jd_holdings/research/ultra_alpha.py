from __future__ import annotations

from dataclasses import dataclass, replace

from jd_holdings.core.v322_allocation import V322Policy

ULTRA_ALPHA_ID = "JDSS-3.3.0-ULTRA-ALPHA"


@dataclass(frozen=True)
class V33LeverageCandidate:
    volatility_brake: float
    leverage_trend: float
    leverage_strong: float

    @property
    def name(self) -> str:
        return (
            "V33_LEVERAGE_"
            f"VOL{int(self.volatility_brake * 100):02d}_"
            f"TREND{int(self.leverage_trend * 100):03d}_"
            f"STRONG{int(self.leverage_strong * 100):03d}"
        )

    def apply(self, baseline: V322Policy) -> V322Policy:
        return replace(
            baseline,
            volatility_brake=self.volatility_brake,
            leverage_trend=self.leverage_trend,
            leverage_strong=self.leverage_strong,
        )


def v33_leverage_candidates() -> tuple[V33LeverageCandidate, ...]:
    return tuple(
        V33LeverageCandidate(volatility, trend, strong)
        for volatility in (0.28, 0.30)
        for trend in (1.25, 1.30)
        for strong in (1.55, 1.60, 1.65, 1.70, 1.75)
    )


def v33_refinement_candidates() -> tuple[V33LeverageCandidate, ...]:
    """Predeclared local neighborhood around the coarse risk-gated candidate."""
    return tuple(
        V33LeverageCandidate(0.30, trend, strong)
        for trend in (1.275, 1.30)
        for strong in (1.50, 1.525, 1.55, 1.575, 1.60)
    )


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
