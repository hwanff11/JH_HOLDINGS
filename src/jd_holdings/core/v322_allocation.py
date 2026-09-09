from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd

V322_STRATEGY_ID = "JDSS-3.2.2-RS6M-ONEWAY-HWM75"
V322_CONFIG_VERSION = "3.2.2"
ALLOCATION_SYMBOLS = ("QQQ", "TQQQ", "SOXL")


@dataclass(frozen=True)
class V322Policy:
    initial_capital: Decimal
    hwm_reinvestment_fraction: Decimal
    sma_short: int
    sma_long: int
    slope_lookback: int
    momentum_short: int
    momentum_medium: int
    momentum_long: int
    volatility_window: int
    volatility_brake: float
    trend_votes_required: int
    leverage_defensive: float
    leverage_normal: float
    leverage_trend: float
    leverage_strong: float
    rs_benchmark: str
    rs_lookback: int
    rs_sleeve_fraction: float
    jdss_overlay_weight: float
    monthly_reset: str

    @classmethod
    def from_config(cls, config: Any) -> V322Policy:
        if config.version != V322_STRATEGY_ID or config.config_version != V322_CONFIG_VERSION:
            raise ValueError(
                "V3.2.2 allocation engine requires "
                f"{V322_STRATEGY_ID}/{V322_CONFIG_VERSION}"
            )
        raw = config.market_regime.get("v322_allocation")
        if not isinstance(raw, dict):
            raise ValueError("market_regime.v322_allocation 설정이 필요합니다")
        policy = cls(
            initial_capital=Decimal(str(raw["initial_capital"])),
            hwm_reinvestment_fraction=Decimal(str(raw["hwm_reinvestment_fraction"])),
            sma_short=int(raw["sma_short"]),
            sma_long=int(raw["sma_long"]),
            slope_lookback=int(raw["slope_lookback"]),
            momentum_short=int(raw["momentum_short"]),
            momentum_medium=int(raw["momentum_medium"]),
            momentum_long=int(raw["momentum_long"]),
            volatility_window=int(raw["volatility_window"]),
            volatility_brake=float(raw["volatility_brake"]),
            trend_votes_required=int(raw["trend_votes_required"]),
            leverage_defensive=float(raw["leverage_defensive"]),
            leverage_normal=float(raw["leverage_normal"]),
            leverage_trend=float(raw["leverage_trend"]),
            leverage_strong=float(raw["leverage_strong"]),
            rs_benchmark=str(raw["rs_benchmark"]).upper(),
            rs_lookback=int(raw["rs_lookback"]),
            rs_sleeve_fraction=float(raw["rs_sleeve_fraction"]),
            jdss_overlay_weight=float(raw["jdss_overlay_weight"]),
            monthly_reset=str(raw["monthly_reset"]),
        )
        policy.validate(config)
        return policy

    def validate(self, config: Any) -> None:
        errors: list[str] = []
        if self.initial_capital != Decimal("50000"):
            errors.append("V3.2.2 initial_capital은 50000으로 고정합니다")
        if self.hwm_reinvestment_fraction != Decimal("0.75"):
            errors.append("V3.2.2 HWM 재투자율은 0.75로 동결되어 있습니다")
        if (self.sma_short, self.sma_long, self.slope_lookback) != (50, 200, 21):
            errors.append("V3.2.2 SMA 규칙은 50/200, slope 21로 동결되어 있습니다")
        if (
            self.momentum_short,
            self.momentum_medium,
            self.momentum_long,
        ) != (21, 63, 126):
            errors.append("V3.2.2 모멘텀 기간은 21/63/126으로 동결되어 있습니다")
        if self.volatility_window != 20 or abs(self.volatility_brake - 0.30) > 1e-12:
            errors.append("V3.2.2 변동성 브레이크는 20일/30%로 동결되어 있습니다")
        if self.trend_votes_required != 3:
            errors.append("V3.2.2 trend vote는 5개 중 3개로 동결되어 있습니다")
        if (
            self.leverage_defensive,
            self.leverage_normal,
            self.leverage_trend,
            self.leverage_strong,
        ) != (0.5, 1.0, 1.25, 1.5):
            errors.append("V3.2.2 노출 단계는 0.5/1.0/1.25/1.5배로 동결되어 있습니다")
        if self.rs_benchmark != "SOXX" or self.rs_lookback != 126:
            errors.append("V3.2.2 RS 기준은 SOXX 126거래일로 동결되어 있습니다")
        if abs(self.rs_sleeve_fraction - 0.50) > 1e-12:
            errors.append("V3.2.2 SOXL 레버리지 슬리브 비중은 50%로 동결되어 있습니다")
        if abs(self.jdss_overlay_weight - 0.05) > 1e-12:
            errors.append("V3.2.2 JDSS overlay는 5%로 동결되어 있습니다")
        if self.monthly_reset != "first_session_close":
            errors.append("V3.2.2 월간 reset은 first_session_close만 지원합니다")
        if config.portfolio.live_enabled:
            errors.append("V3.2.2 live_enabled는 릴리즈 단계에서 계속 false여야 합니다")
        if errors:
            raise ValueError("; ".join(errors))


