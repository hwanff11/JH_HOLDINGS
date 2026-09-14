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

WINDOWS = {
    "train_2007_2014": ("2007-01-01", "2014-12-31"),
    "validation_2015_2020": ("2015-01-01", "2020-12-31"),
    "oos_2021_present": ("2021-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2007_present": ("2007-01-01", None),
    "common_2011_present": ("2011-01-01", None),
}
SMA_WINDOWS = (150, 200, 250)
VOL_THRESHOLDS = (0.25, 0.30, 0.35)
EXPOSURE_MAPS = {
    "STEP_50_0": (0.50, 0.00),
    "STEP_50_25": (0.50, 0.25),
    "STEP_75_25": (0.75, 0.25),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    sma_window: int | None
    vol_threshold: float | None
    one_bad_exposure: float
    both_bad_exposure: float
    rule: str


def scenarios() -> tuple[Scenario, ...]:
    rows: list[Scenario] = []
    for sma in SMA_WINDOWS:
        rows.append(Scenario(f"TREND_SMA{sma}_CASH", sma, None, 0.0, 0.0, "trend"))
    for vol in VOL_THRESHOLDS:
        pct = int(vol * 100)
        rows.append(Scenario(f"VOL{pct}_CASH", None, vol, 0.0, 0.0, "vol"))
    for sma in SMA_WINDOWS:
        for vol in VOL_THRESHOLDS:
            pct = int(vol * 100)
            for label, (one_bad, both_bad) in EXPOSURE_MAPS.items():
                rows.append(
                    Scenario(
                        f"SMA{sma}_VOL{pct}_{label}",
                        sma,
                        vol,
                        one_bad,
                        both_bad,
                        "dual",
                    )
                )
    return tuple(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 10: simple QLD-only strategy sweep"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-only-simple-phase10.json")
    parser.add_argument("--output-md", default="reports/qld-only-simple-phase10.md")
    return parser.parse_args()


def build_frame(prices: pd.DataFrame) -> pd.DataFrame:
    out = prices[["QQQ", "QLD"]].copy()
    returns = out["QQQ"].pct_change(fill_method=None)
    out["QQQ_vol20"] = returns.rolling(20).std() * np.sqrt(252.0)
    for window in SMA_WINDOWS:
        out[f"QQQ_sma{window}"] = out["QQQ"].rolling(window).mean()
    return out


def build_exposure(frame: pd.DataFrame, scenario: Scenario) -> pd.Series:
    reset = p1.rebalance_mask(frame.index, "monthly")
    exposure = pd.Series(np.nan, index=frame.index, dtype=float)
    current = 1.0
    for timestamp, is_reset in reset.items():
        row = frame.loc[timestamp]
        if bool(is_reset):
            current = 1.0

        trend_bad = False
        vol_bad = False
        if scenario.sma_window is not None:
            sma = row[f"QQQ_sma{scenario.sma_window}"]
            if pd.isna(sma):
                exposure.loc[timestamp] = np.nan
                continue
            trend_bad = bool(row["QQQ"] <= sma)
        if scenario.vol_threshold is not None:
            vol = row["QQQ_vol20"]
            if pd.isna(vol):
                exposure.loc[timestamp] = np.nan
                continue
            vol_bad = bool(vol >= scenario.vol_threshold)

        if scenario.rule == "trend":
            if trend_bad:
                current = min(current, scenario.one_bad_exposure)
        elif scenario.rule == "vol":
            if vol_bad:
                current = min(current, scenario.one_bad_exposure)
        elif scenario.rule == "dual":
            if trend_bad and vol_bad:
                current = min(current, scenario.both_bad_exposure)
            elif trend_bad or vol_bad:
                current = min(current, scenario.one_bad_exposure)
        else:
            raise ValueError(scenario.rule)
        exposure.loc[timestamp] = current
    return exposure.ffill()


def simulate(
    frame: pd.DataFrame,
    scenario: Scenario,
    slippage: float,
) -> tuple[pd.Series, pd.Series]:
    target = build_exposure(frame, scenario)
    held = target.shift(1).dropna()
    qld_return = frame["QLD"].pct_change(fill_method=None).reindex(held.index)
    valid = held.notna() & qld_return.notna()
    held = held.loc[valid]
    qld_return = qld_return.loc[valid]
    turnover = held.diff().abs()
    if len(turnover):
        turnover.iloc[0] = abs(float(held.iloc[0]))
    net = held * qld_return - turnover * slippage
    return (1.0 + net).cumprod(), turnover


def window_metrics(
    equity: pd.Series,
    turnover: pd.Series | None = None,
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
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
    deltas: list[float] = []
    first_year = int(common[0].year)
    last_year = int(common[-1].year)
    for start_year in range(first_year, last_year - years + 2):
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
        "beat_rate_pct": round(sum(value > 0 for value in deltas) / len(deltas) * 100.0, 1),
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
        "# JDSS QLD-only Simple Research — Phase 10",
        "",
        "## 원칙",
        "",
        "- 자산은 QLD + 현금만 사용",
        "- 신호는 QQQ 종가로 계산하고 다음 거래일부터 적용",
        "- 월말 reset 후 월중에는 risk-off만 허용(one-way)",
        "- 후보 선택은 Train 2007-2014 + Validation 2015-2020만 사용",
        "- OOS 2021~와 2022~ stress는 선택 후 확인",
        "",
        "## Benchmarks",
        "",
        "|Benchmark|Full CAGR|MDD|Sharpe|Calmar|",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in payload["benchmarks"].items():
        full = metrics["full_2007_present"]
        lines.append(
            f"|{name}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|"
        )
    lines.extend(
        [
            "",
            "## Pre-OOS 상위 10개",
            "",
            "|후보|Train CAGR|Val CAGR|Full CAGR|MDD|Sharpe|Calmar|OOS CAGR|Stress CAGR|5Y QQQ 승률|",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
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
    benchmarks = {
        "QQQ_BH": window_metrics(qqq_equity),
        "QLD_BH": window_metrics(qld_equity),
    }

    rows_by_cost: dict[str, list[dict]] = {}
    base_equities: dict[str, pd.Series] = {}
    for slippage in (0.0005, 0.0010, 0.0020):
        rows: list[dict] = []
        for scenario in scenarios():
            equity, turnover = simulate(frame, scenario, slippage)
            record = {
                "scenario": scenario.name,
                "sma_window": scenario.sma_window,
                "vol_threshold": scenario.vol_threshold,
                "one_bad_exposure": scenario.one_bad_exposure,
                "both_bad_exposure": scenario.both_bad_exposure,
                "rule": scenario.rule,
                "windows": window_metrics(equity, turnover),
            }
            if slippage == 0.0010:
                base_equities[scenario.name] = equity
                record["rolling_3y_vs_qqq"] = rolling_delta(equity, qqq_equity, 3)
                record["rolling_5y_vs_qqq"] = rolling_delta(equity, qqq_equity, 5)
                record["rolling_5y_vs_qld"] = rolling_delta(equity, qld_equity, 5)
            rows.append(record)
        rows_by_cost[f"{slippage:.4f}"] = rows

    base_rows = rows_by_cost["0.0010"]
    ranked = sorted(base_rows, key=pre_oos_score, reverse=True)
    top = ranked[:10]

    baseline_name = "SMA200_VOL30_STEP_50_0"
    baseline = next(row for row in base_rows if row["scenario"] == baseline_name)

    v322_equity, v322_windows = p4.canonical_v322(end, 0.0010)
    payload = {
        "research": "JDSS QLD-only Simple Research Phase 10",
        "end_date": min(prices.index[-1], v322_equity.index[-1]).date().isoformat(),
        "scenario_count": len(base_rows),
        "selection_rule": "lexicographic min(Train,Validation): Calmar, Sharpe, CAGR",
        "benchmarks": benchmarks,
        "v322_common_2011": v322_windows["full_2011_present"],
        "baseline": baseline,
        "top_pre_oos": top,
        "cost_stress": rows_by_cost,
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
