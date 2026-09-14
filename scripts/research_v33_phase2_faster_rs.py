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
    virtual_active_series,
)
from jd_holdings.infrastructure.market_data import YFinanceDataSource


@dataclass(frozen=True)
class Scenario:
    name: str
    rs_reentry: str


SCENARIOS = (
    Scenario("MONTHLY_CONTROL", "control"),
    Scenario("RS_DAILY_REENTRY", "daily"),
    Scenario("RS_WEEKLY_REENTRY", "weekly"),
    Scenario("RS_BIWEEKLY_REENTRY", "biweekly"),
)

WINDOWS = {
    "train_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "oos_2023_present": ("2023-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2011_present": ("2011-01-01", None),
}

BIWEEKLY_ANCHOR = pd.Timestamp("2010-01-04")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS V3.3 phase2 faster-RS research")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/v33-phase2-faster-rs.json")
    parser.add_argument("--output-md", default="reports/v33-phase2-faster-rs.md")
    return parser.parse_args()


def load_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "v33-phase2-faster-rs")
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
    raw = {symbol: source.daily(symbol, warmup_start, end.isoformat()) for symbol in symbols}
    sector_data = {
        name: raw[name]
        for name in (policy.rs_benchmark, "SMH")
        if name in raw
    }
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


def reentry_key(timestamp: pd.Timestamp, mode: str) -> str:
    if mode == "daily":
        return timestamp.date().isoformat()
    if mode == "weekly":
        iso = timestamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if mode == "biweekly":
        block = (timestamp.normalize() - BIWEEKLY_ANCHOR).days // 14
        return f"B{block}"
    raise ValueError(f"unsupported RS reentry mode: {mode}")


def build_candidate_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
    scenario: Scenario,
) -> pd.DataFrame:
    if scenario.rs_reentry == "control":
        return replay_targets(qqq_frame, semi_frame, active_tqqq, active_soxl, policy)

    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semi_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)
    state: AllocationState | None = None
    current_reentry_key: str | None = None
    rows: list[dict] = []

    for timestamp in index:
        qqq_row = qqq.loc[timestamp]
        semi_row = semi.loc[timestamp]
        month = str(timestamp.to_period("M"))
        month_changed = state is None or state.month != month

        if month_changed:
            state = AllocationState(
                month=month,
                leverage=base_leverage(qqq_row, policy),
                semiconductor_active=semiconductor_wins(qqq_row, semi_row),
            )
            current_reentry_key = reentry_key(timestamp, scenario.rs_reentry)
        else:
            leverage = state.leverage
            volatility = qqq_row.get("volatility")
            if (
                not pd.isna(volatility)
                and float(volatility) >= policy.volatility_brake
                and leverage > policy.leverage_defensive
            ):
                leverage = policy.leverage_defensive

            semi_active = state.semiconductor_active
            wins = semiconductor_wins(qqq_row, semi_row)
            if semi_active and not wins:
                semi_active = False

            key = reentry_key(timestamp, scenario.rs_reentry)
            if not semi_active and key != current_reentry_key and wins:
                semi_active = True
            current_reentry_key = key
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

    if not rows:
        raise RuntimeError(f"no targets for {scenario.name}")
    return pd.DataFrame(rows, index=index)


def run_with_targets(config, frames, virtual, targets, *, start, end, slippage):
    import jd_holdings.backtest.portfolio_engine as portfolio_module

    original = portfolio_module.replay_targets

    def target_override(*_args, **_kwargs):
        return targets

    portfolio_module.replay_targets = target_override
    try:
        return PortfolioBacktestEngine(config).run(
            frames,
            virtual,
            start=start,
            end=end,
            slippage=slippage,
        )
    finally:
        portfolio_module.replay_targets = original


