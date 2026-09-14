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

VOL_WINDOWS = (10, 20, 40)
VOL_THRESHOLDS = (0.25, 0.30, 0.35)
RISK_EXPOSURES = (0.0, 0.25, 0.50)
WINDOWS = {
    "train_2007_2014": ("2007-01-01", "2014-12-31"),
    "validation_2015_2020": ("2015-01-01", "2020-12-31"),
    "oos_2021_present": ("2021-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2007_present": ("2007-01-01", None),
    "common_2011_present": ("2011-01-01", None),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    vol_window: int
    vol_threshold: float
    risk_exposure: float


def scenarios() -> tuple[Scenario, ...]:
    rows = []
    for window in VOL_WINDOWS:
        for threshold in VOL_THRESHOLDS:
            for exposure in RISK_EXPOSURES:
                rows.append(
                    Scenario(
                        f"VOL{window}_{int(threshold * 100)}_E{int(exposure * 100)}",
                        window,
                        threshold,
                        exposure,
                    )
                )
    return tuple(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 11: robustness of one-signal QLD volatility strategy"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-only-vol-phase11.json")
    parser.add_argument("--output-md", default="reports/qld-only-vol-phase11.md")
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
            current = min(current, scenario.risk_exposure)
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


def annual_returns(equity: pd.Series) -> dict[str, float]:
    output = {}
    for year, view in equity.groupby(equity.index.year):
        if len(view) < 2:
            continue
        output[str(year)] = round(
            (float(view.iloc[-1]) / float(view.iloc[0]) - 1.0) * 100.0,
            2,
        )
    return output


def drawdown_period(equity: pd.Series) -> dict[str, str | float]:
    equity = equity.dropna()
    drawdown = equity / equity.cummax() - 1.0
    trough = drawdown.idxmin()
    peak = equity.loc[:trough].idxmax()
    return {
        "peak": peak.date().isoformat(),
        "trough": trough.date().isoformat(),
        "mdd_pct": round(float(drawdown.loc[trough]) * 100.0, 2),
    }


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


def pre_oos_score(row: dict) -> tuple[float, float, float]:
    train = row["windows"]["train_2007_2014"]
    validation = row["windows"]["validation_2015_2020"]
    return (
        min(train["calmar"], validation["calmar"]),
        min(train["sharpe"], validation["sharpe"]),
        min(train["cagr_pct"], validation["cagr_pct"]),
    )


def render(payload: dict) -> str:
    lines = [
        "# JDSS QLD-only Research — Phase 11 Volatility-only Robustness",
        "",
        "- 단 하나의 신호: QQQ realized volatility",
        "- 정상시 QLD 100%, 변동성 임계 초과 시 지정 노출로 즉시 감속",
        "- 월말에만 100% 재진입 가능(one-way)",
        "- 후보 순위는 Train 2007-2014 + Validation 2015-2020만으로 결정",
        "",
        "|후보|Train CAGR|Val CAGR|Full CAGR|MDD|Sharpe|Calmar|OOS CAGR|2022~ CAGR|5Y QQQ 승률|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["top_pre_oos"]:
        train = row["windows"]["train_2007_2014"]
        val = row["windows"]["validation_2015_2020"]
        full = row["windows"]["full_2007_present"]
        oos = row["windows"]["oos_2021_present"]
        stress = row["windows"]["recent_stress_2022_present"]
        lines.append(
            f"|{row['scenario']}|{train['cagr_pct']:+.2f}%|{val['cagr_pct']:+.2f}%|"
            f"{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|{full['sharpe']:.3f}|"
            f"{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|{stress['cagr_pct']:+.2f}%|"
            f"{row['rolling_5y_vs_qqq']['beat_rate_pct']:.1f}%|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    frame = build_frame(prices)
    qqq_equity = benchmark_equity(frame["QQQ"])
    qld_equity = benchmark_equity(frame["QLD"])

    by_cost: dict[str, list[dict]] = {}
    for slippage in (0.0005, 0.0010, 0.0020):
        rows = []
        for scenario in scenarios():
            equity, turnover = simulate(frame, scenario, slippage)
            record = {
                "scenario": scenario.name,
                "vol_window": scenario.vol_window,
                "vol_threshold": scenario.vol_threshold,
                "risk_exposure": scenario.risk_exposure,
                "windows": window_metrics(equity, turnover),
            }
            if slippage == 0.0010:
                record["annual_returns_pct"] = annual_returns(equity)
                record["drawdown"] = drawdown_period(equity)
                record["rolling_3y_vs_qqq"] = rolling_delta(equity, qqq_equity, 3)
                record["rolling_5y_vs_qqq"] = rolling_delta(equity, qqq_equity, 5)
                record["rolling_5y_vs_qld"] = rolling_delta(equity, qld_equity, 5)
            rows.append(record)
        by_cost[f"{slippage:.4f}"] = rows

    base = by_cost["0.0010"]
    ranked = sorted(base, key=pre_oos_score, reverse=True)
    baseline = next(row for row in base if row["scenario"] == "VOL20_30_E0")
    v322_equity, v322_windows = p4.canonical_v322(end, 0.0010)

    payload = {
        "research": "JDSS QLD-only Phase 11 Volatility-only Robustness",
        "end_date": min(prices.index[-1], v322_equity.index[-1]).date().isoformat(),
        "scenario_count": len(base),
        "baseline": baseline,
        "top_pre_oos": ranked[:10],
        "all_base_cost": base,
        "cost_stress": by_cost,
        "benchmarks": {
            "QQQ_BH": window_metrics(qqq_equity),
            "QLD_BH": window_metrics(qld_equity),
            "V3.2.2_common_2011": v322_windows["full_2011_present"],
        },
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
