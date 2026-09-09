from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from jd_holdings.config import StrategyConfig
from jd_holdings.core.initial_onboarding import InitialOnboardingPolicy
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    V322Policy,
    replay_targets,
    virtual_active_series,
)

from .engine import BacktestResult
from .performance import maximum_drawdown, risk_adjusted_metrics


@dataclass(frozen=True)
class PortfolioBacktestResult:
    start_date: date
    end_date: date
    strategy_version: str
    config_version: str
    slippage: float
    metrics: dict[str, Any]
    trades: tuple[dict[str, Any], ...]
    equity_curve: pd.Series = field(repr=False, compare=False)

    def to_dict(self, *, include_equity: bool = False) -> dict[str, Any]:
        result = {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "strategy_version": self.strategy_version,
            "config_version": self.config_version,
            "slippage": self.slippage,
            "metrics": self.metrics,
            "trades": list(self.trades),
        }
        if include_equity:
            result["equity_curve"] = {
                timestamp.date().isoformat(): round(float(value), 2)
                for timestamp, value in self.equity_curve.items()
            }
        return result


class PortfolioBacktestEngine:
    """Production-equivalent V3.2.2 shared-account simulation."""

    def __init__(
        self,
        config: StrategyConfig,
        *,
        policy: V322Policy | None = None,
        risk_budget: Callable[[float, float, float], float] | None = None,
        strategy_version: str | None = None,
        profit_reinvestment: str = "HWM75_CONTROLLED",
        apply_initial_onboarding: bool = True,
    ) -> None:
        if not config.portfolio.enabled:
            raise ValueError("포트폴리오 백테스트는 portfolio.enabled가 필요합니다")
        self.config = config
        self.policy = policy or V322Policy.from_config(config)
        self.onboarding_policy = InitialOnboardingPolicy.from_config(config)
        self.risk_budget = risk_budget or self._hwm75_risk_budget
        self.strategy_version = strategy_version or config.version
        self.profit_reinvestment = profit_reinvestment
        self.apply_initial_onboarding = apply_initial_onboarding

    def _hwm75_risk_budget(
        self, initial_capital: float, high_water: float, current_equity: float
    ) -> float:
        budget = initial_capital + float(self.policy.hwm_reinvestment_fraction) * max(
            0.0, high_water - initial_capital
        )
        return max(0.0, min(budget, current_equity))

    def run(
        self,
        frames: dict[str, pd.DataFrame],
        booster_results: dict[str, BacktestResult],
        *,
        start: str | date,
        end: str | date,
        slippage: float | None = None,
    ) -> PortfolioBacktestResult:
        required = {*ALLOCATION_SYMBOLS, self.policy.rs_benchmark}
        missing = required - set(frames)
        if missing:
            raise ValueError("V3.2.2 포트폴리오 데이터 누락: " + ", ".join(sorted(missing)))
        missing_boosters = set(self.config.enabled_symbols) - set(booster_results)
        if missing_boosters:
            raise ValueError(
                "V3.2.2 JDSS overlay 결과 누락: " + ", ".join(sorted(missing_boosters))
            )

        index: pd.DatetimeIndex | None = None
        for symbol in required:
            index = (
                frames[symbol].index
                if index is None
                else index.intersection(frames[symbol].index)
            )
        if index is None or len(index) < 2:
            raise ValueError("V3.2.2 공통 거래일이 부족합니다")

        active = {
            symbol: virtual_active_series(booster_results[symbol], index)
            for symbol in self.config.enabled_symbols
        }
        targets = replay_targets(
            frames["QQQ"].reindex(index),
            frames[self.policy.rs_benchmark].reindex(index),
            active["TQQQ"],
            active["SOXL"],
            self.policy,
        )

        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        sessions = index[(index >= start_ts) & (index <= end_ts)]
        if len(sessions) < 2:
            raise ValueError("포트폴리오 백테스트 기간이 너무 짧습니다")
        prior = index[index < sessions[0]]
        if prior.empty:
            raise ValueError("V3.2.2 시작일 이전 warmup 거래일이 필요합니다")
        prior_ts = prior[-1]
        if prior_ts not in targets.index:
            raise ValueError("V3.2.2 시작일 이전 target을 계산하지 못했습니다")

        slip = float(
            self.config.backtest.default_slippage if slippage is None else slippage
        )
        buy_fee = float(self.config.global_.buy_fee)
        sell_fee = float(self.config.global_.sell_fee)
        initial_capital = float(self.policy.initial_capital)
        cash = initial_capital
        high_water = initial_capital
        quantities = {symbol: 0 for symbol in ALLOCATION_SYMBOLS}
        pending = self._apply_initial_onboarding(
            self._weights_from_target(targets.loc[prior_ts]),
            execution_index=0,
        )
        current: dict[str, float] | None = None
        trades: list[dict[str, Any]] = []
        equity_values: list[float] = []
        exposures: list[float] = []
        sizing_history: list[float] = []

        for execution_index, timestamp in enumerate(sessions):
            opens = {
                symbol: float(frames[symbol].loc[timestamp, "open"])
                for symbol in ALLOCATION_SYMBOLS
            }
            closes = {
                symbol: float(frames[symbol].loc[timestamp, "close"])
                for symbol in ALLOCATION_SYMBOLS
            }
            open_equity = cash + sum(
                quantities[symbol] * opens[symbol] * (1 - sell_fee)
                for symbol in ALLOCATION_SYMBOLS
            )
            sizing_equity = self.risk_budget(
                initial_capital, high_water, open_equity
            )

            if pending != current:
                onboarding_stage = None
                if self.onboarding_policy.enabled:
                    interval = self.onboarding_policy.minimum_sessions_between_stages
                    if interval == 0 and execution_index == 0:
                        onboarding_stage = self.onboarding_policy.total_stages
                    elif interval > 0 and execution_index <= interval * (
                        self.onboarding_policy.total_stages - 1
                    ):
                        onboarding_stage = min(
                            execution_index // interval + 1,
                            self.onboarding_policy.total_stages,
                        )
                cash = self._rebalance(
                    pending,
                    quantities,
                    opens,
                    cash,
                    sizing_equity=sizing_equity,
                    timestamp=timestamp,
                    buy_fee=buy_fee,
                    sell_fee=sell_fee,
                    slippage=slip,
                    trades=trades,
                    onboarding_stage=onboarding_stage,
                )
                current = pending

            liquidation = sum(
                quantities[symbol] * closes[symbol] * (1 - sell_fee)
                for symbol in ALLOCATION_SYMBOLS
            )
            equity = cash + liquidation
            equity_values.append(equity)
            exposures.append(liquidation / equity if equity > 0 else 0.0)
            sizing_history.append(sizing_equity)
            high_water = max(high_water, equity)
            pending = self._apply_initial_onboarding(
                self._weights_from_target(targets.loc[timestamp]),
                execution_index=execution_index + 1,
            )

        equity_curve = pd.Series(equity_values, index=sessions)
        metrics = self._metrics(
            equity_curve,
            exposures,
            trades,
            sizing_history,
            targets.loc[sessions],
            cash=cash,
            quantities=quantities,
            final_prices={
                symbol: float(frames[symbol].loc[sessions[-1], "close"])
                for symbol in ALLOCATION_SYMBOLS
            },
            sell_fee=sell_fee,
        )
        return PortfolioBacktestResult(
            sessions[0].date(),
            sessions[-1].date(),
            self.strategy_version,
            self.config.config_version,
            slip,
            metrics,
            tuple(trades),
            equity_curve,
        )

    @staticmethod
    def _weights_from_target(row: pd.Series) -> dict[str, float]:
        return {
            symbol: float(row[symbol])
            for symbol in ALLOCATION_SYMBOLS
            if float(row[symbol]) > 1e-12
        }

    def _apply_initial_onboarding(
        self,
        target: dict[str, float],
        *,
        execution_index: int,
    ) -> dict[str, float]:
        """Cap risk-increasing targets at 50/75/100% from the report start.

        The production account requires an operator to open each stage after
        the prior stage is filled.  A deterministic backtest assumes fills are
        accepted and opens the next stage after the configured minimum number
        of US sessions.  Target reductions still flow through immediately.
        """
        policy = self.onboarding_policy
        if not self.apply_initial_onboarding or not policy.enabled:
            return target
        interval = policy.minimum_sessions_between_stages
        if interval == 0:
            stage = policy.total_stages
        else:
            stage = min(execution_index // interval + 1, policy.total_stages)
        fraction = float(policy.fraction_for_stage(stage))
        return {symbol: weight * fraction for symbol, weight in target.items()}

    @staticmethod
    def _rebalance(
        target: dict[str, float],
        quantities: dict[str, int],
        prices: dict[str, float],
        cash: float,
        *,
        sizing_equity: float,
        timestamp: pd.Timestamp,
        buy_fee: float,
        sell_fee: float,
        slippage: float,
        trades: list[dict[str, Any]],
        onboarding_stage: int | None = None,
    ) -> float:
        desired: dict[str, int] = {}
        for symbol in ALLOCATION_SYMBOLS:
            buy_price = prices[symbol] * (1 + slippage)
            desired[symbol] = math.floor(
                sizing_equity * target.get(symbol, 0.0) / (buy_price * (1 + buy_fee))
            )

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference >= 0:
                continue
            quantity = -difference
            price = prices[symbol] * (1 - slippage)
            fee = quantity * price * sell_fee
            cash += quantity * price - fee
            quantities[symbol] -= quantity
            trades.append(
                PortfolioBacktestEngine._trade(
                    timestamp, symbol, "SELL", quantity, price, fee,
                    onboarding_stage=onboarding_stage,
                )
            )

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference <= 0:
                continue
            price = prices[symbol] * (1 + slippage)
            affordable = math.floor(cash / (price * (1 + buy_fee)))
            quantity = min(difference, affordable)
            if quantity <= 0:
                continue
            fee = quantity * price * buy_fee
            cash -= quantity * price + fee
            quantities[symbol] += quantity
            trades.append(
                PortfolioBacktestEngine._trade(
                    timestamp, symbol, "BUY", quantity, price, fee,
                    onboarding_stage=onboarding_stage,
                )
            )
        return cash

    @staticmethod
    def _trade(
        timestamp, symbol, side, quantity, price, fee, *, onboarding_stage=None
    ) -> dict[str, Any]:
        purpose = (
            f"INITIAL_ENTRY_STAGE_{onboarding_stage}_{side}"
            if onboarding_stage is not None
            else f"V322_REBALANCE_{side}"
        )
        return {
            "date": timestamp.date().isoformat(),
            "component": "allocation",
            "symbol": symbol,
            "side": side,
            "purpose": purpose,
            "quantity": quantity,
            "price": round(price, 6),
            "fee": round(fee, 6),
        }

    @staticmethod
    def _calendar_returns(equity: pd.Series, initial: float) -> dict[str, float]:
        year_ends = equity.groupby(equity.index.year).last()
        previous = initial
        result: dict[str, float] = {}
        for year, value in year_ends.items():
            result[str(year)] = round((float(value) / previous - 1) * 100, 2)
            previous = float(value)
        return result

    @staticmethod
    def _half_year_returns(equity: pd.Series, initial: float) -> dict[str, float]:
        labels = pd.Index(
            [f"{ts.year}-H{1 if ts.month <= 6 else 2}" for ts in equity.index]
        )
        ends = equity.groupby(labels).last()
        previous = initial
        result: dict[str, float] = {}
        for label, value in ends.items():
            result[str(label)] = round((float(value) / previous - 1) * 100, 2)
            previous = float(value)
        return result

    def _metrics(
        self,
        equity: pd.Series,
        exposures: list[float],
        trades: list[dict[str, Any]],
        sizing_history: list[float],
        targets: pd.DataFrame,
        *,
        cash: float,
        quantities: dict[str, int],
        final_prices: dict[str, float],
        sell_fee: float,
    ) -> dict[str, Any]:
        initial = float(self.policy.initial_capital)
        final = float(equity.iloc[-1])
        years = max(
            (equity.index[-1] - equity.index[0]).days / 365.2425,
            1 / 365.2425,
        )
        mdd = maximum_drawdown(equity)
        sharpe, sortino = risk_adjusted_metrics(
            equity, self.config.backtest.annualization_days
        )
        cagr = (final / initial) ** (1 / years) - 1
        holdings = {
            symbol: {
                "quantity": quantities[symbol],
                "price": round(final_prices[symbol], 6),
                "market_value": round(
                    quantities[symbol] * final_prices[symbol] * (1 - sell_fee), 2
                ),
            }
            for symbol in ALLOCATION_SYMBOLS
        }
        holdings_value = sum(item["market_value"] for item in holdings.values())
        total_fees = sum(float(trade["fee"]) for trade in trades)
        return {
            "initial_equity": round(initial, 2),
            "final_equity": round(final, 2),
            "total_return_pct": round((final / initial - 1) * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "mdd_pct": round(mdd * 100, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "calmar": round(cagr / abs(mdd), 3) if mdd else 0.0,
            "trade_fills": len(trades),
            "average_exposure_pct": round(sum(exposures) / len(exposures) * 100, 2),
            "final_cash": round(cash, 2),
            "final_holdings_value": round(holdings_value, 2),
            "final_holdings": holdings,
            "total_fees": round(total_fees, 2),
            "net_profit": round(final - initial, 2),
            "annual_returns_pct": self._calendar_returns(equity, initial),
            "half_year_returns_pct": self._half_year_returns(equity, initial),
            "component_fills": {"allocation": len(trades)},
            "initial_risk_budget": round(initial, 2),
            "maximum_sizing_base": round(max(sizing_history), 2),
            "final_sizing_base": round(sizing_history[-1], 2),
            "hwm_reinvestment_fraction": float(self.policy.hwm_reinvestment_fraction),
            "profit_reinvestment": self.profit_reinvestment,
            "idle_cash_enabled": False,
            "idle_cash_income": 0.0,
            "initial_onboarding_enabled": (
                self.onboarding_policy.enabled and self.apply_initial_onboarding
            ),
            "initial_onboarding_fractions_pct": [
                float(fraction * 100)
                for fraction in self.onboarding_policy.cumulative_fractions
            ],
            "initial_onboarding_minimum_sessions": (
                self.onboarding_policy.minimum_sessions_between_stages
            ),
            "average_target_leverage": round(float(targets["leverage"].mean()), 4),
            "semiconductor_active_days_pct": round(
                float(targets["semiconductor_active"].mean()) * 100, 2
            ),
        }
