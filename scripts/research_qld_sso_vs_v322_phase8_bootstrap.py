from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_vs_v322 as p4
import research_qld_sso_vs_v322_phase7 as p7

WEIGHTS = (0.40, 0.50, 0.60)
BLOCK_LENGTHS = (3, 6, 12)
SAMPLES = 3000
SEED = 32280


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 8: paired monthly block bootstrap of V3.2.2/QLD blends"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322-phase8.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322-phase8.md")
    return parser.parse_args()


def monthly_returns(equity: pd.Series) -> pd.Series:
    month_end = equity.resample("ME").last()
    return month_end.pct_change().dropna()


def path_metrics(returns: np.ndarray) -> tuple[float, float, float, float]:
    if len(returns) == 0:
        return 0.0, 0.0, 0.0, 0.0
    growth = np.cumprod(1.0 + returns)
    years = len(returns) / 12.0
    cagr = growth[-1] ** (1.0 / years) - 1.0
    peaks = np.maximum.accumulate(growth)
    mdd = float(np.min(growth / peaks - 1.0))
    std = float(np.std(returns, ddof=0))
    sharpe = float(np.mean(returns) / std * np.sqrt(12.0)) if std > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    return cagr, mdd, sharpe, calmar


def paired_block_sample(
    length: int,
    block_length: int,
    rng: np.random.Generator,
) -> np.ndarray:
    starts = np.arange(0, max(length - block_length + 1, 1))
    selected: list[int] = []
    while len(selected) < length:
        start = int(rng.choice(starts))
        selected.extend(range(start, min(start + block_length, length)))
    return np.asarray(selected[:length], dtype=int)


def bootstrap_compare(
    control_returns: np.ndarray,
    candidate_returns: np.ndarray,
    block_length: int,
) -> dict[str, float]:
    if len(control_returns) != len(candidate_returns):
        raise ValueError("paired bootstrap requires equal return lengths")
    rng = np.random.default_rng(SEED + block_length)
    cagr_wins = 0
    mdd_wins = 0
    sharpe_wins = 0
    calmar_wins = 0
    strict_wins = 0
    cagr_deltas = []
    mdd_deltas = []
    calmar_deltas = []

    for _ in range(SAMPLES):
        idx = paired_block_sample(len(control_returns), block_length, rng)
        ctrl = path_metrics(control_returns[idx])
        cand = path_metrics(candidate_returns[idx])
        cagr_ok = cand[0] > ctrl[0]
        mdd_ok = abs(cand[1]) <= abs(ctrl[1])
        sharpe_ok = cand[2] >= ctrl[2]
        calmar_ok = cand[3] >= ctrl[3]
        cagr_wins += int(cagr_ok)
        mdd_wins += int(mdd_ok)
        sharpe_wins += int(sharpe_ok)
        calmar_wins += int(calmar_ok)
        strict_wins += int(cagr_ok and mdd_ok and sharpe_ok and calmar_ok)
        cagr_deltas.append((cand[0] - ctrl[0]) * 100.0)
        mdd_deltas.append((abs(ctrl[1]) - abs(cand[1])) * 100.0)
        calmar_deltas.append(cand[3] - ctrl[3])

    def pct(value: int) -> float:
        return round(value / SAMPLES * 100.0, 1)

    return {
        "samples": SAMPLES,
        "block_length_months": block_length,
        "cagr_win_pct": pct(cagr_wins),
        "mdd_win_pct": pct(mdd_wins),
        "sharpe_win_pct": pct(sharpe_wins),
        "calmar_win_pct": pct(calmar_wins),
        "strict_all4_win_pct": pct(strict_wins),
        "median_cagr_delta_pctpt": round(float(np.median(cagr_deltas)), 2),
        "p05_cagr_delta_pctpt": round(float(np.percentile(cagr_deltas, 5)), 2),
        "median_mdd_improvement_pctpt": round(float(np.median(mdd_deltas)), 2),
        "p05_mdd_improvement_pctpt": round(float(np.percentile(mdd_deltas, 5)), 2),
        "median_calmar_delta": round(float(np.median(calmar_deltas)), 3),
    }


def render(payload: dict) -> str:
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 8 Paired Block Bootstrap",
        "",
        "- 월별 수익률을 동일 블록 순서로 paired resampling하여 V3.2.2와 혼합후보를 비교",
        "- block length: 3 / 6 / 12개월, 각 3,000회, 고정 seed",
        "- 이는 역사적 경로 순서 민감도 검증이며 새로운 독립 OOS 데이터의 대체물은 아님",
        "",
        "|QLD sleeve|Block|CAGR 승률|MDD 승률|Sharpe 승률|Calmar 승률|4조건 동시|중앙 ΔCAGR|5% ΔCAGR|중앙 MDD개선|",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for weight in WEIGHTS:
        key = f"{weight:.2f}"
        for block in BLOCK_LENGTHS:
            row = payload["bootstrap"][key][str(block)]
            lines.append(
                f"|{weight:.0%}|{block}m|{row['cagr_win_pct']:.1f}%|"
                f"{row['mdd_win_pct']:.1f}%|{row['sharpe_win_pct']:.1f}%|"
                f"{row['calmar_win_pct']:.1f}%|{row['strict_all4_win_pct']:.1f}%|"
                f"{row['median_cagr_delta_pctpt']:+.2f}%p|"
                f"{row['p05_cagr_delta_pctpt']:+.2f}%p|"
                f"{row['median_mdd_improvement_pctpt']:+.2f}%p|"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)
    control_equity, _ = p4.canonical_v322(end, 0.0010)
    qld_scenario = p4.Scenario("QLD_STEP_50_0", "never", 0.50, 0.0)
    qld_equity, _ = p4.simulate_candidate(prices, qld_scenario, 0.0010)
    qld_equity = qld_equity.loc[qld_equity.index >= pd.Timestamp("2011-01-01")]

    bootstrap: dict[str, dict[str, dict[str, float]]] = {}
    historical: dict[str, dict[str, float]] = {}
    for weight in WEIGHTS:
        blend, _ = p7.monthly_blend(control_equity, qld_equity, weight, 0.0010)
        common = control_equity.index.intersection(blend.index)
        common = common[common >= pd.Timestamp("2011-01-01")]
        control_monthly = monthly_returns(control_equity.loc[common])
        blend_monthly = monthly_returns(blend.loc[common])
        months = control_monthly.index.intersection(blend_monthly.index)
        control_values = control_monthly.loc[months].to_numpy(dtype=float)
        blend_values = blend_monthly.loc[months].to_numpy(dtype=float)
        bootstrap[f"{weight:.2f}"] = {
            str(block): bootstrap_compare(control_values, blend_values, block)
            for block in BLOCK_LENGTHS
        }
        historical[f"{weight:.2f}"] = {
            "months": len(months),
            "control_cagr_pct": round(path_metrics(control_values)[0] * 100.0, 2),
            "blend_cagr_pct": round(path_metrics(blend_values)[0] * 100.0, 2),
        }

    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 8 Paired Block Bootstrap",
        "end_date": min(prices.index[-1], control_equity.index[-1]).date().isoformat(),
        "weights": list(WEIGHTS),
        "block_lengths_months": list(BLOCK_LENGTHS),
        "samples_per_test": SAMPLES,
        "historical_monthly": historical,
        "bootstrap": bootstrap,
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
