from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4

EXPOSURES = (0.50, 0.45, 0.40, 0.35, 0.30, 0.25)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 5: QLD risk-off exposure boundary sweep vs V3.2.2"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322-phase5.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322-phase5.md")
    return parser.parse_args()


def drawdown_detail(equity: pd.Series) -> dict[str, str | float | None]:
    equity = equity.dropna()
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    trough = drawdown.idxmin()
    peak = equity.loc[:trough].idxmax()
    peak_value = float(equity.loc[peak])
    after = equity.loc[trough:]
    recovered = after.loc[after >= peak_value]
    recovery = recovered.index[0] if not recovered.empty else None
    return {
        "mdd_pct": round(float(drawdown.loc[trough]) * 100, 2),
        "peak_date": peak.date().isoformat(),
        "trough_date": trough.date().isoformat(),
        "recovery_date": recovery.date().isoformat() if recovery is not None else None,
    }


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
        "# JDSS QLD/SSO Alpha Research — Phase 5 Risk-Off Boundary",
        "",
        "- 고정: 평상시 QLD 100%, 월간 reset, 월중 one-way risk-off",
        "- 고정: QQQ SMA200 이탈과 vol20>=30%가 모두 발생하면 현금 100%",
        "- 단일 변수: 위험 조건 1개만 발생했을 때 QLD 노출 50/45/40/35/30/25%",
        "- 비교: production canonical V3.2.2, 공통 2011~현재",
        "",
        "## V3.2.2 Control",
        "",
        f"CAGR {control['cagr_pct']:+.2f}% / MDD {control['mdd_pct']:.2f}% / "
        f"Sharpe {control['sharpe']:.3f} / Calmar {control['calmar']:.3f}",
        "",
        "## 후보",
        "",
        "|노출|Full CAGR|MDD|Sharpe|Calmar|OOS CAGR|OOS MDD|3Y승률|5Y승률|Full 4조건|OOS CAGR+MDD|비용3단계|",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["selection"]:
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['one_bad_exposure']:.0%}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|"
            f"{oos['mdd_pct']:.2f}%|{row['rolling_3y']['beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['beat_rate_pct']:.1f}%|"
            f"{'YES' if row['strict_full_dominance'] else 'NO'}|"
            f"{'YES' if row['oos_return_drawdown_dominance'] else 'NO'}|"
            f"{'YES' if row['cost_robust_full_dominance'] else 'NO'}|"
        )
    lines.extend(["", "## 최대낙폭 발생 구간", ""])
    detail = payload["control"]["drawdown"]
    lines.append(
        f"- V3.2.2: {detail['mdd_pct']:.2f}% "
        f"({detail['peak_date']} → {detail['trough_date']}, recovery={detail['recovery_date']})"
    )
    for row in payload["selection"][:3]:
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
        v322_equity, v322_windows = p4.canonical_v322(end, slippage)
        controls[f"{slippage:.4f}"] = {"windows": v322_windows}
        if slippage == 0.0010:
            control_equity = v322_equity
        rows = []
        for exposure in EXPOSURES:
            scenario = p4.Scenario(
                name=f"QLD_ONEBAD_{int(exposure * 100):02d}_BOTH_CASH",
                switch_rule="never",
                one_bad_exposure=exposure,
                both_bad_exposure=0.0,
            )
            equity, turnover = p4.simulate_candidate(prices, scenario, slippage)
            equity = equity.loc[equity.index >= pd.Timestamp("2011-01-01")]
            turnover = turnover.reindex(equity.index).fillna(0.0)
            if slippage == 0.0010:
                base_equities[scenario.name] = equity
            rows.append(
                {
                    "scenario": scenario.name,
                    "one_bad_exposure": exposure,
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
        item["drawdown"] = drawdown_detail(equity)
        full = item["windows"]["full_2011_present"]
        oos = item["windows"]["oos_2023_present"]
        checks = p4.dominance(full, control_full)
        item["dominance_checks"] = checks
        item["strict_full_dominance"] = all(checks.values())
        item["oos_return_drawdown_dominance"] = (
            oos["cagr_pct"] > control_oos["cagr_pct"]
            and abs(oos["mdd_pct"]) <= abs(control_oos["mdd_pct"])
        )
        cost_passes = []
        for cost in ("0.0005", "0.0010", "0.0020"):
            ctrl = controls[cost]["windows"]["full_2011_present"]
            candidate = next(
                value
                for value in rows_by_cost[cost]
                if value["scenario"] == row["scenario"]
            )["windows"]["full_2011_present"]
            cost_passes.append(all(p4.dominance(candidate, ctrl).values()))
        item["cost_robust_full_dominance"] = all(cost_passes)
        selection.append(item)

    selection.sort(key=lambda value: value["selection_score"], reverse=True)
    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 5 Risk-Off Boundary",
        "end_date": min(prices.index[-1], control_equity.index[-1]).date().isoformat(),
        "control": {
            "strategy": "JDSS-3.2.2-RS6M-ONEWAY-HWM75",
            "windows": base_control,
            "drawdown": drawdown_detail(control_equity),
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
