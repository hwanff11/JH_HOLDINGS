from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4
import research_qld_sso_vs_v322_phase5 as p5

QLD_WEIGHTS = tuple(value / 10 for value in range(1, 10))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 7: blend production V3.2.2 with QLD alpha sleeve"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322-phase7.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322-phase7.md")
    return parser.parse_args()


def monthly_blend(
    control_equity: pd.Series,
    qld_equity: pd.Series,
    qld_weight: float,
    rebalance_slippage: float,
) -> tuple[pd.Series, pd.Series]:
    common = control_equity.index.intersection(qld_equity.index)
    common = common[common >= pd.Timestamp("2011-01-01")]
    control_returns = control_equity.pct_change().reindex(common).fillna(0.0)
    qld_returns = qld_equity.pct_change().reindex(common).fillna(0.0)

    control_weight = 1.0 - qld_weight
    control_capital = control_weight
    qld_capital = qld_weight
    values = []
    turnover = []
    prior_period = None

    for timestamp in common:
        period = timestamp.to_period("M")
        traded = 0.0
        if prior_period is not None and period != prior_period:
            total = control_capital + qld_capital
            target_control = total * control_weight
            target_qld = total * qld_weight
            traded = abs(target_control - control_capital) + abs(target_qld - qld_capital)
            total_after_cost = total - traded * rebalance_slippage
            control_capital = total_after_cost * control_weight
            qld_capital = total_after_cost * qld_weight
        control_capital *= 1.0 + float(control_returns.loc[timestamp])
        qld_capital *= 1.0 + float(qld_returns.loc[timestamp])
        values.append(control_capital + qld_capital)
        turnover.append(traded)
        prior_period = period

    return (
        pd.Series(values, index=common, dtype=float),
        pd.Series(turnover, index=common, dtype=float),
    )


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
    ctrl = payload["control"]["windows"]["full_2011_present"]
    qld = payload["qld_candidate"]["windows"]["full_2011_present"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 7 Blended Portfolio",
        "",
        "- Sleeve A: production V3.2.2 canonical equity",
        "- Sleeve B: QLD_STEP_50_0 (정상 100%, 단일 위험 50%, 이중 위험 0%, monthly one-way)",
        "- 두 sleeve 간 비중은 월초에만 재조정하며 추가 슬리피지를 보수적으로 차감",
        "",
        f"V3.2.2: CAGR {ctrl['cagr_pct']:+.2f}% / MDD {ctrl['mdd_pct']:.2f}% / "
        f"Sharpe {ctrl['sharpe']:.3f} / Calmar {ctrl['calmar']:.3f}",
        f"QLD 후보: CAGR {qld['cagr_pct']:+.2f}% / MDD {qld['mdd_pct']:.2f}% / "
        f"Sharpe {qld['sharpe']:.3f} / Calmar {qld['calmar']:.3f}",
        "",
        "|QLD sleeve|CAGR|MDD|Sharpe|Calmar|OOS CAGR|OOS MDD|3Y승률|5Y승률|Full 4조건|OOS CAGR+MDD|비용3단계|",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["selection"]:
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['qld_weight']:.0%}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
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
        f"({detail['peak_date']} → {detail['trough_date']})"
    )
    for row in payload["selection"][:4]:
        detail = row["drawdown"]
        lines.append(
            f"- QLD {row['qld_weight']:.0%}: {detail['mdd_pct']:.2f}% "
            f"({detail['peak_date']} → {detail['trough_date']})"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    qld_scenario = p4.Scenario("QLD_STEP_50_0", "never", 0.50, 0.0)

    controls: dict[str, dict] = {}
    qld_by_cost: dict[str, dict] = {}
    blends_by_cost: dict[str, list[dict]] = {}
    base_control_equity: pd.Series | None = None
    base_qld_equity: pd.Series | None = None
    base_blend_equities: dict[float, pd.Series] = {}

    for slippage in (0.0005, 0.0010, 0.0020):
        control_equity, control_windows = p4.canonical_v322(end, slippage)
        qld_equity, qld_turnover = p4.simulate_candidate(prices, qld_scenario, slippage)
        qld_equity = qld_equity.loc[qld_equity.index >= pd.Timestamp("2011-01-01")]
        qld_turnover = qld_turnover.reindex(qld_equity.index).fillna(0.0)
        controls[f"{slippage:.4f}"] = {"windows": control_windows}
        qld_by_cost[f"{slippage:.4f}"] = {
            "windows": p4.window_metrics(qld_equity, qld_turnover)
        }
        if slippage == 0.0010:
            base_control_equity = control_equity
            base_qld_equity = qld_equity

        rows = []
        for qld_weight in QLD_WEIGHTS:
            equity, turnover = monthly_blend(
                control_equity,
                qld_equity,
                qld_weight,
                slippage,
            )
            if slippage == 0.0010:
                base_blend_equities[qld_weight] = equity
            rows.append(
                {
                    "qld_weight": qld_weight,
                    "windows": p4.window_metrics(equity, turnover),
                }
            )
        blends_by_cost[f"{slippage:.4f}"] = rows

    if base_control_equity is None or base_qld_equity is None:
        raise RuntimeError("base sleeve equity missing")
    control_windows = controls["0.0010"]["windows"]
    control_full = control_windows["full_2011_present"]
    control_oos = control_windows["oos_2023_present"]

    selection = []
    for row in blends_by_cost["0.0010"]:
        item = json.loads(json.dumps(row))
        weight = float(row["qld_weight"])
        equity = base_blend_equities[weight]
        item["selection_score"] = score_vs_control(item["windows"], control_windows)
        item["rolling_3y"] = p4.rolling_vs_control(equity, base_control_equity, 3)
        item["rolling_5y"] = p4.rolling_vs_control(equity, base_control_equity, 5)
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
                for value in blends_by_cost[cost]
                if float(value["qld_weight"]) == weight
            )["windows"]["full_2011_present"]
            passes.append(all(p4.dominance(candidate, ctrl).values()))
        item["cost_robust_full_dominance"] = all(passes)
        selection.append(item)

    selection.sort(key=lambda value: value["selection_score"], reverse=True)
    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 7 Blended Portfolio",
        "end_date": min(prices.index[-1], base_control_equity.index[-1]).date().isoformat(),
        "control": {
            "strategy": "JDSS-3.2.2-RS6M-ONEWAY-HWM75",
            "windows": control_windows,
            "drawdown": p5.drawdown_detail(base_control_equity),
        },
        "qld_candidate": {
            "strategy": "QLD_STEP_50_0",
            "windows": qld_by_cost["0.0010"]["windows"],
            "drawdown": p5.drawdown_detail(base_qld_equity),
        },
        "selection": selection,
        "cost_stress": {
            "control": controls,
            "qld_candidate": qld_by_cost,
            "blends": blends_by_cost,
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
