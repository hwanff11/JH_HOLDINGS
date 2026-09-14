from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import research_qld_sso_alpha as p1


@dataclass(frozen=True)
class Scenario:
    name: str
    switch_rule: str


SCENARIOS = (
    Scenario("QLD_TRENDVOL_F50", "never"),
    Scenario("SSO_DIVERGENCE", "divergence"),
    Scenario("SSO_RS126_HEALTHY", "rs126_healthy"),
    Scenario("SSO_DUALRS_HEALTHY", "dualrs_healthy"),
    Scenario("SSO_RISKADJ_HEALTHY", "riskadj_healthy"),
    Scenario("SSO_DIVERGENCE_RS126", "divergence_rs126"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QLD/SSO selective SSO Phase 3")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-alpha-phase3.json")
    parser.add_argument("--output-md", default="reports/qld-sso-alpha-phase3.md")
    return parser.parse_args()


def choose_asset(row: pd.Series, rule: str) -> str:
    if rule == "never":
        return "QLD"
    spy_healthy = row["SPY"] > row["SPY_sma200"]
    qqq_weak = row["QQQ"] <= row["QQQ_sma200"]
    spy_rs126 = row["SPY_r126"] > row["QQQ_r126"]
    spy_dual = spy_rs126 and row["SPY_r63"] > row["QQQ_r63"]
    q_score = row["QQQ_r126"] / row["QQQ_vol20"]
    s_score = row["SPY_r126"] / row["SPY_vol20"]
    if rule == "divergence":
        use_sso = spy_healthy and qqq_weak
    elif rule == "rs126_healthy":
        use_sso = spy_healthy and spy_rs126
    elif rule == "dualrs_healthy":
        use_sso = spy_healthy and spy_dual
    elif rule == "riskadj_healthy":
        use_sso = spy_healthy and s_score > q_score
    elif rule == "divergence_rs126":
        use_sso = spy_healthy and qqq_weak and spy_rs126
    else:
        raise ValueError(rule)
    return "SSO" if use_sso else "QLD"


def riskoff(row: pd.Series, asset: str) -> bool:
    base = "QQQ" if asset == "QLD" else "SPY"
    return bool(
        row[base] <= row[f"{base}_sma200"]
        or row[f"{base}_vol20"] >= 0.30
    )


def build_targets(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    weights = pd.DataFrame(np.nan, index=frame.index, columns=["QLD", "SSO"])
    month_end = p1.rebalance_mask(frame.index, "monthly")
    current_asset: str | None = None
    exposure = 0.0
    required = (
        "QQQ_r63",
        "SPY_r63",
        "QQQ_r126",
        "SPY_r126",
        "QQQ_sma200",
        "SPY_sma200",
        "QQQ_vol20",
        "SPY_vol20",
    )
    for timestamp, is_month_end in month_end.items():
        row = frame.loc[timestamp]
        if any(pd.isna(row[field]) for field in required):
            continue
        if bool(is_month_end) or current_asset is None:
            current_asset = choose_asset(row, scenario.switch_rule)
            exposure = 1.0
        if riskoff(row, current_asset):
            exposure = min(exposure, 0.50)
        weights.loc[timestamp] = (
            (exposure, 0.0) if current_asset == "QLD" else (0.0, exposure)
        )
    return weights.ffill()


def simulate(prices: pd.DataFrame, scenario: Scenario, slippage: float):
    frame = p1.features(prices)
    held = build_targets(frame, scenario).shift(1).dropna()
    returns = prices[["QLD", "SSO"]].pct_change().reindex(held.index)
    valid = held.notna().all(axis=1) & returns.notna().all(axis=1)
    held = held.loc[valid]
    returns = returns.loc[valid]
    turnover = held.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = held.iloc[0].abs().sum()
    net = (held * returns).sum(axis=1) - turnover * slippage
    return (1.0 + net).cumprod(), turnover


def render(payload: dict) -> str:
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 3 Selective SSO",
        "",
        "- 공통: 월말 자산 선택, selected index SMA200 이탈 또는 vol20>=30%면 50%로 감속",
        "- 월중 감속 후 다음 월말까지 재가속 금지",
        "- 질문: QLD 중심 구조에 선택적 SSO 회전이 추가 alpha 또는 drawdown 개선을 주는가?",
        "",
        "|순위|전략|Full CAGR|MDD|Calmar|OOS CAGR|OOS MDD|3Y QQQ승률|5Y QQQ승률|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(payload["selection"], 1):
        full = row["windows"]["full"]
        oos = row["windows"]["oos_2021_present"]
        lines.append(
            f"|{rank}|{row['scenario']}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|{oos['mdd_pct']:.2f}%|"
            f"{row['rolling_3y']['qqq_beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['qqq_beat_rate_pct']:.1f}%|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    qqq = p1.benchmark_equity(prices["QQQ"])
    qld = p1.benchmark_equity(prices["QLD"])
    benchmarks = {
        "QQQ": p1.window_metrics(qqq),
        "QLD": p1.window_metrics(qld),
    }
    by_cost = {}
    equities = {}
    for slippage in (0.0005, 0.0010, 0.0020):
        rows = []
        for scenario in SCENARIOS:
            equity, turnover = simulate(prices, scenario, slippage)
            if slippage == 0.0010:
                equities[scenario.name] = equity
            rows.append(
                {
                    "scenario": scenario.name,
                    "switch_rule": scenario.switch_rule,
                    "windows": p1.window_metrics(equity, turnover),
                }
            )
        by_cost[f"{slippage:.4f}"] = rows

    selection = []
    for row in by_cost["0.0010"]:
        item = json.loads(json.dumps(row))
        item["selection_score"] = p1.selection_score(
            item["windows"], benchmarks["QQQ"], benchmarks["QLD"]
        )
        equity = equities[row["scenario"]]
        item["rolling_3y"] = p1.rolling_summary(equity, qqq, 3)
        item["rolling_5y"] = p1.rolling_summary(equity, qqq, 5)
        item["annual_returns_pct"] = p1.annual_returns(equity)
        selection.append(item)
    selection.sort(key=lambda value: value["selection_score"], reverse=True)
    payload = {
        "research": "JDSS QLD/SSO Alpha Research Phase 3 Selective SSO",
        "end_date": prices.index[-1].date().isoformat(),
        "benchmarks": benchmarks,
        "selection": selection,
        "cost_stress_results": by_cost,
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
