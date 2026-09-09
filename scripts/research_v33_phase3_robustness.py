from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import research_v33_phase3_rerisk as p3

CANDIDATES = (
    "MONTHLY_CONTROL",
    "RR_DAILY_V20_C1_FULL",
    "RR_DAILY_V25_C1_STEP",
    "RR_DAILY_V25_C1_FULL",
)


def annual_returns(equity: pd.Series) -> dict[str, float]:
    out: dict[str, float] = {}
    for year, view in equity.groupby(equity.index.year):
        if len(view) < 2:
            continue
        out[str(year)] = round((float(view.iloc[-1]) / float(view.iloc[0]) - 1) * 100, 2)
    return out


def rolling_windows(result, years: int, annual_days: int) -> list[dict]:
    first_year = int(result.equity_curve.index[0].year)
    last_year = int(result.equity_curve.index[-1].year)
    rows = []
    for start_year in range(first_year, last_year - years + 2):
        start = f"{start_year}-01-01"
        end = f"{start_year + years - 1}-12-31"
        try:
            metric = p3.window_metrics(result, start, end, annual_days)
        except (IndexError, ValueError):
            continue
        rows.append({"start_year": start_year, "end_year": start_year + years - 1, **metric})
    return rows


def summarize_rolling(candidate: list[dict], control: list[dict]) -> dict:
    by_key = {(x["start_year"], x["end_year"]): x for x in control}
    deltas = []
    mdd_deltas = []
    for row in candidate:
        base = by_key.get((row["start_year"], row["end_year"]))
        if not base:
            continue
        deltas.append(row["cagr_pct"] - base["cagr_pct"])
        mdd_deltas.append(abs(row["mdd_pct"]) - abs(base["mdd_pct"]))
    mdd_not_worse = (
        round(sum(x <= 0 for x in mdd_deltas) / len(mdd_deltas) * 100, 2)
        if mdd_deltas
        else 0.0
    )
    return {
        "windows": len(deltas),
        "cagr_beat_rate_pct": round(sum(x > 0 for x in deltas) / len(deltas) * 100, 2)
        if deltas
        else 0.0,
        "median_cagr_delta_pctpt": round(float(pd.Series(deltas).median()), 2)
        if deltas
        else 0.0,
        "worst_cagr_delta_pctpt": round(min(deltas), 2) if deltas else 0.0,
        "mdd_not_worse_rate_pct": mdd_not_worse,
    }


def main() -> None:
    end = date.today()
    config, policy, strategy_start, frames, virtual, active = p3.load_history(end)
    scenario_map = {s.name: s for s in p3.SCENARIOS}
    targets = {
        name: p3.build_candidate_targets(
            frames["QQQ"],
            frames[policy.rs_benchmark],
            active["TQQQ"],
            active["SOXL"],
            policy,
            scenario_map[name],
        )
        for name in CANDIDATES
    }
    results_by_cost = {}
    for slip in (0.0005, float(config.backtest.default_slippage), 0.002):
        results_by_cost[f"{slip:.4f}"] = {
            name: p3.run_with_targets(
                config,
                frames,
                virtual,
                targets[name],
                start=strategy_start,
                end=end,
                slippage=slip,
            )
            for name in CANDIDATES
        }

    base_key = f"{float(config.backtest.default_slippage):.4f}"
    base_results = results_by_cost[base_key]
    control = base_results["MONTHLY_CONTROL"]
    roll3_control = rolling_windows(control, 3, config.backtest.annualization_days)
    roll5_control = rolling_windows(control, 5, config.backtest.annualization_days)

    payload = {
        "end_date": end.isoformat(),
        "candidates": {},
        "cost_stress_full": {},
    }
    for name, result in base_results.items():
        roll3 = rolling_windows(result, 3, config.backtest.annualization_days)
        roll5 = rolling_windows(result, 5, config.backtest.annualization_days)
        payload["candidates"][name] = {
            "annual_returns_pct": annual_returns(result.equity_curve),
            "rolling_3y": roll3,
            "rolling_5y": roll5,
            "rolling_3y_vs_control": summarize_rolling(roll3, roll3_control),
            "rolling_5y_vs_control": summarize_rolling(roll5, roll5_control),
        }
    for cost, results in results_by_cost.items():
        payload["cost_stress_full"][cost] = {
            name: p3.window_metrics(
                result,
                "2011-01-01",
                None,
                config.backtest.annualization_days,
            )
            for name, result in results.items()
        }

    lines = [
        "# JDSS V3.3 Phase 3 — 상위 후보 Robustness",
        "",
        "## Rolling window 비교",
        "",
        "|후보|3Y CAGR 승률|3Y 중앙 ΔCAGR|3Y 최악 ΔCAGR|5Y CAGR 승률|5Y 중앙 ΔCAGR|5Y 최악 ΔCAGR|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in CANDIDATES:
        r3 = payload["candidates"][name]["rolling_3y_vs_control"]
        r5 = payload["candidates"][name]["rolling_5y_vs_control"]
        lines.append(
            f"|{name}|{r3['cagr_beat_rate_pct']:.1f}%|{r3['median_cagr_delta_pctpt']:+.2f}%p|"
            f"{r3['worst_cagr_delta_pctpt']:+.2f}%p|{r5['cagr_beat_rate_pct']:.1f}%|"
            f"{r5['median_cagr_delta_pctpt']:+.2f}%p|{r5['worst_cagr_delta_pctpt']:+.2f}%p|"
        )
    lines.extend(["", "## 연도별 수익률 — 기본 슬리피지", ""])
    years = sorted(payload["candidates"]["MONTHLY_CONTROL"]["annual_returns_pct"])
    lines.append("|연도|" + "|".join(CANDIDATES) + "|")
    lines.append("|---:|" + "|".join(["---:"] * len(CANDIDATES)) + "|")
    for year in years:
        vals = [payload["candidates"][n]["annual_returns_pct"].get(year, 0.0) for n in CANDIDATES]
        lines.append(f"|{year}|" + "|".join(f"{v:+.2f}%" for v in vals) + "|")

    Path("reports/v33-phase3-robustness.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path("reports/v33-phase3-robustness.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
