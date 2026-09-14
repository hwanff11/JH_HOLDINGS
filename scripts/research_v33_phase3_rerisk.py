from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    AllocationState,
    V322Policy,
    apply_jdss_overlay,
    base_leverage,
    base_weights,
    build_qqq_features,
    build_rs_features,
    replay_targets,
    semiconductor_wins,
    trend_vote,
    virtual_active_series,
)
from jd_holdings.infrastructure.market_data import YFinanceDataSource


@dataclass(frozen=True)
class Scenario:
    name: str
    cadence: str
    vol_recover: float
    confirm_days: int
    restore_mode: str


CONTROL = Scenario("MONTHLY_CONTROL", "control", 0.0, 0, "control")
SCENARIOS = [CONTROL]
for cadence in ("daily", "weekly", "biweekly"):
    for vol in (0.20, 0.25):
        for confirm in (1, 3):
            for restore in ("step", "full"):
                name = f"RR_{cadence.upper()}_V{int(vol*100)}_C{confirm}_{restore.upper()}"
                SCENARIOS.append(Scenario(name, cadence, vol, confirm, restore))
SCENARIOS = tuple(SCENARIOS)

WINDOWS = {
    "train_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "oos_2023_present": ("2023-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2011_present": ("2011-01-01", None),
}
BIWEEKLY_ANCHOR = pd.Timestamp("2010-01-04")
LEVELS = (0.5, 1.0, 1.25, 1.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS V3.3 phase3 mid-month rerisk sweep")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/v33-phase3-rerisk.json")
    parser.add_argument("--output-md", default="reports/v33-phase3-rerisk.md")
    return parser.parse_args()


def load_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "v33-phase3-rerisk")
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
    raw = {symbol: source.daily(symbol, warmup_start, end.isoformat()) for symbol in symbols}
    sector_data = {name: raw[name] for name in (policy.rs_benchmark, "SMH") if name in raw}
    engine = StrategyBacktestEngine(config)
    slippage = float(config.backtest.default_slippage)
    virtual = {
        symbol: engine.run(
            symbol,
            raw[symbol],
            raw["SPY"],
            raw["QQQ"],
            start=strategy_start,
            end=end,
            slippage=slippage,
            sector_data=sector_data if symbol == "SOXL" else None,
        )
        for symbol in config.enabled_symbols
    }
    frames = {
        "QQQ": raw["QQQ"],
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        policy.rs_benchmark: raw[policy.rs_benchmark],
    }
    index = frames["QQQ"].index
    for symbol in ("TQQQ", "SOXL", policy.rs_benchmark):
        index = index.intersection(frames[symbol].index)
    active = {
        symbol: virtual_active_series(virtual[symbol], index)
        for symbol in config.enabled_symbols
    }
    return config, policy, strategy_start, frames, virtual, active


def cadence_key(timestamp: pd.Timestamp, cadence: str) -> str:
    if cadence == "daily":
        return timestamp.date().isoformat()
    if cadence == "weekly":
        iso = timestamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if cadence == "biweekly":
        block = (timestamp.normalize() - BIWEEKLY_ANCHOR).days // 14
        return f"B{block}"
    raise ValueError(cadence)


def next_step(current: float, target: float) -> float:
    for level in LEVELS:
        if level > current + 1e-12:
            return min(level, target)
    return current


def recovery_ok(row: pd.Series, policy: V322Policy, scenario: Scenario) -> bool:
    required = ("volatility", "sma_long", "ret_short", "ret_medium")
    if any(pd.isna(row.get(field)) for field in required):
        return False
    return (
        float(row["volatility"]) < scenario.vol_recover
        and float(row["close"]) > float(row["sma_long"])
        and float(row["ret_short"]) > 0
        and float(row["ret_medium"]) > 0
        and trend_vote(row, policy)
    )


def build_candidate_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
    scenario: Scenario,
) -> pd.DataFrame:
    if scenario.cadence == "control":
        return replay_targets(qqq_frame, semi_frame, active_tqqq, active_soxl, policy)

    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semi_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)
    state: AllocationState | None = None
    streak = 0
    last_key: str | None = None
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
            streak = 0
            last_key = cadence_key(timestamp, scenario.cadence)
        else:
            leverage = state.leverage
            volatility = qqq_row.get("volatility")
            if (
                not pd.isna(volatility)
                and float(volatility) >= policy.volatility_brake
                and leverage > policy.leverage_defensive
            ):
                leverage = policy.leverage_defensive
                streak = 0

            semi_active = state.semiconductor_active
            if semi_active and not semiconductor_wins(qqq_row, semi_row):
                semi_active = False

            target = base_leverage(qqq_row, policy)
            if leverage < target and recovery_ok(qqq_row, policy, scenario):
                streak += 1
            else:
                streak = 0

            key = cadence_key(timestamp, scenario.cadence)
            cadence_open = key != last_key
            if cadence_open and streak >= scenario.confirm_days and leverage < target:
                leverage = target if scenario.restore_mode == "full" else next_step(leverage, target)
                streak = 0
            last_key = key
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
        }
        row.update({symbol: float(weights.get(symbol, 0.0)) for symbol in ALLOCATION_SYMBOLS})
        rows.append(row)
    return pd.DataFrame(rows, index=index)


