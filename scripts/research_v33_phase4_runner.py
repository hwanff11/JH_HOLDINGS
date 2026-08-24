from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd
from research_v33_phase3_rerisk import (
    load_history,
    replay_targets,
    result_row,
    run_with_targets,
    selection_score,
)
from research_v33_phase4_rerisk_guards import (
    SCENARIOS,
    build_guard_targets,
    render_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS V3.3 phase4 guard runner")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/v33-phase4-rerisk-guards.json")
    parser.add_argument("--output-md", default="reports/v33-phase4-rerisk-guards.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    config, policy, strategy_start, frames, virtual, active = load_history(end)
    base_slip = float(config.backtest.default_slippage)

    targets = {
        scenario.name: build_guard_targets(
            frames["QQQ"],
            frames[policy.rs_benchmark],
            active["TQQQ"],
            active["SOXL"],
            policy,
            scenario,
        )
        for scenario in SCENARIOS
    }
    production = replay_targets(
        frames["QQQ"],
        frames[policy.rs_benchmark],
        active["TQQQ"],
        active["SOXL"],
        policy,
    )
    pd.testing.assert_frame_equal(
        targets["MONTHLY_CONTROL"][production.columns],
        production,
    )

    results_by_cost: dict[str, list[dict]] = {}
    for slip in (0.0005, base_slip, 0.002):
        rows = []
        for scenario in SCENARIOS:
            result = run_with_targets(
                config,
                frames,
                virtual,
                targets[scenario.name],
                start=strategy_start,
                end=end,
                slippage=slip,
            )
            rows.append(result_row(result, scenario.name, slip, config))
        results_by_cost[f"{slip:.4f}"] = rows

    base_rows = results_by_cost[f"{base_slip:.4f}"]
    control = next(row for row in base_rows if row["scenario"] == "MONTHLY_CONTROL")
    ranked = []
    for row in base_rows:
        copy = json.loads(json.dumps(row))
        copy["selection_score"] = (
            0.0
            if row["scenario"] == "MONTHLY_CONTROL"
            else selection_score(row, control)
        )
        ranked.append(copy)
    ranked.sort(key=lambda row: row["selection_score"], reverse=True)

    payload = {
        "strategy_version": getattr(config, "version", "3.2.2"),
        "end_date": end.isoformat(),
        "scenario_count": len(SCENARIOS),
        "base_slippage": base_slip,
        "base_cost_results": base_rows,
        "train_validation_selection": ranked,
        "cost_stress_results": results_by_cost,
        "scenario_parameters": [
            {
                "name": s.name,
                "cooldown_days": s.cooldown_days,
                "max_rerisk_steps": s.max_rerisk_steps,
                "second_riskoff_lock": s.second_riskoff_lock,
            }
            for s in SCENARIOS
        ],
    }
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_md.write_text(render_markdown(payload), encoding="utf-8")
    print(output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
