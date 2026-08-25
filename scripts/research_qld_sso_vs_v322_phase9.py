from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4

from jd_holdings.config import load_config
from jd_holdings.core.initial_onboarding import InitialOnboardingPolicy
from jd_holdings.core.v322_allocation import V322Policy, base_leverage, build_qqq_features
from jd_holdings.infrastructure.market_data import YFinanceDataSource

SCENARIOS = ("V322_QQQ_QLD", "V322_QLD_ONLY")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 9: replace V3.2.2 TQQQ/SOXL sleeves with QLD"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322-phase9.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322-phase9.md")
    return parser.parse_args()


def leverage_targets(
    qqq: pd.DataFrame,
    policy: V322Policy,
    scenario: str,
) -> pd.DataFrame:
    features = build_qqq_features(qqq, policy)
    symbols = ("QQQ", "QLD") if scenario == "V322_QQQ_QLD" else ("QLD",)
    targets = pd.DataFrame(0.0, index=features.index, columns=list(symbols))
    current_month: str | None = None
    leverage = policy.leverage_normal

    for timestamp, row in features.iterrows():
        month = str(timestamp.to_period("M"))
        if current_month != month:
            leverage = base_leverage(row, policy)
            current_month = month
        elif (
            not pd.isna(row.get("volatility"))
            and float(row["volatility"]) >= policy.volatility_brake
            and leverage > policy.leverage_defensive
        ):
            leverage = policy.leverage_defensive

        if scenario == "V322_QQQ_QLD":
            if leverage <= 1.0:
                targets.loc[timestamp, "QQQ"] = max(0.0, leverage)
            else:
                targets.loc[timestamp, "QQQ"] = max(0.0, 2.0 - leverage)
                targets.loc[timestamp, "QLD"] = max(0.0, leverage - 1.0)
        elif scenario == "V322_QLD_ONLY":
            targets.loc[timestamp, "QLD"] = max(0.0, leverage / 2.0)
        else:
            raise ValueError(scenario)
    return targets


def onboarding_target(
    target: dict[str, float],
    policy: InitialOnboardingPolicy,
    execution_index: int,
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


def rebalance(
    target: dict[str, float],
    quantities: dict[str, int],
    opens: dict[str, float],
    cash: float,
    sizing_equity: float,
    buy_fee: float,
    sell_fee: float,
    slippage: float,
) -> tuple[float, float]:
    desired: dict[str, int] = {}
    for symbol in quantities:
        buy_price = opens[symbol] * (1.0 + slippage)
        desired[symbol] = math.floor(
            sizing_equity * target.get(symbol, 0.0) / (buy_price * (1.0 + buy_fee))
        )

    traded_notional = 0.0
    for symbol in quantities:
        difference = desired[symbol] - quantities[symbol]
        if difference >= 0:
            continue
        quantity = -difference
        price = opens[symbol] * (1.0 - slippage)
        fee = quantity * price * sell_fee
        cash += quantity * price - fee
        quantities[symbol] -= quantity
        traded_notional += quantity * price

    for symbol in quantities:
        difference = desired[symbol] - quantities[symbol]
        if difference <= 0:
            continue
        price = opens[symbol] * (1.0 + slippage)
        affordable = math.floor(cash / (price * (1.0 + buy_fee)))
        quantity = min(difference, affordable)
        if quantity <= 0:
            continue
        fee = quantity * price * buy_fee
        cash -= quantity * price + fee
        quantities[symbol] += quantity
        traded_notional += quantity * price

    return cash, traded_notional


def simulate(
    qqq: pd.DataFrame,
    qld: pd.DataFrame,
    scenario: str,
    start: date,
    end: date,
    slippage: float,
) -> tuple[pd.Series, pd.Series]:
    config = load_config()
    policy = V322Policy.from_config(config)
    onboarding = InitialOnboardingPolicy.from_config(config)
    targets = leverage_targets(qqq, policy, scenario)
    symbols = tuple(targets.columns)
    frames = {"QQQ": qqq, "QLD": qld}
    index = qqq.index.intersection(qld.index).intersection(targets.index)
    sessions = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    prior = index[index < sessions[0]]
    if prior.empty:
        raise ValueError("start 이전 target warmup 거래일이 필요합니다")

    initial = float(policy.initial_capital)
    cash = initial
    high_water = initial
    quantities = {symbol: 0 for symbol in symbols}
    prior_target = {
        symbol: float(targets.loc[prior[-1], symbol])
        for symbol in symbols
        if float(targets.loc[prior[-1], symbol]) > 1e-12
    }
    pending = onboarding_target(prior_target, onboarding, 0)
    current: dict[str, float] | None = None
    equity_values: list[float] = []
    turnover_values: list[float] = []
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)

    for execution_index, timestamp in enumerate(sessions):
        opens = {symbol: float(frames[symbol].loc[timestamp, "open"]) for symbol in symbols}
        closes = {
            symbol: float(frames[symbol].loc[timestamp, "close"]) for symbol in symbols
        }
        open_equity = cash + sum(
            quantities[symbol] * opens[symbol] * (1.0 - sell_fee)
            for symbol in symbols
        )
        sizing_equity = initial + float(policy.hwm_reinvestment_fraction) * max(
            0.0, high_water - initial
        )
        sizing_equity = max(0.0, min(sizing_equity, open_equity))
        traded = 0.0

        if pending != current:
            cash, traded = rebalance(
                pending,
                quantities,
                opens,
                cash,
                sizing_equity,
                buy_fee,
                sell_fee,
                slippage,
            )
            current = pending

        liquidation = sum(
            quantities[symbol] * closes[symbol] * (1.0 - sell_fee)
            for symbol in symbols
        )
        equity = cash + liquidation
        equity_values.append(equity)
        turnover_values.append(traded / equity if equity > 0 else 0.0)
        high_water = max(high_water, equity)

        raw_target = {
            symbol: float(targets.loc[timestamp, symbol])
            for symbol in symbols
            if float(targets.loc[timestamp, symbol]) > 1e-12
        }
        pending = onboarding_target(raw_target, onboarding, execution_index + 1)

    return (
        pd.Series(equity_values, index=sessions, dtype=float),
        pd.Series(turnover_values, index=sessions, dtype=float),
    )