@dataclass(frozen=True)
class AllocationState:
    month: str
    leverage: float
    semiconductor_active: bool


def build_qqq_features(frame: pd.DataFrame, policy: V322Policy) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    returns = close.pct_change(fill_method=None)
    out["sma_short"] = close.rolling(policy.sma_short).mean()
    out["sma_long"] = close.rolling(policy.sma_long).mean()
    out["ret_short"] = close / close.shift(policy.momentum_short) - 1
    out["ret_medium"] = close / close.shift(policy.momentum_medium) - 1
    out["ret_long"] = close / close.shift(policy.momentum_long) - 1
    # Keep the QQQ side of relative strength on the exact same lookback as
    # the semiconductor benchmark.  For production V3.2.2 this remains 126
    # sessions, while research policies can exercise a different RS horizon
    # without silently comparing unlike periods.
    out["rs_return"] = close / close.shift(policy.rs_lookback) - 1
    out["volatility"] = returns.rolling(policy.volatility_window).std() * (252**0.5)
    out["sma_long_slope"] = (
        out["sma_long"] / out["sma_long"].shift(policy.slope_lookback) - 1
    )
    return out


def build_rs_features(frame: pd.DataFrame, policy: V322Policy) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    out["rs_return"] = close / close.shift(policy.rs_lookback) - 1
    return out


def _row_ready(row: pd.Series) -> bool:
    fields = (
        "sma_short",
        "sma_long",
        "ret_short",
        "ret_medium",
        "ret_long",
        "volatility",
        "sma_long_slope",
    )
    return not any(pd.isna(row.get(field)) for field in fields)


def trend_vote(row: pd.Series, policy: V322Policy) -> bool:
    if not _row_ready(row):
        return False
    votes = sum(
        (
            float(row["ret_short"]) > 0,
            float(row["ret_medium"]) > 0,
            float(row["ret_long"]) > 0,
            float(row["sma_short"]) > float(row["sma_long"]),
            float(row["sma_long_slope"]) > 0,
        )
    )
    return votes >= policy.trend_votes_required


def base_leverage(row: pd.Series, policy: V322Policy) -> float:
    if not _row_ready(row):
        return policy.leverage_normal
    if float(row["volatility"]) >= policy.volatility_brake:
        return policy.leverage_defensive
    if float(row["close"]) <= float(row["sma_long"]):
        return policy.leverage_normal
    strong = (
        float(row["sma_short"]) > float(row["sma_long"])
        and float(row["ret_medium"]) > 0
        and float(row["ret_long"]) > 0
    )
    if strong:
        return policy.leverage_strong
    if trend_vote(row, policy):
        return policy.leverage_trend
    return policy.leverage_normal


def semiconductor_wins(qqq_row: pd.Series, semi_row: pd.Series) -> bool:
    qqq_return = qqq_row.get("rs_return", qqq_row.get("ret_long"))
    semi_return = semi_row.get("rs_return")
    if pd.isna(qqq_return) or pd.isna(semi_return):
        return False
    return float(semi_return) > 0 and float(semi_return) > float(qqq_return)


def advance_state(
    previous: AllocationState | None,
    timestamp: pd.Timestamp,
    qqq_row: pd.Series,
    semi_row: pd.Series,
    policy: V322Policy,
) -> AllocationState:
    month = str(timestamp.to_period("M"))
    if previous is None or previous.month != month:
        return AllocationState(
            month=month,
            leverage=base_leverage(qqq_row, policy),
            semiconductor_active=semiconductor_wins(qqq_row, semi_row),
        )

    leverage = previous.leverage
    if (
        not pd.isna(qqq_row.get("volatility"))
        and float(qqq_row["volatility"]) >= policy.volatility_brake
        and leverage > policy.leverage_defensive
    ):
        leverage = policy.leverage_defensive
    semiconductor_active = previous.semiconductor_active
    if semiconductor_active and not semiconductor_wins(qqq_row, semi_row):
        semiconductor_active = False
    return AllocationState(
        month=month,
        leverage=leverage,
        semiconductor_active=semiconductor_active,
    )


