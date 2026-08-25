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
    selector: str
    risk_rule: str
    floor: float


SCENARIOS = (
    Scenario("QLD_TRENDVOL_F50", "qld", "trend_or_vol", 0.50),
    Scenario("RS126_TREND_F50", "rs126", "trend", 0.50),
    Scenario("RS126_VOL30_F50", "rs126", "vol", 0.50),
    Scenario("RS126_TRENDVOL_F50", "rs126", "trend_or_vol", 0.50),
    Scenario("RS126_TRENDVOL_F75", "rs126", "trend_or_vol", 0.75),
    Scenario("RISKADJ_TREND_F50", "riskadj", "trend", 0.50),
    Scenario("RISKADJ_VOL30_F50", "riskadj", "vol", 0.50),
    Scenario("RISKADJ_TRENDVOL_F50", "riskadj", "trend_or_vol", 0.50),
    Scenario("RISKADJ_TRENDVOL_F75", "riskadj", "trend_or_vol", 0.75),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QLD/SSO alpha Phase 2 exposure sweep"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-alpha-phase2.json")
    parser.add_argument("--output-md", default="reports/qld-sso-alpha-phase2.md")
    return parser.parse_args()


def select_asset(row: pd.Series, selector: str) -> str:
    if selector == "qld":
        return "QLD"
    if selector == "rs126":
        return "QLD" if row["QQQ_r126"] >= row["SPY_r126"] else "SSO"
    if selector == "riskadj":
        q_score = row["QQQ_r126"] / row["QQQ_vol20"]
        s_score = row["SPY_r126"] / row["SPY_vol20"]
        return "QLD" if q_score >= s_score else "SSO"
    raise ValueError(selector)


def riskoff(row: pd.Series, asset: str, rule: str) -> bool:
    base = "QQQ" if asset == "QLD" else "SPY"
    trend_bad = row[base] <= row[f"{base}_sma200"]
    vol_bad = row[f"{base}_vol20"] >= 0.30
    if rule == "trend":
        return bool(trend_bad)
    if rule == "vol":
        return bool(vol_bad)
    if rule == "trend_or_vol":
        return bool(trend_bad or vol_bad)
    raise ValueError(rule)


