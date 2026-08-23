from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import V322Policy
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.research.ultra_alpha import (
    ULTRA_ALPHA_ID,
    leverage_policy,
    rs63_policy,
    stepped_hwm_75_82_budget,
    ultra_alpha_policy,
    v33_leverage_candidates,
)


@dataclass(frozen=True)
class Variant:
    name: str
    policy: V322Policy
    risk_budget: Callable[[float, float, float], float] | None = None
    profit_reinvestment: str = "HWM75_CONTROLLED"
    onboarding: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent Ultra Alpha validation")
    parser.add_argument("--start", default="2011-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    return parser.parse_args()


def period_metrics(
    result: PortfolioBacktestResult,
    start: str,
    end: str | None = None,
) -> dict[str, float | str]:
    curve = result.equity_curve
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else curve.index[-1]
    selected = curve[(curve.index >= start_ts) & (curve.index <= end_ts)]
    if selected.empty:
        raise ValueError(f"period has no sessions: {start}~{end or 'latest'}")
    prior = curve[curve.index < selected.index[0]]
    initial = float(prior.iloc[-1]) if not prior.empty else float(
        result.metrics["initial_equity"]
    )
    anchor_time = selected.index[0] - pd.Timedelta(nanoseconds=1)
    anchored = pd.concat([pd.Series([initial], index=[anchor_time]), selected])
    years = max((selected.index[-1] - selected.index[0]).days / 365.2425, 1 / 365.2425)
    final = float(selected.iloc[-1])
    cagr = (final / initial) ** (1 / years) - 1
    sharpe, sortino = risk_adjusted_metrics(anchored, 252)
    return {
        "start": selected.index[0].date().isoformat(),
        "end": selected.index[-1].date().isoformat(),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(anchored) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
    }


def run_variant(
    config,
    frames,
    boosters,
    variant: Variant,
    *,
    start: str,
    end: str,
    slippage: float,
) -> PortfolioBacktestResult:
    return PortfolioBacktestEngine(
        config,
        policy=variant.policy,
        risk_budget=variant.risk_budget,
        strategy_version=variant.name,
        profit_reinvestment=variant.profit_reinvestment,
        apply_initial_onboarding=variant.onboarding,
    ).run(frames, boosters, start=start, end=end, slippage=slippage)


def main() -> None:
    args = parse_args()
    config = load_config()
    baseline_policy = V322Policy.from_config(config)
    data = YFinanceDataSource(args.cache_dir)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = (strategy_start - timedelta(days=420)).isoformat()

    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    frames = {
        symbol: data.daily(symbol, warmup_start, args.end)
        for symbol in symbols
    }
    sector_data = {symbol: frames[symbol] for symbol in ("SOXX", "SMH")}

    def build_boosters(slippage: float):
        strategy_engine = StrategyBacktestEngine(config)
        return {
            symbol: strategy_engine.run(
                symbol,
                frames[symbol],
                frames["SPY"],
                frames["QQQ"],
                start=strategy_start,
                end=args.end,
                slippage=slippage,
                sector_data=sector_data if symbol == "SOXL" else None,
            )
            for symbol in config.enabled_symbols
        }

    boosters = build_boosters(0.001)
    portfolio_frames = {
        symbol: frames[symbol] for symbol in ("QQQ", "TQQQ", "SOXL", "SOXX")
    }

    variants = (
        Variant(config.version, baseline_policy),
        Variant("LEVERAGE_028_130_175_ONLY", leverage_policy(baseline_policy)),
        Variant("RS63_SOXL60_ONLY", rs63_policy(baseline_policy)),
        Variant(
            "STEPPED_HWM_75_82_ONLY",
            baseline_policy,
            stepped_hwm_75_82_budget,
            "STEPPED_HWM_75_82",
        ),
        Variant(
            f"{ULTRA_ALPHA_ID}-COMMON-ONBOARDING",
            ultra_alpha_policy(baseline_policy),
            stepped_hwm_75_82_budget,
            "STEPPED_HWM_75_82",
        ),
        Variant(
            ULTRA_ALPHA_ID,
            ultra_alpha_policy(baseline_policy),
            stepped_hwm_75_82_budget,
            "STEPPED_HWM_75_82",
            onboarding=False,
        ),
    )
    results = {
        variant.name: run_variant(
            config,
            portfolio_frames,
            boosters,
            variant,
            start=args.start,
            end=args.end,
            slippage=0.001,
        )
        for variant in variants
    }

    periods = {
        "train_2011_2018": ("2011-01-01", "2018-12-31"),
        "validation_2019_2022": ("2019-01-01", "2022-12-31"),
        "selection_2011_2022": ("2011-01-01", "2022-12-31"),
        "observed_recent_2023_latest": ("2023-01-01", None),
        "recent_stress_2022_latest": ("2022-01-01", None),
    }
    period_results = {
        name: {
            period: period_metrics(result, start, end)
            for period, (start, end) in periods.items()
        }
        for name, result in results.items()
    }

    v33_variants = tuple(
        Variant(candidate.name, candidate.apply(baseline_policy))
        for candidate in v33_leverage_candidates()
    )
    v33_results = {
        variant.name: run_variant(
            config,
            portfolio_frames,
            boosters,
            variant,
            start=args.start,
            end=args.end,
            slippage=0.001,
        )
        for variant in v33_variants
    }
    v33_periods = {
        name: {
            period: period_metrics(result, period_start, period_end)
            for period, (period_start, period_end) in periods.items()
        }
        for name, result in v33_results.items()
    }
    baseline_periods = period_results[config.version]
    train_baseline = baseline_periods["train_2011_2018"]
    validation_baseline = baseline_periods["validation_2019_2022"]
    eligible_names = [
        name
        for name, candidate_periods in v33_periods.items()
        if candidate_periods["train_2011_2018"]["mdd_pct"]
        >= train_baseline["mdd_pct"] - 1.0
        and candidate_periods["validation_2019_2022"]["mdd_pct"]
        >= validation_baseline["mdd_pct"] - 0.5
        and candidate_periods["validation_2019_2022"]["sharpe"]
        >= validation_baseline["sharpe"] - 0.05
        and candidate_periods["validation_2019_2022"]["cagr_pct"]
        >= validation_baseline["cagr_pct"]
    ]
    if not eligible_names:
        raise AssertionError("V3.3 risk gate를 통과한 레버리지 후보가 없습니다")
    selected_name = max(
        eligible_names,
        key=lambda name: v33_periods[name]["selection_2011_2022"]["cagr_pct"],
    )
    selected_variant = next(
        variant for variant in v33_variants if variant.name == selected_name
    )
    ranked_names = sorted(
        v33_results,
        key=lambda name: v33_periods[name]["selection_2011_2022"]["cagr_pct"],
        reverse=True,
    )

    stress: dict[str, dict[str, dict]] = {}
    for slippage in (0.0005, 0.001, 0.002):
        key = f"{slippage:.4f}"
        stress[key] = {}
        stress_boosters = boosters if slippage == 0.001 else build_boosters(slippage)
        for variant in (variants[0], variants[4]):
            run = run_variant(
                config,
                portfolio_frames,
                stress_boosters,
                variant,
                start=args.start,
                end=args.end,
                slippage=slippage,
            )
            stress[key][variant.name] = run.metrics

    v33_stress: dict[str, dict[str, dict]] = {}
    for slippage in (0.0005, 0.001, 0.002):
        key = f"{slippage:.4f}"
        stress_boosters = boosters if slippage == 0.001 else build_boosters(slippage)
        v33_stress[key] = {}
        for variant in (variants[0], selected_variant):
            run = run_variant(
                config,
                portfolio_frames,
                stress_boosters,
                variant,
                start=args.start,
                end=args.end,
                slippage=slippage,
            )
            v33_stress[key][variant.name] = run.metrics

    payload = {
        "data_end": results[config.version].end_date.isoformat(),
        "assumptions": {
            "initial_capital": 50_000,
            "buy_fee": 0.001,
            "sell_fee": 0.001,
            "default_slippage": 0.001,
            "execution": "completed close signal, next-session open",
            "idle_cash_interest": 0.0,
            "volatility_brake_priority": "0.50x overrides SOXL-to-TQQQ exit",
        },
        "variants": {
            name: result.to_dict(include_equity=False)
            for name, result in results.items()
        },
        "periods": period_results,
        "cost_stress": stress,
        "v33_candidate_research": {
            "selection_data": "2011-2022 only",
            "recent_data_policy": "2023-latest observed after selection; not independent OOS",
            "risk_gate": {
                "train_mdd_max_degradation_pct_point": 1.0,
                "validation_mdd_max_degradation_pct_point": 0.5,
                "validation_sharpe_max_degradation": 0.05,
                "validation_cagr_must_not_underperform": True,
            },
            "eligible": eligible_names,
            "selected": selected_name,
            "ranked": ranked_names,
            "variants": {
                name: result.to_dict(include_equity=False)
                for name, result in v33_results.items()
            },
            "periods": v33_periods,
            "cost_stress": v33_stress,
        },
    }
    Path(args.output_json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    baseline_name = config.version
    common_name = f"{ULTRA_ALPHA_ID}-COMMON-ONBOARDING"
    lines = [
        "# JDSS Ultra Alpha 독립 검증",
        "",
        f"- 데이터 종료: `{payload['data_end']}`",
        "- 초기 $50,000, 매수·매도 수수료 각 0.1%, 기본 슬리피지 0.1%",
        "- 완료봉 신호 → 다음 거래일 시가 체결, 현금이자 0%",
        "- 변동성 28% 브레이크가 월중 SOXL 교체보다 우선",
        "",
        "## 전체 기간 및 구조 기여도",
        "",
        "| 후보 | Total Return | CAGR | MDD | Sharpe | Sortino | 평균노출 | 체결 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in variants:
        metrics = results[variant.name].metrics
        lines.append(
            f"| {variant.name} | {metrics['total_return_pct']:+.2f}% | "
            f"{metrics['cagr_pct']:.2f}% | {metrics['mdd_pct']:.2f}% | "
            f"{metrics['sharpe']:.3f} | {metrics['sortino']:.3f} | "
            f"{metrics['average_exposure_pct']:.2f}% | {metrics['trade_fills']} |"
        )
    lines += ["", "## 기간 분리: 현행 vs Ultra Alpha(공통 최초진입)", ""]
    lines += [
        "| 기간 | 전략 | Total Return | CAGR | MDD | Sharpe |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for period in periods:
        for name in (baseline_name, common_name):
            metrics = period_results[name][period]
            lines.append(
                f"| {period} | {name} | {metrics['total_return_pct']:+.2f}% | "
                f"{metrics['cagr_pct']:.2f}% | {metrics['mdd_pct']:.2f}% | "
                f"{metrics['sharpe']:.3f} |"
            )
    lines += ["", "## 비용 민감도: 현행 vs Ultra Alpha(공통 최초진입)", ""]
    lines += [
        "| 슬리피지 | 전략 | Total Return | CAGR | MDD | Sharpe |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for slip, comparison in stress.items():
        for name in (baseline_name, common_name):
            metrics = comparison[name]
            lines.append(
                f"| {float(slip) * 100:.2f}% | {name} | "
                f"{metrics['total_return_pct']:+.2f}% | {metrics['cagr_pct']:.2f}% | "
                f"{metrics['mdd_pct']:.2f}% | {metrics['sharpe']:.3f} |"
            )
    lines += [
        "",
        "## 주장값 대조",
        "",
        "제시된 +1,235.70%, CAGR 18.04%, MDD -26.89%는 위 동일 엔진·동일 데이터 "
        "결과와 직접 대조한다. 차이가 나면 기존 주장값을 재현 성공으로 보지 않는다.",
        "",
    ]
    lines += [
        "## V3.3 레버리지 후보 선별",
        "",
        "- RS126·SOXL50·HWM75·H40-S3 고정",
        "- 선별 데이터: 2011~2022만 사용",
        "- 2023~최신: 이미 관찰된 참고 구간이며 독립 OOS로 사용하지 않음",
        f"- 위험 게이트 통과: {len(eligible_names)}/20",
        f"- 선정 후보: **{selected_name}**",
        "",
        "| 순위 | 후보 | 2011~2022 CAGR | Train MDD | Validation CAGR | "
        "Validation MDD | Validation Sharpe | 최근 참고 CAGR | 전체 CAGR | 전체 MDD |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, name in enumerate(ranked_names, start=1):
        candidate_periods = v33_periods[name]
        train = candidate_periods["train_2011_2018"]
        validation = candidate_periods["validation_2019_2022"]
        selection = candidate_periods["selection_2011_2022"]
        recent = candidate_periods["observed_recent_2023_latest"]
        full = v33_results[name].metrics
        marker = " ✅" if name == selected_name else ""
        lines.append(
            f"| {rank} | {name}{marker} | {selection['cagr_pct']:.2f}% | "
            f"{train['mdd_pct']:.2f}% | {validation['cagr_pct']:.2f}% | "
            f"{validation['mdd_pct']:.2f}% | {validation['sharpe']:.3f} | "
            f"{recent['cagr_pct']:.2f}% | {full['cagr_pct']:.2f}% | "
            f"{full['mdd_pct']:.2f}% |"
        )
    lines += [
        "",
        "### 선정 후보 비용 민감도",
        "",
        "| 슬리피지 | 전략 | Total Return | CAGR | MDD | Sharpe |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for slip, comparison in v33_stress.items():
        for name in (baseline_name, selected_name):
            metrics = comparison[name]
            lines.append(
                f"| {float(slip) * 100:.2f}% | {name} | "
                f"{metrics['total_return_pct']:+.2f}% | {metrics['cagr_pct']:.2f}% | "
                f"{metrics['mdd_pct']:.2f}% | {metrics['sharpe']:.3f} |"
            )
    lines.append("")
    Path(args.output_md).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
