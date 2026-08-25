from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import research_qld_sso_alpha as p1
import research_qld_sso_alpha_phase3 as p3

from jd_holdings.backtest.runner import run_production_backtest
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ANNUAL_DAYS = 252
WINDOWS = {
    "train_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "oos_2023_present": ("2023-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2011_present": ("2011-01-01", None),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    switch_rule: str
    one_bad_exposure: float
    both_bad_exposure: float


SCENARIOS = (
    Scenario("QLD_TRENDVOL_F50", "never", 0.50, 0.50),
    Scenario("SSO_DIVERGENCE_F50", "divergence", 0.50, 0.50),
    Scenario("QLD_STEP_50_25", "never", 0.50, 0.25),
    Scenario("SSO_DIVERGENCE_STEP_50_25", "divergence", 0.50, 0.25),
    Scenario("QLD_STEP_50_0", "never", 0.50, 0.00),
    Scenario("SSO_DIVERGENCE_STEP_50_0", "divergence", 0.50, 0.00),
    Scenario("QLD_ANY_F25", "never", 0.25, 0.25),
    Scenario("QLD_ANY_CASH", "never", 0.00, 0.00),
    Scenario("QLD_TREND_CASH_VOL50", "never", 0.50, 0.00),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare QLD/SSO alpha candidates directly with production V3.2.2"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-v322.json")
    parser.add_argument("--output-md", default="reports/qld-sso-v322.md")
    return parser.parse_args()


def build_targets(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    weights = pd.DataFrame(np.nan, index=frame.index, columns=["QLD", "SSO"])
    month_end = p1.rebalance_mask(frame.index, "monthly")
    current_asset: str | None = None
    exposure = 0.0
    required = (
        "QQQ_r63",
        "SPY_r63",
        "QQQ_r126",
        "SPY_r126",
        "QQQ_sma200",
        "SPY_sma200",
        "QQQ_vol20",
        "SPY_vol20",
    )
    for timestamp, is_month_end in month_end.items():
        row = frame.loc[timestamp]
        if any(pd.isna(row[field]) for field in required):
            continue
        if bool(is_month_end) or current_asset is None:
            current_asset = p3.choose_asset(row, scenario.switch_rule)
            exposure = 1.0

        base = "QQQ" if current_asset == "QLD" else "SPY"
        trend_bad = bool(row[base] <= row[f"{base}_sma200"])
        vol_bad = bool(row[f"{base}_vol20"] >= 0.30)
        if trend_bad and vol_bad:
            exposure = min(exposure, scenario.both_bad_exposure)
        elif trend_bad or vol_bad:
            exposure = min(exposure, scenario.one_bad_exposure)

        weights.loc[timestamp] = (
            (exposure, 0.0) if current_asset == "QLD" else (0.0, exposure)
        )
    return weights.ffill()


def simulate_candidate(
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


def metrics(
    equity: pd.Series,
    turnover: pd.Series | None = None,
) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {
            "cagr_pct": 0.0,
            "mdd_pct": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "turnover_x_per_year": 0.0,
        }
    years = max(
        (equity.index[-1] - equity.index[0]).days / 365.2425,
        1 / 365.2425,
    )
    cagr = (float(equity.iloc[-1]) / float(equity.iloc[0])) ** (1 / years) - 1.0
    drawdown = equity / equity.cummax() - 1.0
    mdd = float(drawdown.min())
    returns = equity.pct_change().dropna()
    std = float(returns.std(ddof=0))
    sharpe = float(returns.mean() / std * np.sqrt(ANNUAL_DAYS)) if std > 0 else 0.0
    annual_turnover = 0.0
    if turnover is not None:
        selected = turnover.reindex(equity.index).fillna(0.0)
        annual_turnover = float(selected.sum() / years)
    return {
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(cagr / abs(mdd), 3) if mdd < 0 else 0.0,
        "turnover_x_per_year": round(annual_turnover, 3),
    }


def window_metrics(
    equity: pd.Series,
    turnover: pd.Series | None = None,
) -> dict[str, dict[str, float]]:
    output = {}
    for name, (start, end) in WINDOWS.items():
        view = equity.loc[equity.index >= pd.Timestamp(start)]
        if end is not None:
            view = view.loc[view.index <= pd.Timestamp(end)]
        selected_turnover = turnover.reindex(view.index) if turnover is not None else None
        output[name] = metrics(view, selected_turnover)
    return output


def annual_returns(equity: pd.Series) -> dict[str, float]:
    output: dict[str, float] = {}
    for year, view in equity.groupby(equity.index.year):
        if len(view) < 2:
            continue
        output[str(year)] = round(
            (float(view.iloc[-1]) / float(view.iloc[0]) - 1.0) * 100,
            2,
        )
    return output


def rolling_vs_control(
    candidate: pd.Series,
    control: pd.Series,
    years: int,
) -> dict[str, float]:
    common = candidate.index.intersection(control.index)
    common = common[common >= pd.Timestamp("2011-01-01")]
    candidate = candidate.loc[common]
    control = control.loc[common]
    deltas = []
    first = int(common[0].year)
    last = int(common[-1].year)
    for start_year in range(first, last - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        end = pd.Timestamp(f"{start_year + years - 1}-12-31")
        cand_view = candidate.loc[(candidate.index >= start) & (candidate.index <= end)]
        ctrl_view = control.loc[(control.index >= start) & (control.index <= end)]
        if len(cand_view) < 100 or len(ctrl_view) < 100:
            continue
        deltas.append(metrics(cand_view)["cagr_pct"] - metrics(ctrl_view)["cagr_pct"])
    return {
        "windows": len(deltas),
        "beat_rate_pct": (
            round(sum(value > 0 for value in deltas) / len(deltas) * 100, 1)
            if deltas
            else 0.0
        ),
        "median_cagr_delta_pctpt": (
            round(float(pd.Series(deltas).median()), 2) if deltas else 0.0
        ),
        "worst_cagr_delta_pctpt": round(min(deltas), 2) if deltas else 0.0,
    }


def canonical_v322(
    end: date,
    slippage: float,
) -> tuple[pd.Series, dict[str, dict[str, float]]]:
    config = load_config()
    source = YFinanceDataSource(Path(".cache") / "qld-sso-v322-control")
    run = run_production_backtest(
        config,
        source,
        symbols=config.enabled_symbols,
        start=config.backtest.default_start,
        end=end.isoformat(),
        slippage=slippage,
    )
    if run.portfolio is None:
        raise RuntimeError("V3.2.2 portfolio result is missing")
    equity = run.portfolio.equity_curve.copy()
    return equity, window_metrics(equity)


def dominance(candidate: dict[str, float], control: dict[str, float]) -> dict[str, bool]:
    return {
        "cagr": candidate["cagr_pct"] > control["cagr_pct"],
        "mdd": abs(candidate["mdd_pct"]) <= abs(control["mdd_pct"]),
        "sharpe": candidate["sharpe"] >= control["sharpe"],
        "calmar": candidate["calmar"] >= control["calmar"],
    }


def render(payload: dict) -> str:
    control = payload["control"]["windows"]["full_2011_present"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 4 vs V3.2.2",
        "",
        f"- 공통 비교기간: 2011-01-01 ~ {payload['end_date']}",
        "- V3.2.2는 production canonical backtest engine 그대로 실행",
        "- QLD/SSO 신호는 종가 계산 후 다음 거래일부터 반영",
        "- 후보는 월초/월말 정규 reset + 월중 one-way risk-off 구조",
        "",
        "## Production Control",
        "",
        "|전략|CAGR|MDD|Sharpe|Calmar|",
        "|---|---:|---:|---:|---:|",
        f"|V3.2.2|{control['cagr_pct']:+.2f}%|{control['mdd_pct']:.2f}%|"
        f"{control['sharpe']:.3f}|{control['calmar']:.3f}|",
        "",
        "## 후보 비교 — 기본 슬리피지 0.10%",
        "",
        "|후보|CAGR|MDD|Sharpe|Calmar|OOS CAGR|OOS MDD|3Y 승률|5Y 승률|Strict Dominance|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["selection"]:
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            f"|{row['scenario']}|{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|"
            f"{full['sharpe']:.3f}|{full['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|"
            f"{oos['mdd_pct']:.2f}%|{row['rolling_3y']['beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y']['beat_rate_pct']:.1f}%|"
            f"{'YES' if row['strict_dominance'] else 'NO'}|"
        )
    lines.extend(
        [
            "",
            "## 주요 연도 수익률",
            "",
            "|전략|2018|2020|2022|2023|2024|2025|2026 YTD|",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    names = ["V3.2.2"] + [row["scenario"] for row in payload["selection"][:4]]
    annual = {"V3.2.2": payload["control"]["annual_returns_pct"]}
    annual.update(
        {row["scenario"]: row["annual_returns_pct"] for row in payload["selection"][:4]}
    )
    for name in names:
        values = annual[name]
        cells = [values.get(str(year), 0.0) for year in (2018, 2020, 2022, 2023, 2024, 2025, 2026)]
        lines.append("|" + name + "|" + "|".join(f"{value:+.2f}%" for value in cells) + "|")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = p1.load_prices(end)

    controls: dict[str, dict] = {}
    candidate_costs: dict[str, list[dict]] = {}
    base_equities: dict[str, pd.Series] = {}
    base_turnovers: dict[str, pd.Series] = {}
    control_equity: pd.Series | None = None

    for slippage in (0.0005, 0.0010, 0.0020):
        v322_equity, v322_windows = canonical_v322(end, slippage)
        controls[f"{slippage:.4f}"] = {"windows": v322_windows}
        if slippage == 0.0010:
            control_equity = v322_equity

        rows = []
        for scenario in SCENARIOS:
            equity, turnover = simulate_candidate(prices, scenario, slippage)
            equity = equity.loc[equity.index >= pd.Timestamp("2011-01-01")]
            turnover = turnover.reindex(equity.index).fillna(0.0)
            if slippage == 0.0010:
                base_equities[scenario.name] = equity
                base_turnovers[scenario.name] = turnover
            rows.append(
                {
                    "scenario": scenario.name,
                    "switch_rule": scenario.switch_rule,
                    "one_bad_exposure": scenario.one_bad_exposure,
                    "both_bad_exposure": scenario.both_bad_exposure,
                    "windows": window_metrics(equity, turnover),
                }
            )
        candidate_costs[f"{slippage:.4f}"] = rows

    if control_equity is None:
        raise RuntimeError("base V3.2.2 control was not generated")
    base_control = controls["0.0010"]["windows"]
    control_full = base_control["full_2011_present"]

    selection = []
    for row in candidate_costs["0.0010"]:
        item = json.loads(json.dumps(row))
        equity = base_equities[row["scenario"]]
        item["rolling_3y"] = rolling_vs_control(equity, control_equity, 3)
        item["rolling_5y"] = rolling_vs_control(equity, control_equity, 5)
        item["annual_returns_pct"] = annual_returns(equity)
        checks = dominance(item["windows"]["full_2011_present"], control_full)
        item["dominance_checks"] = checks
        item["strict_dominance"] = all(checks.values())
        train = item["windows"]["train_2011_2018"]
        validation = item["windows"]["validation_2019_2022"]
        ctrl_train = base_control["train_2011_2018"]
        ctrl_validation = base_control["validation_2019_2022"]
        item["selection_score"] = round(
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
        selection.append(item)
    selection.sort(key=lambda value: value["selection_score"], reverse=True)

    control_annual = annual_returns(control_equity)
    payload = {
        "research": "JDSS QLD/SSO Alpha Phase 4 vs V3.2.2",
        "end_date": min(prices.index[-1], control_equity.index[-1]).date().isoformat(),
        "control": {
            "strategy": "JDSS-3.2.2-RS6M-ONEWAY-HWM75",
            "windows": base_control,
            "annual_returns_pct": control_annual,
        },
        "selection": selection,
        "cost_stress": {
            "control": controls,
            "candidates": candidate_costs,
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