def window_metrics(result, start: str, end: str | None, annual_days: int) -> dict:
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end) if end else result.equity_curve.index[-1]
    view = result.equity_curve[(result.equity_curve.index >= lower) & (result.equity_curve.index <= upper)]
    if len(view) < 2:
        raise RuntimeError(f"insufficient equity data: {start}~{end}")
    initial = float(view.iloc[0])
    final = float(view.iloc[-1])
    years = max((view.index[-1] - view.index[0]).days / 365.2425, 1 / 365.2425)
    cagr = (final / initial) ** (1 / years) - 1
    mdd = maximum_drawdown(view)
    sharpe, sortino = risk_adjusted_metrics(view, annual_days)
    trades = [
        trade
        for trade in result.trades
        if lower.date() <= pd.Timestamp(trade["date"]).date() <= upper.date()
    ]
    notional = sum(float(trade["quantity"]) * float(trade["price"]) for trade in trades)
    turnover = notional / float(view.mean()) / years if float(view.mean()) > 0 else 0.0
    fees = sum(float(trade["fee"]) for trade in trades)
    calmar = cagr / abs(mdd) if mdd else 0.0
    return {
        "start": view.index[0].date().isoformat(),
        "end": view.index[-1].date().isoformat(),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "trade_fills": len(trades),
        "turnover_x_per_year": round(turnover, 3),
        "fees_usd": round(fees, 2),
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
        candidate = row["windows"][window]
        baseline = control["windows"][window]
        score += weight * (
            0.45 * (candidate["cagr_pct"] - baseline["cagr_pct"])
            + 4.0 * (candidate["calmar"] - baseline["calmar"])
            + 1.5 * (candidate["sharpe"] - baseline["sharpe"])
            - 0.04 * max(0.0, abs(candidate["mdd_pct"]) - abs(baseline["mdd_pct"]))
        )
    return round(score, 4)


def table(rows: list[dict], window: str) -> list[str]:
    lines = [
        "| 시나리오 | CAGR | MDD | Sharpe | Sortino | Calmar | Turnover/yr |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        metric = row["windows"][window]
        lines.append(
            "| {scenario} | {cagr:+.2f}% | {mdd:.2f}% | {sharpe:.3f} | {sortino:.3f} | "
            "{calmar:.3f} | {turnover:.2f}x |".format(
                scenario=row["scenario"],
                cagr=metric["cagr_pct"],
                mdd=metric["mdd_pct"],
                sharpe=metric["sharpe"],
                sortino=metric["sortino"],
                calmar=metric["calmar"],
                turnover=metric["turnover_x_per_year"],
            )
        )
    return lines


def render_markdown(payload: dict) -> str:
    rows = payload["base_cost_results"]
    lines = [
        "# JDSS V3.3 Phase 2 — Monthly Core + Faster RS",
        "",
        f"- Production control: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- 레버리지: 현행 월초 full reset + 월중 volatility one-way risk-off 유지",
        "- SOXL 이탈: 현행처럼 매일 RS 상실 시 즉시 TQQQ로 risk-off",
        "- 변경 변수: SOXL 재진입 허용 주기만 Daily / Weekly / Biweekly로 단축",
        "- 후보 선택: Train+Validation만 사용하고 OOS는 선택 후 확인",
        "",
    ]
    for window in WINDOWS:
        lines.extend([f"## {window}", ""])
        lines.extend(table(rows, window))
        lines.append("")
    lines.extend(["## Train + Validation 사전 순위", ""])
    for rank, row in enumerate(payload["train_validation_selection"], 1):
        lines.append(f"{rank}. `{row['scenario']}` — score {row['selection_score']:+.4f}")
    lines.extend(["", "## 판정 원칙", ""])
    lines.append("- Faster RS가 Full CAGR만 높이고 2022 stress MDD/Calmar를 훼손하면 채택하지 않습니다.")
    lines.append("- 비용 스트레스 0.05% / 0.10% / 0.20%에서 방향이 유지되어야 합니다.")
    lines.append("- OOS를 확인한 후보는 즉시 production 반영하지 않고 SHADOW 후보로만 분류합니다.")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    config, policy, strategy_start, frames, virtual, active = load_history(end)
    base_slippage = float(config.backtest.default_slippage)
    slips = (0.0005, base_slippage, 0.002)

    target_map = {
        scenario.name: build_candidate_targets(
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
    pd.testing.assert_frame_equal(target_map["MONTHLY_CONTROL"], production)

    results_by_cost: dict[str, list[dict]] = {}
    for slip in slips:
        rows = []
        for scenario in SCENARIOS:
            result = run_with_targets(
                config,
                frames,
                virtual,
                target_map[scenario.name],
                start=strategy_start,
                end=end,
                slippage=slip,
            )
            rows.append(result_row(result, scenario.name, slip, config))
        results_by_cost[f"{slip:.4f}"] = rows

    base_rows = results_by_cost[f"{base_slippage:.4f}"]
    control = next(row for row in base_rows if row["scenario"] == "MONTHLY_CONTROL")
    ranked = []
    for row in base_rows:
        item = dict(row)
        item["selection_score"] = selection_score(row, control)
        ranked.append(item)
    ranked.sort(key=lambda item: item["selection_score"], reverse=True)

    payload = {
        "strategy_version": config.version,
        "config_version": config.config_version,
        "end_date": max(frame.index[-1] for frame in frames.values()).date().isoformat(),
        "base_slippage": base_slippage,
        "base_cost_results": base_rows,
        "cost_stress_results": results_by_cost,
        "train_validation_selection": ranked,
        "baseline_reproduction": True,
    }
    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