def base_weights(state: AllocationState, policy: V322Policy) -> dict[str, float]:
    leverage = state.leverage
    if leverage <= 1.0:
        return {"QQQ": max(0.0, leverage)}
    sleeve = (leverage - 1.0) / 2.0
    weights = {"QQQ": 1.0 - sleeve}
    if state.semiconductor_active:
        weights["TQQQ"] = sleeve * (1.0 - policy.rs_sleeve_fraction)
        weights["SOXL"] = sleeve * policy.rs_sleeve_fraction
    else:
        weights["TQQQ"] = sleeve
    return {symbol: weight for symbol, weight in weights.items() if weight > 0}


def apply_jdss_overlay(
    weights: dict[str, float],
    *,
    active_tqqq: bool,
    active_soxl: bool,
    policy: V322Policy,
) -> dict[str, float]:
    result = {symbol: float(weights.get(symbol, 0.0)) for symbol in ALLOCATION_SYMBOLS}
    active = [
        symbol
        for symbol, enabled in (("TQQQ", active_tqqq), ("SOXL", active_soxl))
        if enabled
    ]
    shift = min(policy.jdss_overlay_weight, result["QQQ"]) if active else 0.0
    result["QQQ"] -= shift
    for symbol in active:
        result[symbol] += shift / len(active)
    return {symbol: weight for symbol, weight in result.items() if weight > 1e-12}


def virtual_active_series(result: Any, index: pd.DatetimeIndex) -> pd.Series:
    """Replay the research overlay state and delay it one session to avoid look-ahead."""
    by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for trade in result.trades:
        by_date.setdefault(pd.Timestamp(trade["date"]), []).append(trade)
    end_of_day_quantity = 0
    active: list[bool] = []
    for timestamp in index:
        trades = by_date.get(timestamp, [])
        buys = sum(int(item["quantity"]) for item in trades if item["side"] == "BUY")
        sells = sum(int(item["quantity"]) for item in trades if item["side"] == "SELL")
        open_quantity = end_of_day_quantity + buys
        active.append(open_quantity > 0)
        end_of_day_quantity = max(0, open_quantity - sells)
    expected = int(result.open_position.get("quantity", 0))
    if end_of_day_quantity != expected:
        raise AssertionError(
            f"JDSS overlay replay mismatch: replay={end_of_day_quantity} expected={expected}"
        )
    return pd.Series(active, index=index).shift(1, fill_value=False)


def replay_targets(
    qqq_frame: pd.DataFrame,
    semiconductor_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
) -> pd.DataFrame:
    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semiconductor_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)
    state: AllocationState | None = None
    rows: list[dict[str, Any]] = []
    for timestamp in index:
        state = advance_state(state, timestamp, qqq.loc[timestamp], semi.loc[timestamp], policy)
        weights = apply_jdss_overlay(
            base_weights(state, policy),
            active_tqqq=bool(active_tqqq.loc[timestamp]),
            active_soxl=bool(active_soxl.loc[timestamp]),
            policy=policy,
        )
        rows.append(
            {
                "trade_date": timestamp.date().isoformat(),
                "leverage": state.leverage,
                "semiconductor_active": state.semiconductor_active,
                "jdss_tqqq_active": bool(active_tqqq.loc[timestamp]),
                "jdss_soxl_active": bool(active_soxl.loc[timestamp]),
                **{symbol: float(weights.get(symbol, 0.0)) for symbol in ALLOCATION_SYMBOLS},
            }
        )
    if not rows:
        raise ValueError("V3.2.2 allocation target을 계산할 공통 거래일이 없습니다")
    return pd.DataFrame(rows, index=index)


def hwm_risk_budget(
    high_water_equity: Decimal,
    current_equity: Decimal,
    policy: V322Policy,
) -> Decimal:
    gain = max(Decimal("0"), high_water_equity - policy.initial_capital)
    budget = policy.initial_capital + policy.hwm_reinvestment_fraction * gain
    return max(Decimal("0"), min(budget, current_equity))
