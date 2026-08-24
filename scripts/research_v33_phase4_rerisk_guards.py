from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    AllocationState,
    apply_jdss_overlay,
    base_leverage,
    base_weights,
    build_qqq_features,
    build_rs_features,
    replay_targets,
    semiconductor_wins,
    trend_vote,
)
from research_v33_phase3_rerisk import (
    load_history,
    next_step,
    result_row,
    run_with_targets,
    selection_score,
)


@dataclass(frozen=True)
class GuardScenario:
    name: str
    cooldown_days: int
    max_rerisk_steps: int | None
    second_riskoff_lock: bool


CONTROL = GuardScenario("MONTHLY_CONTROL", 0, None, False)
BASE_SHADOW = GuardScenario("RR_DAILY_V25_C1_STEP", 0, None, False)
SCENARIOS = [CONTROL, BASE_SHADOW]
for cooldown in (0, 3, 5, 10):
    for max_steps in (1, 2, 3, None):
        for second_lock in (False, True):
            if cooldown == 0 and max_steps is None and not second_lock:
                continue
            max_label = "INF" if max_steps is None else str(max_steps)
            lock_label = "LOCK" if second_lock else "OPEN"
            name = f"G_CD{cooldown}_M{max_label}_{lock_label}"
            SCENARIOS.append(GuardScenario(name, cooldown, max_steps, second_lock))
SCENARIOS = tuple(SCENARIOS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS V3.3 phase4 re-risk guard sweep")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/v33-phase4-rerisk-guards.json")
    parser.add_argument("--output-md", default="reports/v33-phase4-rerisk-guards.md")
    return parser.parse_args()


def recovery_ok(row: pd.Series, policy) -> bool:
    required = ("volatility", "sma_long", "ret_short", "ret_medium")
    if any(pd.isna(row.get(field)) for field in required):
        return False
    return (
        float(row["volatility"]) < 0.25
        and float(row["close"]) > float(row["sma_long"])
        and float(row["ret_short"]) > 0
        and float(row["ret_medium"]) > 0
        and trend_vote(row, policy)
    )


def build_guard_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy,
    scenario: GuardScenario,
) -> pd.DataFrame:
    if scenario.name == "MONTHLY_CONTROL":
        return replay_targets(qqq_frame, semi_frame, active_tqqq, active_soxl, policy)

    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semi_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)

    state: AllocationState | None = None
    month_rerisk_steps = 0
    rerisk_happened = False
    month_locked = False
    cooldown_remaining = 0
    rows: list[dict] = []

    for timestamp in index:
        qqq_row = qqq.loc[timestamp]
        semi_row = semi.loc[timestamp]
        month = str(timestamp.to_period("M"))

        if state is None or state.month != month:
            state = AllocationState(
                month=month,
                leverage=base_leverage(qqq_row, policy),
                semiconductor_active=semiconductor_wins(qqq_row, semi_row),
            )
            month_rerisk_steps = 0
            rerisk_happened = False
            month_locked = False
            cooldown_remaining = 0
        else:
            leverage = state.leverage
            volatility = qqq_row.get("volatility")
            riskoff_now = (
                not pd.isna(volatility)
                and float(volatility) >= policy.volatility_brake
                and leverage > policy.leverage_defensive
            )
            if riskoff_now:
                leverage = policy.leverage_defensive
                cooldown_remaining = scenario.cooldown_days
                if scenario.second_riskoff_lock and rerisk_happened:
                    month_locked = True

            semi_active = state.semiconductor_active
            if semi_active and not semiconductor_wins(qqq_row, semi_row):
                semi_active = False

            if cooldown_remaining > 0 and not riskoff_now:
                cooldown_remaining -= 1

            target = base_leverage(qqq_row, policy)
            can_rerisk = (
                not month_locked
                and cooldown_remaining == 0
                and leverage < target
                and recovery_ok(qqq_row, policy)
                and (
                    scenario.max_rerisk_steps is None
                    or month_rerisk_steps < scenario.max_rerisk_steps
                )
            )
            if can_rerisk:
                new_leverage = next_step(leverage, target)
                if new_leverage > leverage + 1e-12:
                    leverage = new_leverage
                    month_rerisk_steps += 1
                    rerisk_happened = True

            state = AllocationState(month, leverage, semi_active)

        weights = apply_jdss_overlay(
            base_weights(state, policy),
            active_tqqq=bool(active_tqqq.loc[timestamp]),
            active_soxl=bool(active_soxl.loc[timestamp]),
            policy=policy,
        )
        row = {
            "trade_date": timestamp.date().isoformat(),
            "leverage": state.leverage,
            "semiconductor_active": state.semiconductor_active,
            "jdss_tqqq_active": bool(active_tqqq.loc[timestamp]),
            "jdss_soxl_active": bool(active_soxl.loc[timestamp]),
            "month_rerisk_steps": month_rerisk_steps,
            "month_locked": month_locked,
            "cooldown_remaining": cooldown_remaining,
        }
        row.update({symbol: float(weights.get(symbol, 0.0)) for symbol in ALLOCATION_SYMBOLS})
        rows.append(row)

    return pd.DataFrame(rows, index=index)


