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
import research_qld_sso_vs_v322_phase5 as p5


@dataclass(frozen=True)
class Scenario:
    name: str
    rerisk_mode: str
    confirmation_days: int
    step_up: bool


SCENARIOS = (
    Scenario("MONTHLY_ONEWAY", "monthly", 1, False),
    Scenario("DAILY_C1_FULL", "daily", 1, False),
    Scenario("DAILY_C3_FULL", "daily", 3, False),
    Scenario("DAILY_C5_FULL", "daily", 5, False),
    Scenario("DAILY_C1_STEP", "daily", 1, True),
    Scenario("DAILY_C3_STEP", "daily", 3, True),
    Scenario("WEEKLY_FULL", "weekly", 1, False),
    Scenario("WEEKLY_STEP", "weekly", 1, True),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 6: QLD recovery re-risk cadence vs V3.2.2"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322-phase6.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322-phase6.md")
    return parser.parse_args()


def desired_exposure(row: pd.Series) -> float:
    trend_bad = bool(row["QQQ"] <= row["QQQ_sma200"])
    vol_bad = bool(row["QQQ_vol20"] >= 0.30)
    if trend_bad and vol_bad:
        return 0.0
    if trend_bad or vol_bad:
        return 0.50
    return 1.0


def increase(current: float, desired: float, step_up: bool) -> float:
    if not step_up:
        return desired
    return min(desired, current + 0.50)


