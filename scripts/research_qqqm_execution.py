from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.indicators import MarketDataError
from jd_holdings.core.initial_onboarding import InitialOnboardingPolicy
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    V322Policy,
    replay_targets,
    virtual_active_series,
)
from jd_holdings.infrastructure.market_data import YFinanceDataSource

QQQM_INCEPTION = "2020-10-13"
DEFAULT_END = "2026-09-02"


@dataclass(frozen=True)
class Variant:
    name: str
    core_frame: pd.DataFrame
    execution_symbol: str
    description: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare QQQ vs QQQM execution while keeping all JDSS signals on QQQ."
    )
    parser.add_argument("--start", default=QQQM_INCEPTION)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-json", default="reports/qqqm-execution.json")
    parser.add_argument("--output-md", default="reports/qqqm-execution.md")
    return parser.parse_args()


def _weights_from_target(row: pd.Series) -> dict[str, float]:
    return {
        symbol: float(row[symbol])
        for symbol in ALLOCATION_SYMBOLS
        if float(row[symbol]) > 1e-12
    }


def _onboard(
    target: dict[str, float],
    execution_index: int,
    policy: InitialOnboardingPolicy,
) -> dict[str, float]:
    if not policy.enabled:
        return target
    interval = policy.minimum_sessions_between_stages
    if interval == 0:
        stage = policy.total_stages
    else:
        stage = min(execution_index // interval + 1, policy.total_stages)
    fraction = float(policy.fraction_for_stage(stage))
    return {symbol: weight * fraction for symbol, weight in target.items()}


def _prepare_history(start: date, end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    onboarding = InitialOnboardingPolicy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "qqqm-execution-research")

    required = ("SPY", "QQQ", "QQQM", "TQQQ", "SOXL", policy.rs_benchmark)
    raw: dict[str, pd.DataFrame] = {
        symbol: source.daily(symbol, warmup_start, end, refresh=False)
        for symbol in required
    }
    try:
        raw["SMH"] = source.daily("SMH", warmup_start, end, refresh=False)
    except MarketDataError as exc:
        print(f"WARNING optional SMH unavailable: {exc}")

    sector_data = {
        symbol: raw[symbol]
        for symbol in (policy.rs_benchmark, "SMH")
        if symbol in raw
    }
    engine = StrategyBacktestEngine(config)
    virtual = {
        symbol: engine.run(
            symbol,
            raw[symbol],
            raw["SPY"],
            raw["QQQ"],
            start=strategy_start,
            end=end,
            slippage=float(config.backtest.default_slippage),
            sector_data=sector_data if symbol == "SOXL" else None,
        )
        for symbol in config.enabled_symbols
    }

    common = raw["QQQ"].index
    for symbol in ("TQQQ", "SOXL", policy.rs_benchmark):
        common = common.intersection(raw[symbol].index)
    active = {
        symbol: virtual_active_series(virtual[symbol], common)
        for symbol in config.enabled_symbols
    }
    targets = replay_targets(
        raw["QQQ"].reindex(common),
        raw[policy.rs_benchmark].reindex(common),
        active["TQQQ"],
        active["SOXL"],
        policy,
    )

    qqqm_common = raw["QQQ"].index.intersection(raw["QQQM"].index)
    qqqm_common = qqqm_common[
        (qqqm_common >= pd.Timestamp(start)) & (qqqm_common <= pd.Timestamp(end))
    ]
    if qqqm_common.empty:
        raise ValueError("QQQM common history is empty")
    anchor = qqqm_common[0]
    scale = float(raw["QQQM"].loc[anchor, "close"]) / float(
        raw["QQQ"].loc[anchor, "close"]
    )
    synthetic = raw["QQQ"].copy()
    for column in ("open", "high", "low", "close"):
        synthetic[column] = synthetic[column].astype(float) * scale

    variants = (
        Variant(
            "QQQ_CONTROL",
            raw["QQQ"],
            "QQQ",
            "현재 방식: QQQ 신호 + QQQ 실제 체결",
        ),
        Variant(
            "QQQ_SCALED_TO_QQQM_PRICE",
            synthetic,
            "QQQ_SYNTH",
            "QQQ 수익률 경로를 QQQM 출시일 가격대로 축소한 순수 정수주 효과 통제군",
        ),
        Variant(
            "QQQM_EXECUTION",
            raw["QQQM"],
            "QQQM",
            "제안 방식: 모든 신호는 QQQ, 코어 실제 체결만 QQQM",
        ),
    )
    return config, policy, onboarding, raw, targets, variants, anchor, scale


def _simulate(
    variant: Variant,
    *,
    config,
    policy: V322Policy,
    onboarding: InitialOnboardingPolicy,
    raw: dict[str, pd.DataFrame],
    targets: pd.DataFrame,
    start: date,
    end: date,
) -> dict[str, object]:
    execution_frames = {
        "QQQ": variant.core_frame,
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
    }

    index = targets.index
    for frame in execution_frames.values():
        index = index.intersection(frame.index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    if len(sessions) < 2:
        raise ValueError(f"{variant.name}: insufficient sessions")
    prior = targets.index[targets.index < sessions[0]]
    if prior.empty:
        raise ValueError(f"{variant.name}: prior target unavailable")
    prior_ts = prior[-1]

    slip = float(config.backtest.default_slippage)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    initial = float(policy.initial_capital)
    cash = initial
    high_water = initial
    quantities = {symbol: 0 for symbol in ALLOCATION_SYMBOLS}
    pending = _onboard(_weights_from_target(targets.loc[prior_ts]), 0, onboarding)
    current: dict[str, float] | None = None

    equity_values: list[float] = []
    exposures: list[float] = []
    trades: list[dict[str, object]] = []
    rebalance_rows: list[dict[str, float | str]] = []
    sizing_history: list[float] = []

    for execution_index, timestamp in enumerate(sessions):
        opens = {
            symbol: float(execution_frames[symbol].loc[timestamp, "open"])
            for symbol in ALLOCATION_SYMBOLS
        }
        closes = {
            symbol: float(execution_frames[symbol].loc[timestamp, "close"])
            for symbol in ALLOCATION_SYMBOLS
        }
        open_equity = cash + sum(
            quantities[symbol] * opens[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        sizing_equity = initial + float(policy.hwm_reinvestment_fraction) * max(
            0.0, high_water - initial
        )
        sizing_equity = max(0.0, min(sizing_equity, open_equity))
        sizing_history.append(sizing_equity)

        if pending != current:
            desired: dict[str, int] = {}
            target_cost: dict[str, float] = {}
            quantization_gap: dict[str, float] = {}
            for symbol in ALLOCATION_SYMBOLS:
                buy_price = opens[symbol] * (1 + slip)
                unit_cost = buy_price * (1 + buy_fee)
                target_cost[symbol] = sizing_equity * pending.get(symbol, 0.0)
                desired[symbol] = math.floor(target_cost[symbol] / unit_cost)
                quantization_gap[symbol] = max(
                    0.0, target_cost[symbol] - desired[symbol] * unit_cost
                )

            for symbol in ALLOCATION_SYMBOLS:
                difference = desired[symbol] - quantities[symbol]
                if difference >= 0:
                    continue
                quantity = -difference
                price = opens[symbol] * (1 - slip)
                fee = quantity * price * sell_fee
                cash += quantity * price - fee
                quantities[symbol] -= quantity
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "logical_symbol": symbol,
                        "execution_symbol": (
                            variant.execution_symbol if symbol == "QQQ" else symbol
                        ),
                        "side": "SELL",
                        "quantity": quantity,
                        "price": price,
                        "fee": fee,
                    }
                )

            for symbol in ALLOCATION_SYMBOLS:
                difference = desired[symbol] - quantities[symbol]
                if difference <= 0:
                    continue
                price = opens[symbol] * (1 + slip)
                unit_cost = price * (1 + buy_fee)
                affordable = math.floor(cash / unit_cost)
                quantity = min(difference, affordable)
                if quantity <= 0:
                    continue
                fee = quantity * price * buy_fee
                cash -= quantity * price + fee
                quantities[symbol] += quantity
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "logical_symbol": symbol,
                        "execution_symbol": (
                            variant.execution_symbol if symbol == "QQQ" else symbol
                        ),
                        "side": "BUY",
                        "quantity": quantity,
                        "price": price,
                        "fee": fee,
                    }
                )

            core_gap = quantization_gap["QQQ"]
            total_gap = sum(quantization_gap.values())
            core_weight_error_bps = (
                core_gap / sizing_equity * 10000 if sizing_equity > 0 else 0.0
            )
            total_weight_error_bps = (
                total_gap / sizing_equity * 10000 if sizing_equity > 0 else 0.0
            )
            rebalance_rows.append(
                {
                    "date": timestamp.date().isoformat(),
                    "sizing_equity": sizing_equity,
                    "core_target_weight": pending.get("QQQ", 0.0),
                    "core_price": opens["QQQ"],
                    "core_target_usd": target_cost["QQQ"],
                    "core_rounding_gap_usd": core_gap,
                    "total_rounding_gap_usd": total_gap,
                    "core_weight_error_bps": core_weight_error_bps,
                    "total_weight_error_bps": total_weight_error_bps,
                    "post_rebalance_cash": cash,
                }
            )
            current = pending

        liquidation = sum(
            quantities[symbol] * closes[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposures.append(liquidation / equity if equity > 0 else 0.0)
        high_water = max(high_water, equity)
        pending = _onboard(
            _weights_from_target(targets.loc[timestamp]),
            execution_index + 1,
            onboarding,
        )

    equity = pd.Series(equity_values, index=sessions)
    final = float(equity.iloc[-1])
    years = max((sessions[-1] - sessions[0]).days / 365.2425, 1 / 365.2425)
    cagr = (final / initial) ** (1 / years) - 1
    mdd = maximum_drawdown(equity)
    sharpe, sortino = risk_adjusted_metrics(equity, config.backtest.annualization_days)
    total_fees = sum(float(item["fee"]) for item in trades)

    core_gaps = [float(row["core_rounding_gap_usd"]) for row in rebalance_rows]
    total_gaps = [float(row["total_rounding_gap_usd"]) for row in rebalance_rows]
    core_bps = [float(row["core_weight_error_bps"]) for row in rebalance_rows]
    total_bps = [float(row["total_weight_error_bps"]) for row in rebalance_rows]
    post_cash = [float(row["post_rebalance_cash"]) for row in rebalance_rows]
    core_prices = [float(row["core_price"]) for row in rebalance_rows]

    core_name = variant.execution_symbol if variant.execution_symbol != "QQQ_SYNTH" else "QQQ_SYNTH"
    final_holdings = {
        (core_name if symbol == "QQQ" else symbol): {
            "quantity": quantities[symbol],
            "price": round(closes[symbol], 6),
            "market_value": round(
                quantities[symbol] * closes[symbol] * (1 - sell_fee), 2
            ),
        }
        for symbol in ALLOCATION_SYMBOLS
    }

    annual_returns: dict[str, float] = {}
    year_ends = equity.groupby(equity.index.year).last()
    previous = initial
    for year, value in year_ends.items():
        annual_returns[str(year)] = (float(value) / previous - 1) * 100
        previous = float(value)

    return {
        "name": variant.name,
        "description": variant.description,
        "start_date": sessions[0].date().isoformat(),
        "end_date": sessions[-1].date().isoformat(),
        "sessions": len(sessions),
        "initial_equity": initial,
        "final_equity": final,
        "net_profit": final - initial,
        "total_return_pct": (final / initial - 1) * 100,
        "cagr_pct": cagr * 100,
        "mdd_pct": mdd * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "average_exposure_pct": mean(exposures) * 100,
        "trade_fills": len(trades),
        "total_fees": total_fees,
        "final_cash": cash,
        "final_holdings": final_holdings,
        "rebalance_count": len(rebalance_rows),
        "avg_core_price": mean(core_prices),
        "avg_core_rounding_gap_usd": mean(core_gaps),
        "median_core_rounding_gap_usd": median(core_gaps),
        "max_core_rounding_gap_usd": max(core_gaps),
        "avg_total_rounding_gap_usd": mean(total_gaps),
        "median_total_rounding_gap_usd": median(total_gaps),
        "max_total_rounding_gap_usd": max(total_gaps),
        "avg_core_weight_error_bps": mean(core_bps),
        "avg_total_weight_error_bps": mean(total_bps),
        "avg_post_rebalance_cash": mean(post_cash),
        "annual_returns_pct": annual_returns,
        "rebalance_rows": rebalance_rows,
        "trades": trades,
        "equity_curve": {
            ts.date().isoformat(): float(value) for ts, value in equity.items()
        },
    }


def _diff(candidate: dict[str, object], control: dict[str, object]) -> dict[str, float]:
    keys = (
        "final_equity",
        "net_profit",
        "total_return_pct",
        "cagr_pct",
        "mdd_pct",
        "sharpe",
        "sortino",
        "total_fees",
        "final_cash",
        "avg_core_rounding_gap_usd",
        "avg_total_rounding_gap_usd",
        "avg_core_weight_error_bps",
        "avg_total_weight_error_bps",
        "avg_post_rebalance_cash",
    )
    return {key: float(candidate[key]) - float(control[key]) for key in keys}


def _pct_reduction(candidate: float, control: float) -> float:
    if abs(control) < 1e-12:
        return 0.0
    return (1.0 - candidate / control) * 100


def _render(payload: dict[str, object]) -> str:
    variants = payload["variants"]
    control = variants["QQQ_CONTROL"]
    synthetic = variants["QQQ_SCALED_TO_QQQM_PRICE"]
    qqqm = variants["QQQM_EXECUTION"]
    pure = payload["comparisons"]["pure_denomination"]
    real = payload["comparisons"]["real_qqqm"]

    lines = [
        "# QQQ → QQQM 실행 ETF 시뮬레이션",
        "",
        f"- 전략: `{payload['strategy_version']}` / config `{payload['config_version']}`",
        f"- 기간: `{payload['start_date']} ~ {payload['end_date']}`",
        "- 판단 데이터: QQQ 유지 (시장상태·모멘텀·SMA·변동성·V3.2.2 목표비중 모두 동일)",
        "- 비교 대상: 코어 실제 체결만 QQQ vs QQQM",
        "- 초기자본: $50,000 / HWM 이익 75% 재투자 / 최초진입 50→75→100%, 각 3 미국 거래세션",
        "- 체결: 정수주만 사용, 매수·매도 수수료 0.1%, 슬리피지 0.1%",
        "- `QQQ_SCALED_TO_QQQM_PRICE`는 QQQ의 수익률을 그대로 두고 주당 가격만 QQQM 수준으로 낮춘 가상 통제군입니다. 따라서 순수한 정수주 단위 효과를 분리합니다.",
        "",
        "## 1. 전체 결과",
        "",
        "| 항목 | QQQ 실제체결 | QQQ 저가격 가상체결 | QQQM 실제체결 |",
        "|---|---:|---:|---:|",
        f"| 최종자산 | ${control['final_equity']:,.2f} | ${synthetic['final_equity']:,.2f} | ${qqqm['final_equity']:,.2f} |",
        f"| 누적수익률 | {control['total_return_pct']:+.2f}% | {synthetic['total_return_pct']:+.2f}% | {qqqm['total_return_pct']:+.2f}% |",
        f"| CAGR | {control['cagr_pct']:+.2f}% | {synthetic['cagr_pct']:+.2f}% | {qqqm['cagr_pct']:+.2f}% |",
        f"| MDD | {control['mdd_pct']:.2f}% | {synthetic['mdd_pct']:.2f}% | {qqqm['mdd_pct']:.2f}% |",
        f"| Sharpe | {control['sharpe']:.3f} | {synthetic['sharpe']:.3f} | {qqqm['sharpe']:.3f} |",
        f"| 총 수수료 | ${control['total_fees']:,.2f} | ${synthetic['total_fees']:,.2f} | ${qqqm['total_fees']:,.2f} |",
        f"| 체결 건수 | {control['trade_fills']} | {synthetic['trade_fills']} | {qqqm['trade_fills']} |",
        f"| 평균 코어 주가(리밸런싱일) | ${control['avg_core_price']:,.2f} | ${synthetic['avg_core_price']:,.2f} | ${qqqm['avg_core_price']:,.2f} |",
        "",
        "## 2. 정수주 자금 활용도",
        "",
        "| 항목 | QQQ | QQQ 저가격 가상 | QQQM |",
        "|---|---:|---:|---:|",
        f"| 평균 코어 목표금액 미달 | ${control['avg_core_rounding_gap_usd']:,.2f} | ${synthetic['avg_core_rounding_gap_usd']:,.2f} | ${qqqm['avg_core_rounding_gap_usd']:,.2f} |",
        f"| 중앙값 코어 목표금액 미달 | ${control['median_core_rounding_gap_usd']:,.2f} | ${synthetic['median_core_rounding_gap_usd']:,.2f} | ${qqqm['median_core_rounding_gap_usd']:,.2f} |",
        f"| 최대 코어 목표금액 미달 | ${control['max_core_rounding_gap_usd']:,.2f} | ${synthetic['max_core_rounding_gap_usd']:,.2f} | ${qqqm['max_core_rounding_gap_usd']:,.2f} |",
        f"| 평균 코어 비중오차 | {control['avg_core_weight_error_bps']:.2f}bp | {synthetic['avg_core_weight_error_bps']:.2f}bp | {qqqm['avg_core_weight_error_bps']:.2f}bp |",
        f"| 평균 전체 목표금액 미달 | ${control['avg_total_rounding_gap_usd']:,.2f} | ${synthetic['avg_total_rounding_gap_usd']:,.2f} | ${qqqm['avg_total_rounding_gap_usd']:,.2f} |",
        f"| 평균 리밸런싱 후 현금 | ${control['avg_post_rebalance_cash']:,.2f} | ${synthetic['avg_post_rebalance_cash']:,.2f} | ${qqqm['avg_post_rebalance_cash']:,.2f} |",
        "",
        "### QQQ 대비 개선율",
        "",
        f"- 순수 저가격 효과 기준 코어 목표금액 미달 감소: **{payload['pure_core_gap_reduction_pct']:.2f}%**",
        f"- 실제 QQQM 기준 코어 목표금액 미달 감소: **{payload['real_core_gap_reduction_pct']:.2f}%**",
        f"- 실제 QQQM 기준 평균 코어 비중오차 감소: **{payload['real_core_bps_reduction_pct']:.2f}%**",
        "",
        "## 3. 수익 차이 분해",
        "",
        f"- **순수 정수주/주당가격 효과**(가상 저가격 QQQ - QQQ): 최종자산 **${pure['final_equity']:+,.2f}**, 누적수익률 **{pure['total_return_pct']:+.2f}%p**, CAGR **{pure['cagr_pct']:+.3f}%p**",
        f"- **실제 QQQM 전체 효과**(QQQM - QQQ): 최종자산 **${real['final_equity']:+,.2f}**, 누적수익률 **{real['total_return_pct']:+.2f}%p**, CAGR **{real['cagr_pct']:+.3f}%p**",
        "- 실제 QQQM 전체 효과에는 정수주 효율뿐 아니라 QQQM 자체의 추적오차·보수 차이도 포함됩니다.",
        "",
        "## 4. 연도별 수익률",
        "",
        "| 연도 | QQQ | QQQ 저가격 가상 | QQQM | QQQM-QQQ |",
        "|---|---:|---:|---:|---:|",
    ]
    years = sorted(set(control["annual_returns_pct"]) | set(qqqm["annual_returns_pct"]))
    for year in years:
        a = float(control["annual_returns_pct"].get(year, float("nan")))
        b = float(synthetic["annual_returns_pct"].get(year, float("nan")))
        c = float(qqqm["annual_returns_pct"].get(year, float("nan")))
        lines.append(f"| {year} | {a:+.2f}% | {b:+.2f}% | {c:+.2f}% | {c-a:+.2f}%p |")

    lines.extend(
        [
            "",
            "## 5. 해석 주의사항",
            "",
            "- 이 연구는 **전략 신호를 QQQM으로 바꾸지 않습니다.** QQQM은 주문/보유 종목으로만 대체했습니다.",
            "- 현금은 SGOV로 운용하지 않는 현행 V3.2.2 계약을 그대로 사용했습니다.",
            "- `목표금액 미달`은 각 리밸런싱 시점의 정수주 floor 때문에 목표달러 대비 주문 가능한 정수주 원가가 부족한 정도입니다. 시점별 잔액이므로 기간 합계는 손실액으로 해석하면 안 됩니다.",
            "- 최종 승격 여부는 실제 주문/계좌정합성/대시보드의 QQQ↔QQQM 역할 분리를 별도로 검증해야 합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    start = pd.Timestamp(args.start).date()
    end = pd.Timestamp(args.end).date()
    config, policy, onboarding, raw, targets, variants, anchor, scale = _prepare_history(
        start, end
    )

    results = {
        variant.name: _simulate(
            variant,
            config=config,
            policy=policy,
            onboarding=onboarding,
            raw=raw,
            targets=targets,
            start=start,
            end=end,
        )
        for variant in variants
    }
    control = results["QQQ_CONTROL"]
    synthetic = results["QQQ_SCALED_TO_QQQM_PRICE"]
    qqqm = results["QQQM_EXECUTION"]

    payload: dict[str, object] = {
        "strategy_version": config.version,
        "config_version": config.config_version,
        "start_date": control["start_date"],
        "end_date": control["end_date"],
        "qqqm_first_common_session": anchor.date().isoformat(),
        "qqqm_to_qqq_price_scale_at_anchor": scale,
        "signal_instrument": "QQQ",
        "execution_mapping_control": {"QQQ": "QQQ", "TQQQ": "TQQQ", "SOXL": "SOXL"},
        "execution_mapping_candidate": {"QQQ": "QQQM", "TQQQ": "TQQQ", "SOXL": "SOXL"},
        "variants": results,
        "comparisons": {
            "pure_denomination": _diff(synthetic, control),
            "real_qqqm": _diff(qqqm, control),
        },
        "pure_core_gap_reduction_pct": _pct_reduction(
            float(synthetic["avg_core_rounding_gap_usd"]),
            float(control["avg_core_rounding_gap_usd"]),
        ),
        "real_core_gap_reduction_pct": _pct_reduction(
            float(qqqm["avg_core_rounding_gap_usd"]),
            float(control["avg_core_rounding_gap_usd"]),
        ),
        "real_core_bps_reduction_pct": _pct_reduction(
            float(qqqm["avg_core_weight_error_bps"]),
            float(control["avg_core_weight_error_bps"]),
        ),
    }

    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = _render(payload)
    md_path.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
