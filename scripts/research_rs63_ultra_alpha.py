#!/usr/bin/env python3
"""Reproduce and stress-test the proposed RS63 and Ultra Alpha scenarios.

This is a research-only harness.  It reuses the production strategy and
portfolio engines, changes allocation policy objects only after the frozen
V3.2.2 baseline has been instantiated, and never writes production config.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

from jd_holdings.backtest.performance import maximum_underwater_days
from jd_holdings.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.v322_allocation import V322Policy
from jd_holdings.infrastructure.market_data import YFinanceDataSource


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    policy_changes: dict[str, float | int]
    hwm_mode: str = "HWM75"


class FlatStepHWMEngine(PortfolioBacktestEngine):
    """Use 75% below $100k HWM and 82% on the entire gain afterwards."""

    def _sizing_equity(self, *, initial_capital, high_water, open_equity):
        rate = 0.82 if high_water >= 100_000 else 0.75
        sizing = initial_capital + rate * max(0.0, high_water - initial_capital)
        return max(0.0, min(sizing, open_equity))


class MarginalStepHWMEngine(PortfolioBacktestEngine):
    """Use 75% on the first $50k gain and 82% only above $100k HWM."""

    def _sizing_equity(self, *, initial_capital, high_water, open_equity):
        gain = max(0.0, high_water - initial_capital)
        first_band = min(gain, 50_000.0)
        upper_band = max(0.0, gain - 50_000.0)
        sizing = initial_capital + 0.75 * first_band + 0.82 * upper_band
        return max(0.0, min(sizing, open_equity))


SCENARIOS = (
    Scenario("BASELINE_126D", "현재 V3.2.2", {}),
    Scenario("S4_RS63", "시나리오 4: RS 63일만 변경", {"rs_lookback": 63}),
    Scenario(
        "S4_PLUS_LEVERAGE",
        "RS63 + 추세/강세 노출 1.30/1.75",
        {"rs_lookback": 63, "leverage_trend": 1.30, "leverage_strong": 1.75},
    ),
    Scenario(
        "S4_PLUS_LEV_SOXL60",
        "RS63 + 1.30/1.75 + SOXL 60%",
        {
            "rs_lookback": 63,
            "leverage_trend": 1.30,
            "leverage_strong": 1.75,
            "rs_sleeve_fraction": 0.60,
        },
    ),
    Scenario(
        "S8_NO_HWM_STEP",
        "시나리오 8 파라미터, HWM75 유지",
        {
            "rs_lookback": 63,
            "leverage_trend": 1.30,
            "leverage_strong": 1.75,
            "rs_sleeve_fraction": 0.60,
            "volatility_brake": 0.28,
        },
    ),
    Scenario(
        "S8_FLAT_STEP_82",
        "시나리오 8, $100k 이후 전체 이익 82%",
        {
            "rs_lookback": 63,
            "leverage_trend": 1.30,
            "leverage_strong": 1.75,
            "rs_sleeve_fraction": 0.60,
            "volatility_brake": 0.28,
        },
        "FLAT_STEP_75_82",
    ),
    Scenario(
        "S8_MARGINAL_STEP_82",
        "시나리오 8, $100k 초과이익만 82%",
        {
            "rs_lookback": 63,
            "leverage_trend": 1.30,
            "leverage_strong": 1.75,
            "rs_sleeve_fraction": 0.60,
            "volatility_brake": 0.28,
        },
        "MARGINAL_STEP_75_82",
    ),
)

PERIODS = {
    "TRAIN_2011_2018": ("2011-01-01", "2018-12-31"),
    "VALIDATION_2019_2022": ("2019-01-01", "2022-12-31"),
    "OOS_2023_2026": ("2023-01-01", "2026-08-21"),
    "RECENT_STRESS_2022_2026": ("2022-01-01", "2026-08-21"),
    "FULL_2011_2026": ("2011-01-01", "2026-08-21"),
}


def _engine_for(config: StrategyConfig, scenario: Scenario) -> PortfolioBacktestEngine:
    cls: type[PortfolioBacktestEngine]
    if scenario.hwm_mode == "FLAT_STEP_75_82":
        cls = FlatStepHWMEngine
    elif scenario.hwm_mode == "MARGINAL_STEP_75_82":
        cls = MarginalStepHWMEngine
    else:
        cls = PortfolioBacktestEngine
    engine = cls(config)
    baseline = engine.policy
    engine.policy = dataclasses.replace(baseline, **scenario.policy_changes)
    for key, expected in scenario.policy_changes.items():
        actual = getattr(engine.policy, key)
        if actual != expected:
            raise AssertionError(f"override mismatch {scenario.name} {key}: {actual} != {expected}")
    return engine


def _drawdown_details(equity: pd.Series) -> dict[str, object]:
    running = equity.cummax()
    drawdown = equity / running - 1.0
    trough = drawdown.idxmin()
    peak = equity.loc[:trough].idxmax()
    peak_value = float(equity.loc[peak])
    after = equity.loc[trough:]
    recovered = after[after >= peak_value]
    recovery = None if recovered.empty else recovered.index[0]
    return {
        "peak_date": peak.date().isoformat(),
        "trough_date": trough.date().isoformat(),
        "recovery_date": recovery.date().isoformat() if recovery is not None else None,
        "max_underwater_sessions": maximum_underwater_days(equity),
    }


def _metrics(result: PortfolioBacktestResult) -> dict[str, object]:
    metrics = dict(result.metrics)
    metrics.update(_drawdown_details(result.equity_curve))
    metrics["turnover_notional"] = round(
        sum(float(t["quantity"]) * float(t["price"]) for t in result.trades), 2
    )
    return metrics


def _load_frames(source: YFinanceDataSource, end: str) -> dict[str, pd.DataFrame]:
    warmup_start = (date.fromisoformat("2011-01-01") - timedelta(days=420)).isoformat()
    return {
        symbol: source.daily(symbol, warmup_start, end)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    }


def _virtual_results(
    config: StrategyConfig,
    frames: dict[str, pd.DataFrame],
    *,
    end: str,
    slippage: float,
):
    engine = StrategyBacktestEngine(config)
    sector = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
    return {
        symbol: engine.run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start="2011-01-01",
            end=end,
            slippage=slippage,
            sector_data=sector if symbol == "SOXL" else None,
            idle_cash_data=None,
        )
        for symbol in config.enabled_symbols
    }


def _run_matrix(config: StrategyConfig, frames, base_slippage: float):
    rows: list[dict[str, object]] = []
    virtual_cache = {}

    def virtual(end: str, slip: float):
        key = (end, slip)
        if key not in virtual_cache:
            virtual_cache[key] = _virtual_results(
                config, frames, end=end, slippage=slip
            )
        return virtual_cache[key]

    for period, (start, end) in PERIODS.items():
        boosters = virtual(end, base_slippage)
        for scenario in SCENARIOS:
            result = _engine_for(config, scenario).run(
                frames,
                boosters,
                start=start,
                end=end,
                slippage=base_slippage,
            )
            rows.append(
                {
                    "period": period,
                    "slippage": base_slippage,
                    "scenario": scenario.name,
                    "description": scenario.description,
                    "hwm_mode": scenario.hwm_mode,
                    "metrics": _metrics(result),
                }
            )

    full_start, full_end = PERIODS["FULL_2011_2026"]
    for slip in (0.0005, 0.0020):
        boosters = virtual(full_end, slip)
        for scenario in SCENARIOS:
            result = _engine_for(config, scenario).run(
                frames, boosters, start=full_start, end=full_end, slippage=slip
            )
            rows.append(
                {
                    "period": "FULL_COST_STRESS",
                    "slippage": slip,
                    "scenario": scenario.name,
                    "description": scenario.description,
                    "hwm_mode": scenario.hwm_mode,
                    "metrics": _metrics(result),
                }
            )
    return rows


def _assert_baseline(rows, base_slippage: float) -> None:
    match = next(
        row
        for row in rows
        if row["period"] == "FULL_2011_2026"
        and row["scenario"] == "BASELINE_126D"
        and row["slippage"] == base_slippage
    )
    metrics = match["metrics"]
    assert 22.0 <= metrics["cagr_pct"] <= 22.8, metrics
    assert -31.6 <= metrics["mdd_pct"] <= -30.3, metrics
    assert 0.94 <= metrics["sharpe"] <= 1.07, metrics


def _fmt(value, suffix="%"):
    return f"{float(value):+.2f}{suffix}"


def _markdown(rows, base_slippage: float) -> str:
    lines = [
        "# RS63 / Ultra Alpha 재현 검증",
        "",
        "- production StrategyBacktestEngine + PortfolioBacktestEngine 공유",
        "- 초기 $50,000, 수수료 매수/매도 각 0.1%, 최초진입 50→75→100/3거래일",
        f"- 기본 슬리피지 {base_slippage * 100:.2f}%",
        "- OOS 2023+는 이미 후보 설명에 사용됐으므로 독립 OOS가 아닌 shadow 근거",
        "- Stepped HWM 문구가 모호하여 전체이익 82%와 초과이익만 82%를 모두 검증",
    ]
    for period in PERIODS:
        selected = [
            row
            for row in rows
            if row["period"] == period and row["slippage"] == base_slippage
        ]
        lines += [
            "",
            f"## {period}",
            "",
            "| 시나리오 | Total | CAGR | MDD | Sharpe | Sortino | 최종자산 | 평균노출 | 체결 | 수수료 | 최악구간 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in selected:
            m = row["metrics"]
            lines.append(
                f"| {row['scenario']} | {_fmt(m['total_return_pct'])} | "
                f"{_fmt(m['cagr_pct'])} | {_fmt(m['mdd_pct'])} | "
                f"{m['sharpe']:.3f} | {m['sortino']:.3f} | "
                f"${m['final_equity']:,.0f} | {m['average_exposure_pct']:.2f}% | "
                f"{m['component_fills']['allocation']} | ${m['total_fees']:,.0f} | "
                f"{m['peak_date']}→{m['trough_date']} |"
            )
    lines += [
        "",
        "## FULL 비용 스트레스",
        "",
        "| 슬리피지 | 시나리오 | CAGR | MDD | Sharpe | 최종자산 | 수수료 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["period"] != "FULL_COST_STRESS":
            continue
        m = row["metrics"]
        lines.append(
            f"| {row['slippage'] * 100:.2f}% | {row['scenario']} | "
            f"{_fmt(m['cagr_pct'])} | {_fmt(m['mdd_pct'])} | {m['sharpe']:.3f} | "
            f"${m['final_equity']:,.0f} | ${m['total_fees']:,.0f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    config = load_config("strategy.yaml")
    baseline = V322Policy.from_config(config)
    assert baseline.rs_lookback == 126
    assert baseline.leverage_strong == 1.50
    assert baseline.volatility_brake == 0.30
    source = YFinanceDataSource(Path(".cache") / "rs63-ultra-alpha")
    frames = _load_frames(source, args.end)
    rows = _run_matrix(config, frames, args.slippage)
    _assert_baseline(rows, args.slippage)

    payload = {
        "baseline_strategy": config.version,
        "end": args.end,
        "base_slippage": args.slippage,
        "periods": PERIODS,
        "scenarios": [dataclasses.asdict(item) for item in SCENARIOS],
        "results": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    args.output_md.write_text(_markdown(rows, args.slippage), encoding="utf-8")
    print(args.output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