def render_markdown(payload: dict) -> str:
    rows = payload["base_cost_results"]
    ranked = payload["train_validation_selection"]
    control = next(r for r in rows if r["scenario"] == "MONTHLY_CONTROL")
    shadow = next(r for r in rows if r["scenario"] == "RR_DAILY_V25_C1_STEP")
    lines = [
        "# JDSS V3.3 Phase 4 — Re-Risk One-Way Guard 전수검증",
        "",
        f"- Production control: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- Phase 3 shadow 고정: Daily / Vol25 / 1일 / Step-up",
        "- 변경 변수: cooldown × 월중 re-risk step cap × second-risk-off lock",
        f"- 총 후보: {len(rows)}개 (control + unguarded shadow 포함)",
        "",
        "## 기준선",
        "",
        f"- MONTHLY_CONTROL Full CAGR {control['windows']['full_2011_present']['cagr_pct']:+.2f}% / "
        f"MDD {control['windows']['full_2011_present']['mdd_pct']:.2f}% / "
        f"Calmar {control['windows']['full_2011_present']['calmar']:.3f}",
        f"- RR_DAILY_V25_C1_STEP Full CAGR {shadow['windows']['full_2011_present']['cagr_pct']:+.2f}% / "
        f"MDD {shadow['windows']['full_2011_present']['mdd_pct']:.2f}% / "
        f"Calmar {shadow['windows']['full_2011_present']['calmar']:.3f}",
        "",
        "## Train+Validation 상위 12개",
        "",
        "|순위|시나리오|점수|Full CAGR|Full MDD|Calmar|OOS CAGR|Stress CAGR|Turnover|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(ranked[:12], 1):
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        stress = row["windows"]["recent_stress_2022_present"]
        lines.append(
            f"|{rank}|{row['scenario']}|{row['selection_score']:+.4f}|"
            f"{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|{full['calmar']:.3f}|"
            f"{oos['cagr_pct']:+.2f}%|{stress['cagr_pct']:+.2f}%|{full['turnover_x_per_year']:.2f}x|"
        )
    lines.extend(["", "## Full Calmar 상위 15개", ""])
    lines.extend([
        "|시나리오|CAGR|MDD|Sharpe|Calmar|Turnover/yr|",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in sorted(
        rows,
        key=lambda r: r["windows"]["full_2011_present"]["calmar"],
        reverse=True,
    )[:15]:
        m = row["windows"]["full_2011_present"]
        lines.append(
            f"|{row['scenario']}|{m['cagr_pct']:+.2f}%|{m['mdd_pct']:.2f}%|"
            f"{m['sharpe']:.3f}|{m['calmar']:.3f}|{m['turnover_x_per_year']:.2f}x|"
        )
    return "\n".join(lines) + "\n"


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

    base_key = f"{base_slip:.4f}"
    base_rows = results_by_cost[base_key]
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
        "strategy_version": getattr(config.strategy, "version", "unknown"),
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
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_markdown(payload), encoding="utf-8")
    print(output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
