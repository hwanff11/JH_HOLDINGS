from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4

VOL_WINDOWS = (5, 10, 15, 20)
VOL_THRESHOLDS = (0.225, 0.25, 0.275)
WINDOWS = {
    "train_2007_2014": ("2007-01-01", "2014-12-31"),
    "validation_2015_2020": ("2015-01-01", "2020-12-31"),
    "oos_2021_present": ("2021-01-01", None),
    "oos_2023_present": ("2023-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2007_present": ("2007-01-01", None),
    "common_2011_present": ("2011-01-01", None),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    vol_window: int
    vol_threshold: float


def scenarios() -> tuple[Scenario, ...]:
    return tuple(
        Scenario(
            f"VOL{window}_{threshold * 100:.1f}_CASH".replace(".", "p"),
            window,
            threshold,
        )
        for window in VOL_WINDOWS
        for threshold in VOL_THRESHOLDS
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 12: local robustness of QLD volatility/cash rule"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-only-vol-phase12.json")
    parser.add_argument("--output-md", default="reports/qld-only-vol-phase12.md")
    return parser.parse_args()


def build_frame(prices: pd.DataFrame) -> pd.DataFrame:
    out = prices[["QQQ", "QLD"]].copy()
    returns = out["QQQ"].pct_change(fill_method=None)
    for window in VOL_WINDOWS:
        out[f"vol{window}"] = returns.rolling(window).std() * np.sqrt(252.0)
    return out


def target_exposure(frame: pd.DataFrame, scenario: Scenario) -> pd.Series:
    reset = p1.rebalance_mask(frame.index, "monthly")
    target = pd.Series(np.nan, index=frame.index, dtype=float)
    current = 1.0
    field = f"vol{scenario.vol_window}"
    for timestamp, is_reset in reset.items():
        value = frame.loc[timestamp, field]
        if pd.isna(value):
            continue
        if bool(is_reset):
            current = 1.0
        if float(value) >= scenario.vol_threshold:
            current = 0.0
        target.loc[timestamp] = current
    return target.ffill()


def simulate(
    frame: pd.DataFrame,
    scenario: Scenario,
    slippage: float,
) -> tuple[pd.Series, pd.Series]:
    held = target_exposure(frame, scenario).shift(1).dropna()
    returns = frame["QLD"].pct_change(fill_method=None).reindex(held.index)
    valid = held.notna() & returns.notna()
    held = held.loc[valid]
    returns = returns.loc[valid]
    turnover = held.diff().abs()
    if len(turnover):
        turnover.iloc[0] = abs(float(held.iloc[0]))
    net = held * returns - turnover * slippage
    return (1.0 + net).cumprod(), turnover


def window_metrics(
    equity: pd.Series,
    turnover: pd.Series | None = None,
) -> dict[str, dict[str, float]]:
    output = {}
    for name, (start, end) in WINDOWS.items():
        view = equity.loc[equity.index >= pd.Timestamp(start)]
        if end is not None:
            view = view.loc[view.index <= pd.Timestamp(end)]
        selected = turnover.reindex(view.index) if turnover is not None else None
        output[name] = p4.metrics(view, selected)
    return output


def benchmark_equity(price: pd.Series) -> pd.Series:
    returns = price.pct_change(fill_method=None).dropna()
    return (1.0 + returns).cumprod()


def rolling_delta(
    candidate: pd.Series,
    benchmark: pd.Series,
    years: int,
) -> dict[str, float]:
    common = candidate.index.intersection(benchmark.index)
    candidate = candidate.loc[common]
    benchmark = benchmark.loc[common]
    deltas = []
    for start_year in range(int(common[0].year), int(common[-1].year) - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        end = pd.Timestamp(f"{start_year + years - 1}-12-31")
        cand = candidate.loc[(candidate.index >= start) & (candidate.index <= end)]
        base = benchmark.loc[(benchmark.index >= start) & (benchmark.index <= end)]
        if len(cand) < 100 or len(base) < 100:
            continue
        deltas.append(p4.metrics(cand)["cagr_pct"] - p4.metrics(base)["cagr_pct"])
    if not deltas:
        return {"windows": 0, "beat_rate_pct": 0.0, "median_delta_pctpt": 0.0, "worst_delta_pctpt": 0.0}
    return {
        "windows": len(deltas),
        "beat_rate_pct": round(sum(x > 0 for x in deltas) / len(deltas) * 100.0, 1),
        "median_delta_pctpt": round(float(np.median(deltas)), 2),
        "worst_delta_pctpt": round(float(min(deltas)), 2),
    }


def render(payload: dict) -> str:
    v322 = payload["v322_common_2011"]
    lines = [
        "# JDSS QLD-only Research — Phase 12 Local Robustness",
        "",
        "- QLD + 현금만 사용",
        "- QQQ realized-volatility 하나만 사용",
        "- 임계 초과 시 즉시 현금, 재진입은 월말 reset에서만 가능",
        "",
        f"V3.2.2 common 2011: CAGR {v322['cagr_pct']:.2f}%, "
        f"MDD {v322['mdd_pct']:.2f}%, Sharpe {v322['sharpe']:.3f}, "
        f"Calmar {v322['calmar']:.3f}",
        "",
        "|후보|Full CAGR|MDD|Sharpe|Calmar|2011~ CAGR|2011~ MDD|2023~ CAGR|2023~ MDD|5Y QQQ 승률|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        full = row["windows"]["full_2007_present"]
        common = row["windows"]["common_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['scenario']}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|{common['cagr_pct']:+.2f}%|"
            f"{common['mdd_pct']:.2f}%|{oos['cagr_pct']:+.2f}%|{oos['mdd_pct']:.2f}%|"
            f"{row['rolling_5y_vs_qqq']['beat_rate_pct']:.1f}%|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    frame = build_frame(prices)
    qqq_equity = benchmark_equity(frame["QQQ"])
    rows = []
    for scenario in scenarios():
        equity, turnover = simulate(frame, scenario, 0.0010)
        rows.append(
            {
                "scenario": scenario.name,
                "vol_window": scenario.vol_window,
                "vol_threshold": scenario.vol_threshold,
                "windows": window_metrics(equity, turnover),
                "rolling_3y_vs_qqq": rolling_delta(equity, qqq_equity, 3),
                "rolling_5y_vs_qqq": rolling_delta(equity, qqq_equity, 5),
            }
        )

    v322_equity, v322_windows = p4.canonical_v322(end, 0.0010)
    payload = {
        "research": "JDSS QLD-only Phase 12 Local Robustness",
        "end_date": min(prices.index[-1], v322_equity.index[-1]).date().isoformat(),
        "v322_common_2011": v322_windows["full_2011_present"],
        "rows": rows,
    }
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render(payload), encoding="utf-8")
    print(output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
