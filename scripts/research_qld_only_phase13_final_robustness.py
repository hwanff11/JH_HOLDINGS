from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import research_qld_only_vol_phase12_neighborhood as p12
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4

BLOCKS = (3, 6, 12)
SAMPLES = 5000
SEED = 32213


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 13: final robustness of fixed VOL10/25 QLD-cash candidate"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-only-phase13.json")
    parser.add_argument("--output-md", default="reports/qld-only-phase13.md")
    return parser.parse_args()


def monthly_returns(equity: pd.Series) -> pd.Series:
    return equity.resample("ME").last().pct_change(fill_method=None).dropna()


def metrics_from_returns(returns: np.ndarray) -> tuple[float, float, float, float]:
    growth = np.cumprod(1.0 + returns)
    years = len(returns) / 12.0
    cagr = float(growth[-1] ** (1.0 / years) - 1.0)
    peaks = np.maximum.accumulate(growth)
    mdd = float(np.min(growth / peaks - 1.0))
    std = float(np.std(returns, ddof=0))
    sharpe = float(np.mean(returns) / std * np.sqrt(12.0)) if std > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    return cagr, mdd, sharpe, calmar


def block_indices(length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    starts = np.arange(max(length - block + 1, 1))
    selected: list[int] = []
    while len(selected) < length:
        start = int(rng.choice(starts))
        selected.extend(range(start, min(start + block, length)))
    return np.asarray(selected[:length], dtype=int)


def bootstrap(control: np.ndarray, candidate: np.ndarray, block: int) -> dict[str, float]:
    rng = np.random.default_rng(SEED + block)
    wins = np.zeros(5, dtype=int)
    cagr_delta = []
    mdd_improvement = []
    calmar_delta = []
    for _ in range(SAMPLES):
        idx = block_indices(len(control), block, rng)
        ctrl = metrics_from_returns(control[idx])
        cand = metrics_from_returns(candidate[idx])
        cagr_ok = cand[0] > ctrl[0]
        mdd_ok = abs(cand[1]) <= abs(ctrl[1])
        sharpe_ok = cand[2] >= ctrl[2]
        calmar_ok = cand[3] >= ctrl[3]
        strict = cagr_ok and mdd_ok and sharpe_ok and calmar_ok
        wins += np.asarray([cagr_ok, mdd_ok, sharpe_ok, calmar_ok, strict], dtype=int)
        cagr_delta.append((cand[0] - ctrl[0]) * 100.0)
        mdd_improvement.append((abs(ctrl[1]) - abs(cand[1])) * 100.0)
        calmar_delta.append(cand[3] - ctrl[3])
    return {
        "samples": SAMPLES,
        "block_months": block,
        "cagr_win_pct": round(float(wins[0]) / SAMPLES * 100.0, 1),
        "mdd_win_pct": round(float(wins[1]) / SAMPLES * 100.0, 1),
        "sharpe_win_pct": round(float(wins[2]) / SAMPLES * 100.0, 1),
        "calmar_win_pct": round(float(wins[3]) / SAMPLES * 100.0, 1),
        "strict_all4_win_pct": round(float(wins[4]) / SAMPLES * 100.0, 1),
        "median_cagr_delta_pctpt": round(float(np.median(cagr_delta)), 2),
        "p05_cagr_delta_pctpt": round(float(np.percentile(cagr_delta, 5)), 2),
        "median_mdd_improvement_pctpt": round(float(np.median(mdd_improvement)), 2),
        "p05_mdd_improvement_pctpt": round(float(np.percentile(mdd_improvement, 5)), 2),
        "median_calmar_delta": round(float(np.median(calmar_delta)), 3),
    }


def rolling_compare(candidate: pd.Series, control: pd.Series, years: int) -> dict[str, float]:
    common = candidate.index.intersection(control.index)
    candidate = candidate.loc[common]
    control = control.loc[common]
    cagr_deltas = []
    strict_wins = 0
    windows = 0
    for start_year in range(int(common[0].year), int(common[-1].year) - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        end = pd.Timestamp(f"{start_year + years - 1}-12-31")
        cand = candidate.loc[(candidate.index >= start) & (candidate.index <= end)]
        ctrl = control.loc[(control.index >= start) & (control.index <= end)]
        if len(cand) < 100 or len(ctrl) < 100:
            continue
        cm = p4.metrics(cand)
        vm = p4.metrics(ctrl)
        cagr_deltas.append(cm["cagr_pct"] - vm["cagr_pct"])
        strict = (
            cm["cagr_pct"] > vm["cagr_pct"]
            and abs(cm["mdd_pct"]) <= abs(vm["mdd_pct"])
            and cm["sharpe"] >= vm["sharpe"]
            and cm["calmar"] >= vm["calmar"]
        )
        strict_wins += int(strict)
        windows += 1
    return {
        "windows": windows,
        "cagr_beat_rate_pct": round(sum(x > 0 for x in cagr_deltas) / windows * 100.0, 1),
        "strict_all4_rate_pct": round(strict_wins / windows * 100.0, 1),
        "median_cagr_delta_pctpt": round(float(np.median(cagr_deltas)), 2),
        "worst_cagr_delta_pctpt": round(float(min(cagr_deltas)), 2),
    }


def render(payload: dict) -> str:
    cand = payload["candidate_common_2011"]
    ctrl = payload["v322_common_2011"]
    lines = [
        "# JDSS QLD-only Research — Phase 13 Final Robustness",
        "",
        "Fixed candidate: QLD 100% / QQQ 10d realized vol >=25% -> cash / month-end re-entry only",
        "",
        "|전략|CAGR|MDD|Sharpe|Calmar|",
        "|---|---:|---:|---:|---:|",
        f"|V3.2.2|{ctrl['cagr_pct']:+.2f}%|{ctrl['mdd_pct']:.2f}%|{ctrl['sharpe']:.3f}|{ctrl['calmar']:.3f}|",
        f"|QLD_VOL10_25_CASH|{cand['cagr_pct']:+.2f}%|{cand['mdd_pct']:.2f}%|{cand['sharpe']:.3f}|{cand['calmar']:.3f}|",
        "",
        "## Paired monthly block bootstrap",
        "",
        "|Block|CAGR 승률|MDD 승률|Sharpe 승률|Calmar 승률|4조건 동시|중앙 ΔCAGR|5% ΔCAGR|중앙 MDD개선|",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for block in BLOCKS:
        row = payload["bootstrap"][str(block)]
        lines.append(
            f"|{block}m|{row['cagr_win_pct']:.1f}%|{row['mdd_win_pct']:.1f}%|"
            f"{row['sharpe_win_pct']:.1f}%|{row['calmar_win_pct']:.1f}%|"
            f"{row['strict_all4_win_pct']:.1f}%|{row['median_cagr_delta_pctpt']:+.2f}%p|"
            f"{row['p05_cagr_delta_pctpt']:+.2f}%p|{row['median_mdd_improvement_pctpt']:+.2f}%p|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    frame = p12.build_frame(prices)
    scenario = p12.Scenario("VOL10_25_CASH", 10, 0.25)
    candidate, turnover = p12.simulate(frame, scenario, 0.0010)
    control, control_windows = p4.canonical_v322(end, 0.0010)

    common = candidate.index.intersection(control.index)
    common = common[common >= pd.Timestamp("2011-01-01")]
    candidate = candidate.loc[common]
    control = control.loc[common]
    candidate_metrics = p4.metrics(candidate, turnover.reindex(common))
    control_metrics = p4.metrics(control)

    candidate_monthly = monthly_returns(candidate)
    control_monthly = monthly_returns(control)
    months = candidate_monthly.index.intersection(control_monthly.index)
    cand_values = candidate_monthly.loc[months].to_numpy(dtype=float)
    ctrl_values = control_monthly.loc[months].to_numpy(dtype=float)

    payload = {
        "research": "JDSS QLD-only Phase 13 Final Robustness",
        "end_date": min(candidate.index[-1], control.index[-1]).date().isoformat(),
        "candidate_common_2011": candidate_metrics,
        "v322_common_2011": control_metrics,
        "v322_canonical_windows": control_windows,
        "rolling_3y_vs_v322": rolling_compare(candidate, control, 3),
        "rolling_5y_vs_v322": rolling_compare(candidate, control, 5),
        "bootstrap": {
            str(block): bootstrap(ctrl_values, cand_values, block) for block in BLOCKS
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