def load_frames(end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = load_config()
    start = pd.Timestamp(config.backtest.default_start).date()
    warmup = (start - timedelta(days=420)).isoformat()
    source = YFinanceDataSource(Path(".cache") / "qld-v322-simple-replacement")
    qqq = source.daily("QQQ", warmup, end.isoformat())
    qld = source.daily("QLD", warmup, end.isoformat())
    return qqq, qld


def render(payload: dict) -> str:
    control = payload["control"]["windows"]["full_2011_present"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 9 Simple V3.2.2 Replacement",
        "",
        "- V3.2.2 market timing/HWM75/onboarding은 유지",
        "- TQQQ/SOXL/SOXX RS/JDSS overlay는 제거",
        "- QQQ+QLD형과 QLD-only형을 production V3.2.2와 동일 기간/비용으로 비교",
        "",
        "|전략|CAGR|MDD|Sharpe|Calmar|OOS CAGR|OOS MDD|3Y승률|5Y승률|4조건 동시|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        (
            f"|V3.2.2|{control['cagr_pct']:+.2f}%|{control['mdd_pct']:.2f}%|"
            f"{control['sharpe']:.3f}|{control['calmar']:.3f}|-|-|-|-|-|"
        ),
    ]
    for row in payload["selection"]:
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['scenario']}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|"
            f"{oos['mdd_pct']:.2f}%|{row['rolling_3y']['beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['beat_rate_pct']:.1f}%|"
            f"{'YES' if row['strict_dominance'] else 'NO'}|"
        )
    lines.extend(["", "## 구현 의미", ""])
    lines.append(
        "- V322_QQQ_QLD: 0.5x는 QQQ 50%, 1.0x는 QQQ 100%, "
        "1.25x는 QQQ 75%+QLD 25%, 1.5x는 QQQ 50%+QLD 50%."
    )
    lines.append(
        "- V322_QLD_ONLY: 목표 노출 0.5/1.0/1.25/1.5x를 "
        "QLD 25/50/62.5/75% + 현금으로 구현."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    config = load_config()
    start = pd.Timestamp(config.backtest.default_start).date()
    qqq, qld = load_frames(end)

    controls: dict[str, dict] = {}
    by_cost: dict[str, list[dict]] = {}
    base_equities: dict[str, pd.Series] = {}
    control_equity: pd.Series | None = None

    for slippage in (0.0005, 0.0010, 0.0020):
        ctrl_equity, ctrl_windows = p4.canonical_v322(end, slippage)
        controls[f"{slippage:.4f}"] = {"windows": ctrl_windows}
        if slippage == 0.0010:
            control_equity = ctrl_equity

        rows = []
        for scenario in SCENARIOS:
            equity, turnover = simulate(
                qqq,
                qld,
                scenario,
                start,
                end,
                slippage,
            )
            if slippage == 0.0010:
                base_equities[scenario] = equity
            rows.append(
                {
                    "scenario": scenario,
                    "windows": p4.window_metrics(equity, turnover),
                }
            )
        by_cost[f"{slippage:.4f}"] = rows

    if control_equity is None:
        raise RuntimeError("V3.2.2 control missing")

    control_windows = controls["0.0010"]["windows"]
    control_full = control_windows["full_2011_present"]
    selection = []
    for row in by_cost["0.0010"]:
        item = json.loads(json.dumps(row))
        equity = base_equities[row["scenario"]]
        item["rolling_3y"] = p4.rolling_vs_control(equity, control_equity, 3)
        item["rolling_5y"] = p4.rolling_vs_control(equity, control_equity, 5)
        item["annual_returns_pct"] = p4.annual_returns(equity)
        checks = p4.dominance(item["windows"]["full_2011_present"], control_full)
        item["dominance_checks"] = checks
        item["strict_dominance"] = all(checks.values())
        cost_passes = []
        for cost in ("0.0005", "0.0010", "0.0020"):
            ctrl = controls[cost]["windows"]["full_2011_present"]
            candidate = next(
                value
                for value in by_cost[cost]
                if value["scenario"] == row["scenario"]
            )["windows"]["full_2011_present"]
            cost_passes.append(all(p4.dominance(candidate, ctrl).values()))
        item["cost_robust_strict_dominance"] = all(cost_passes)
        selection.append(item)

    selection.sort(
        key=lambda item: item["windows"]["full_2011_present"]["calmar"],
        reverse=True,
    )
    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 9 Simple V3.2.2 Replacement",
        "end_date": min(qqq.index[-1], qld.index[-1], control_equity.index[-1])
        .date()
        .isoformat(),
        "control": {
            "strategy": "JDSS-3.2.2-RS6M-ONEWAY-HWM75",
            "windows": control_windows,
            "annual_returns_pct": p4.annual_returns(control_equity),
        },
        "selection": selection,
        "cost_stress": {"control": controls, "candidates": by_cost},
    }
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_md.write_text(render(payload), encoding="utf-8")
    print(output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