def run_with_targets(config, frames, virtual, targets, *, start, end, slippage):
    import jd_holdings.backtest.portfolio_engine as portfolio_module

    original = portfolio_module.replay_targets

    def target_override(*_args, **_kwargs):
        return targets

    portfolio_module.replay_targets = target_override
    try:
        return PortfolioBacktestEngine(config).run(
            frames, virtual, start=start, end=end, slippage=slippage
        )
    finally:
        portfolio_module.replay_targets = original


def window_metrics(result, start: str, end: str | None, annual_days: int) -> dict:
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end) if end else result.equity_curve.index[-1]
    view = result.equity_curve[(result.equity_curve.index >= lower) & (result.equity_curve.index <= upper)]
    initial, final = float(view.iloc[0]), float(view.iloc[-1])
    years = max((view.index[-1] - view.index[0]).days / 365.2425, 1 / 365.2425)
    cagr = (final / initial) ** (1 / years) - 1
    mdd = maximum_drawdown(view)
    sharpe, sortino = risk_adjusted_metrics(view, annual_days)
    trades = [t for t in result.trades if lower.date() <= pd.Timestamp(t["date"]).date() <= upper.date()]
    notional = sum(float(t["quantity"]) * float(t["price"]) for t in trades)
    turnover = notional / float(view.mean()) / years if float(view.mean()) > 0 else 0.0
    return {
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(cagr / abs(mdd), 3) if mdd else 0.0,
        "turnover_x_per_year": round(turnover, 3),
        "trade_fills": len(trades),
    }


def result_row(result, scenario: str, slippage: float, config) -> dict:
    return {
        "scenario": scenario,
        "slippage": slippage,
        "windows": {
            name: window_metrics(result, start, finish, config.backtest.annualization_days)
            for name, (start, finish) in WINDOWS.items()
        },
    }


def selection_score(row: dict, control: dict) -> float:
    score = 0.0
    for window, weight in (("train_2011_2018", 0.4), ("validation_2019_2022", 0.6)):
        c = row["windows"][window]
        b = control["windows"][window]
        score += weight * (
            0.45 * (c["cagr_pct"] - b["cagr_pct"])
            + 4.0 * (c["calmar"] - b["calmar"])
            + 1.5 * (c["sharpe"] - b["sharpe"])
            - 0.04 * max(0.0, abs(c["mdd_pct"]) - abs(b["mdd_pct"]))
        )
    return round(score, 4)


def render_markdown(payload: dict) -> str:
    rows = payload["base_cost_results"]
    ranked = payload["train_validation_selection"]
    lines = [
        "# JDSS V3.3 Phase 3 — Mid-month Re-Risk 전수검증",
        "",
        f"- Production control: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- 현행 월초 reset / daily risk-off / RS one-way 구조는 고정",
        "- 변경 변수: re-risk cadence × recovery volatility × confirmation × restore mode",
        f"- 총 후보: {len(rows)}개 (control 포함)",
        "",
        "## Train+Validation 상위 10개",
        "",
        "|순위|시나리오|점수|Full CAGR|Full MDD|Full Calmar|OOS CAGR|Stress MDD|",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(ranked[:10], 1):
        full = row["windows"]["full_2011_present"]
        oos = row["windows"]["oos_2023_present"]
        stress = row["windows"]["recent_stress_2022_present"]
        lines.append(
            f"|{rank}|{row['scenario']}|{row['selection_score']:+.4f}|"
            f"{full['cagr_pct']:+.2f}%|{full['mdd_pct']:.2f}%|{full['calmar']:.3f}|"
            f"{oos['cagr_pct']:+.2f}%|{stress['mdd_pct']:.2f}%|"
        )
    lines.extend(["", "## Full 전체 후보", ""])
    lines.extend([
        "|시나리오|CAGR|MDD|Sharpe|Calmar|Turnover/yr|",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in sorted(rows, key=lambda r: r["windows"]["full_2011_present"]["calmar"], reverse=True):
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
        s.name: build_candidate_targets(
            frames["QQQ"], frames[policy.rs_benchmark], active["TQQQ"], active["SOXL"], policy, s
        )
        for s in SCENARIOS
    }
    production = replay_targets(
        frames["QQQ"], frames[policy.rs_benchmark], active["TQQQ"], active["SOXL"], policy
    )
    pd.testing.assert_frame_equal(targets["MONTHLY_CONTROL"], production)

    results_by_cost: dict[str, list[dict]] = {}
    for slip in (0.0005, base_slip, 0.002):
        rows = []
        for s in SCENARIOS:
            result = run_with_targets(
                config, frames, virtual, targets[s.name], start=strategy_start, end=end, slippage=slip
            )
            rows.append(result_row(result, s.name, slip, config))
        results_by_cost[f"{slip:.4f}"] = rows

    base_rows = results_by_cost[f"{base_slip:.4f}"]
    control = next(r for r in base_rows if r["scenario"] == "MONTHLY_CONTROL")
    ranked = []
    for row in base_rows:
        copy = dict(row)
        copy["selection_score"] = selection_score(row, control)
        ranked.append(copy)
    ranked.sort(key=lambda r: r["selection_score"], reverse=True)

    payload = {
        "strategy_version": config.version,
        "end_date": end.isoformat(),
        "scenario_count": len(SCENARIOS),
        "base_cost_results": base_rows,
        "cost_stress": results_by_cost,
        "train_validation_selection": ranked,
    }
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(args.output_md).write_text(render_markdown(payload), encoding="utf-8")
    print(render_markdown(payload))


if __name__ == "__main__":
    main()