def build_targets(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    weights = pd.DataFrame(np.nan, index=frame.index, columns=["QLD", "SSO"])
    month_end = p1.rebalance_mask(frame.index, "monthly")
    week_end = p1.rebalance_mask(frame.index, "weekly")
    exposure: float | None = None
    recovery_streak = 0

    for timestamp in frame.index:
        row = frame.loc[timestamp]
        if pd.isna(row["QQQ_sma200"]) or pd.isna(row["QQQ_vol20"]):
            continue
        desired = desired_exposure(row)
        if exposure is None:
            exposure = desired
            weights.loc[timestamp] = (exposure, 0.0)
            continue

        if desired < exposure:
            exposure = desired
            recovery_streak = 0
        elif desired > exposure:
            if scenario.rerisk_mode == "monthly":
                if bool(month_end.loc[timestamp]):
                    exposure = increase(exposure, desired, scenario.step_up)
            elif scenario.rerisk_mode == "weekly":
                if bool(week_end.loc[timestamp]):
                    exposure = increase(exposure, desired, scenario.step_up)
            elif scenario.rerisk_mode == "daily":
                recovery_streak += 1
                if recovery_streak >= scenario.confirmation_days:
                    exposure = increase(exposure, desired, scenario.step_up)
                    recovery_streak = 0
            else:
                raise ValueError(scenario.rerisk_mode)
        else:
            recovery_streak = 0

        weights.loc[timestamp] = (exposure, 0.0)
    return weights.ffill()


def simulate(
    prices: pd.DataFrame,
    scenario: Scenario,
    slippage: float,
) -> tuple[pd.Series, pd.Series]:
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


def score_vs_control(row: dict, control: dict) -> float:
    train = row["train_2011_2018"]
    validation = row["validation_2019_2022"]
    ctrl_train = control["train_2011_2018"]
    ctrl_validation = control["validation_2019_2022"]
    return round(
        0.45
        * (
            (train["cagr_pct"] - ctrl_train["cagr_pct"])
            + 5.0 * (train["calmar"] - ctrl_train["calmar"])
            + (train["sharpe"] - ctrl_train["sharpe"])
        )
        + 0.55
        * (
            (validation["cagr_pct"] - ctrl_validation["cagr_pct"])
            + 5.0 * (validation["calmar"] - ctrl_validation["calmar"])
            + (validation["sharpe"] - ctrl_validation["sharpe"])
        ),
        4,
    )


def render(payload: dict) -> str:
    control = payload["control"]["windows"]["full_2011_present"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 6 Re-Risk Cadence",
        "",
        "- 상태: QQQ SMA200/vol20(30%) 두 신호로 QLD 노출 100% / 50% / 0%",
        "- Risk-off는 매일 즉시, 이번 변수는 risk-on 재가속 시점만 변경",
        "- 신호는 종가 계산 후 다음 거래일부터 적용",
        "",
        f"V3.2.2: CAGR {control['cagr_pct']:+.2f}% / MDD {control['mdd_pct']:.2f}% / "
        f"Sharpe {control['sharpe']:.3f} / Calmar {control['calmar']:.3f}",
        "",
        "|후보|CAGR|MDD|Sharpe|Calmar|OOS CAGR|OOS MDD|3Y승률|5Y승률|Full 4조건|OOS CAGR+MDD|비용3단계|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["selection"]:
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['scenario']}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|"
            f"{oos['mdd_pct']:.2f}%|{row['rolling_3y']['beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['beat_rate_pct']:.1f}%|"
            f"{'YES' if row['strict_full_dominance'] else 'NO'}|"
            f"{'YES' if row['oos_return_drawdown_dominance'] else 'NO'}|"
            f"{'YES' if row['cost_robust_full_dominance'] else 'NO'}|"
        )
    lines.extend(["", "## 최대낙폭 구간", ""])
    detail = payload["control"]["drawdown"]
    lines.append(
        f"- V3.2.2: {detail['mdd_pct']:.2f}% "
        f"({detail['peak_date']} → {detail['trough_date']}, recovery={detail['recovery_date']})"
    )
    for row in payload["selection"][:4]:
        detail = row["drawdown"]
        lines.append(
            f"- {row['scenario']}: {detail['mdd_pct']:.2f}% "
            f"({detail['peak_date']} → {detail['trough_date']}, recovery={detail['recovery_date']})"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)

    controls: dict[str, dict] = {}
    rows_by_cost: dict[str, list[dict]] = {}
    base_equities: dict[str, pd.Series] = {}
    control_equity: pd.Series | None = None

    for slippage in (0.0005, 0.0010, 0.0020):
        control, control_windows = p4.canonical_v322(end, slippage)
        controls[f"{slippage:.4f}"] = {"windows": control_windows}
        if slippage == 0.0010:
            control_equity = control
        rows = []
        for scenario in SCENARIOS:
            equity, turnover = simulate(prices, scenario, slippage)
            equity = equity.loc[equity.index >= pd.Timestamp("2011-01-01")]
            turnover = turnover.reindex(equity.index).fillna(0.0)
            if slippage == 0.0010:
                base_equities[scenario.name] = equity
            rows.append(
                {
                    "scenario": scenario.name,
                    "rerisk_mode": scenario.rerisk_mode,
                    "confirmation_days": scenario.confirmation_days,
                    "step_up": scenario.step_up,
                    "windows": p4.window_metrics(equity, turnover),
                }
            )
        rows_by_cost[f"{slippage:.4f}"] = rows

    if control_equity is None:
        raise RuntimeError("V3.2.2 control missing")
    base_control = controls["0.0010"]["windows"]
    control_full = base_control["full_2011_present"]
    control_oos = base_control["oos_2023_present"]

    selection = []
    for row in rows_by_cost["0.0010"]:
        item = json.loads(json.dumps(row))
        equity = base_equities[row["scenario"]]
        item["selection_score"] = score_vs_control(item["windows"], base_control)
        item["rolling_3y"] = p4.rolling_vs_control(equity, control_equity, 3)
        item["rolling_5y"] = p4.rolling_vs_control(equity, control_equity, 5)
        item["annual_returns_pct"] = p4.annual_returns(equity)
        item["drawdown"] = p5.drawdown_detail(equity)
        full = item["windows"]["full_2011_present"]
        oos = item["windows"]["oos_2023_present"]
        checks = p4.dominance(full, control_full)
        item["dominance_checks"] = checks
        item["strict_full_dominance"] = all(checks.values())
        item["oos_return_drawdown_dominance"] = (
            oos["cagr_pct"] > control_oos["cagr_pct"]
            and abs(oos["mdd_pct"]) <= abs(control_oos["mdd_pct"])
        )
        passes = []
        for cost in ("0.0005", "0.0010", "0.0020"):
            ctrl = controls[cost]["windows"]["full_2011_present"]
            candidate = next(
                value
                for value in rows_by_cost[cost]
                if value["scenario"] == row["scenario"]
            )["windows"]["full_2011_present"]
            passes.append(all(p4.dominance(candidate, ctrl).values()))
        item["cost_robust_full_dominance"] = all(passes)
        selection.append(item)

    selection.sort(key=lambda value: value["selection_score"], reverse=True)
    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 6 Re-Risk Cadence",
        "end_date": min(prices.index[-1], control_equity.index[-1]).date().isoformat(),
        "control": {
            "strategy": "JDSS-3.2.2-RS6M-ONEWAY-HWM75",
            "windows": base_control,
            "drawdown": p5.drawdown_detail(control_equity),
            "annual_returns_pct": p4.annual_returns(control_equity),
        },
        "selection": selection,
        "cost_stress": {"control": controls, "candidates": rows_by_cost},
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
