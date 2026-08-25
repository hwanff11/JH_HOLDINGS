from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import research_v33_phase3_rerisk as p3
import research_v33_phase3_robustness as p3r
import research_v33_phase4_rerisk_guards as p4

CANDIDATES = (
    "MONTHLY_CONTROL",
    "RR_DAILY_V25_C1_STEP",
    "G_CD0_M1_OPEN",
    "G_CD0_M2_OPEN",
)


def main() -> None:
    end = date.today()
    config, policy, strategy_start, frames, virtual, active = p4.load_history(end)
    scenario_map = {scenario.name: scenario for scenario in p4.SCENARIOS}
    targets = {
        name: p4.build_guard_targets(
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
            name: p4.run_with_targets(
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
    roll3_control = p3r.rolling_windows(control, 3, config.backtest.annualization_days)
    roll5_control = p3r.rolling_windows(control, 5, config.backtest.annualization_days)

    payload = {
        "end_date": end.isoformat(),
        "candidates": {},
        "cost_stress_full": {},
    }
    for name, result in base_results.items():
        roll3 = p3r.rolling_windows(result, 3, config.backtest.annualization_days)
        roll5 = p3r.rolling_windows(result, 5, config.backtest.annualization_days)
        payload["candidates"][name] = {
            "annual_returns_pct": p3r.annual_returns(result.equity_curve),
            "rolling_3y": roll3,
            "rolling_5y": roll5,
            "rolling_3y_vs_control": p3r.summarize_rolling(roll3, roll3_control),
            "rolling_5y_vs_control": p3r.summarize_rolling(roll5, roll5_control),
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
        "# JDSS V3.3 Phase 4 — Guard 후보 Robustness",
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
        vals = [
            payload["candidates"][name]["annual_returns_pct"].get(year, 0.0)
            for name in CANDIDATES
        ]
        lines.append(f"|{year}|" + "|".join(f"{value:+.2f}%" for value in vals) + "|")

    Path("reports/v33-phase4-robustness.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path("reports/v33-phase4-robustness.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