def build_targets(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    weights = pd.DataFrame(np.nan, index=frame.index, columns=["QLD", "SSO"])
    month_end = p1.rebalance_mask(frame.index, "monthly")
    current_asset: str | None = None
    exposure = 0.0

    for timestamp, is_month_end in month_end.items():
        row = frame.loc[timestamp]
        required = (
            "QQQ_r126",
            "SPY_r126",
            "QQQ_sma200",
            "SPY_sma200",
            "QQQ_vol20",
            "SPY_vol20",
        )
        if any(pd.isna(row[field]) for field in required):
            continue
        if bool(is_month_end) or current_asset is None:
            current_asset = select_asset(row, scenario.selector)
            exposure = 1.0
        if riskoff(row, current_asset, scenario.risk_rule):
            exposure = min(exposure, scenario.floor)
        weights.loc[timestamp] = (
            (exposure, 0.0) if current_asset == "QLD" else (0.0, exposure)
        )

    return weights.ffill()


def simulate(
    prices: pd.DataFrame,
    scenario: Scenario,
    slippage: float,
) -> tuple[pd.Series, pd.Series]:
    frame = p1.features(prices)
    targets = build_targets(frame, scenario)
    held = targets.shift(1).dropna()
    asset_returns = prices[["QLD", "SSO"]].pct_change().reindex(held.index)
    valid = held.notna().all(axis=1) & asset_returns.notna().all(axis=1)
    held = held.loc[valid]
    asset_returns = asset_returns.loc[valid]
    turnover = held.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = held.iloc[0].abs().sum()
    gross = (held * asset_returns).sum(axis=1)
    net = gross - turnover * slippage
    return (1.0 + net).cumprod(), turnover


def render(payload: dict) -> str:
    benchmarks = payload["benchmarks"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 2",
        "",
        "- 자산 선택: 월말 QQQ/SPY 상대강도 또는 위험조정 상대강도",
        "- 위험 축소: 월중 매일 확인, 한 번 감속되면 다음 월말까지 재가속 금지",
        "- 위험구간 노출: 50% 또는 75%; 나머지는 현금(수익률 0% 가정)",
        "- 신호는 종가 계산 후 다음 거래일부터 반영",
        "",
        "## Benchmarks",
        "",
        "|Benchmark|CAGR|MDD|Calmar|",
        "|---|---:|---:|---:|",
    ]
    for name in ("QQQ", "QLD", "SSO"):
        metric = benchmarks[name]["full"]
        lines.append(
            f"|{name}|{metric['cagr_pct']:+.2f}%|{metric['mdd_pct']:.2f}%|"
            f"{metric['calmar']:.3f}|"
        )
    lines.extend(
        [
            "",
            "## Train+Validation 사전순위",
            "",
            "|순위|전략|점수|Full CAGR|MDD|Calmar|OOS CAGR|OOS MDD|3Y QQQ승률|5Y QQQ승률|목표|",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for rank, row in enumerate(payload["selection"], 1):
        full = row["windows"]["full"]
        oos = row["windows"]["oos_2021_present"]
        lines.append(
            f"|{rank}|{row['scenario']}|{row['selection_score']:+.3f}|"
            f"{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|{full['calmar']:.3f}|"
            f"{oos['cagr_pct']:+.2f}%|{oos['mdd_pct']:.2f}%|"
            f"{row['rolling_3y']['qqq_beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['qqq_beat_rate_pct']:.1f}%|"
            f"{'YES' if row['objective_pass'] else 'NO'}|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    qqq_equity = p1.benchmark_equity(prices["QQQ"])
    qld_equity = p1.benchmark_equity(prices["QLD"])
    sso_equity = p1.benchmark_equity(prices["SSO"])
    benchmarks = {
        "QQQ": p1.window_metrics(qqq_equity),
        "QLD": p1.window_metrics(qld_equity),
        "SSO": p1.window_metrics(sso_equity),
    }

    by_cost: dict[str, list[dict]] = {}
    base_equities: dict[str, pd.Series] = {}
    for slippage in (0.0005, 0.0010, 0.0020):
        rows = []
        for scenario in SCENARIOS:
            equity, turnover = simulate(prices, scenario, slippage)
            if slippage == 0.0010:
                base_equities[scenario.name] = equity
            windows = p1.window_metrics(equity, turnover)
            full = windows["full"]
            objective_pass = (
                full["cagr_pct"] > benchmarks["QQQ"]["full"]["cagr_pct"]
                and abs(full["mdd_pct"]) < abs(benchmarks["QLD"]["full"]["mdd_pct"])
            )
            rows.append(
                {
                    "scenario": scenario.name,
                    "selector": scenario.selector,
                    "risk_rule": scenario.risk_rule,
                    "floor": scenario.floor,
                    "windows": windows,
                    "objective_pass": objective_pass,
                }
            )
        by_cost[f"{slippage:.4f}"] = rows

    selection = []
    for row in by_cost["0.0010"]:
        item = json.loads(json.dumps(row))
        item["selection_score"] = p1.selection_score(
            item["windows"],
            benchmarks["QQQ"],
            benchmarks["QLD"],
        )
        equity = base_equities[row["scenario"]]
        item["rolling_3y"] = p1.rolling_summary(equity, qqq_equity, 3)
        item["rolling_5y"] = p1.rolling_summary(equity, qqq_equity, 5)
        item["annual_returns_pct"] = p1.annual_returns(equity)
        selection.append(item)
    selection.sort(key=lambda value: value["selection_score"], reverse=True)

    payload = {
        "research": "JDSS QLD/SSO Alpha Research Phase 2",
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
