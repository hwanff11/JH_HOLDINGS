from __future__ import annotations

import argparse
import json
import math
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
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource


@dataclass(frozen=True)
class Scenario:
    name: str
    reset: str
    daily_risk_off: bool


SCENARIOS = (
    Scenario("DAILY", "daily", False),
    Scenario("WEEKLY", "weekly", False),
    Scenario("BIWEEKLY_10D", "biweekly_10d", False),
    Scenario("MONTHLY_CONTROL", "monthly_control", True),
    Scenario("WEEKLY_DAILY_RISK_OFF", "weekly", True),
)

WINDOWS = {
    "train_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "oos_2023_present": ("2023-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full_2011_present": ("2011-01-01", None),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS V3.3 rebalance-frequency research")
    parser.add_argument("--end", default="", help="YYYY-MM-DD; default latest completed XNYS session")
    parser.add_argument("--output-json", default="reports/v33-rebalance-frequency.json")
    parser.add_argument("--output-md", default="reports/v33-rebalance-frequency.md")
    return parser.parse_args()


def prepare_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "v33-rebalance-frequency")
    end_text = end.isoformat()
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
    raw = {
        symbol: source.daily(symbol, warmup_start, end_text, refresh=False)
        for symbol in symbols
    }
    sector_data = {
        symbol: raw[symbol]
        for symbol in (policy.rs_benchmark, "SMH")
        if symbol in raw
    }
    engine = StrategyBacktestEngine(config)
    virtual = {
        symbol: engine.run(
            symbol,
            raw[symbol],
            raw["SPY"],
            raw["QQQ"],
            start=strategy_start,
            end=end,
            slippage=float(config.backtest.default_slippage),
            sector_data=sector_data if symbol == "SOXL" else None,
        )
        for symbol in config.enabled_symbols
    }
    common = raw["QQQ"].index
    for symbol in ("TQQQ", "SOXL", policy.rs_benchmark):
        common = common.intersection(raw[symbol].index)
    active = {
        symbol: virtual_active_series(virtual[symbol], common)
        for symbol in config.enabled_symbols
    }
    frames = {
        "QQQ": raw["QQQ"],
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        policy.rs_benchmark: raw[policy.rs_benchmark],
    }
    return config, policy, strategy_start, frames, virtual, active, common


def reset_due(
    scenario: Scenario,
    position: int,
    timestamp: pd.Timestamp,
    previous_timestamp: pd.Timestamp | None,
) -> bool:
    if position == 0:
        return True
    if scenario.reset == "daily":
        return True
    if scenario.reset == "biweekly_10d":
        return position % 10 == 0
    if scenario.reset == "weekly":
        assert previous_timestamp is not None
        current_iso = timestamp.isocalendar()
        previous_iso = previous_timestamp.isocalendar()
        return (current_iso.year, current_iso.week) != (previous_iso.year, previous_iso.week)
    raise ValueError(f"unsupported reset mode: {scenario.reset}")


def build_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
    scenario: Scenario,
) -> pd.DataFrame:
    if scenario.name == "MONTHLY_CONTROL":
        return replay_targets(qqq_frame, semi_frame, active_tqqq, active_soxl, policy)

    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semi_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)
    state: AllocationState | None = None
    rows: list[dict] = []
    previous_timestamp: pd.Timestamp | None = None

    for position, timestamp in enumerate(index):
        qqq_row = qqq.loc[timestamp]
        semi_row = semi.loc[timestamp]
        if state is None or reset_due(scenario, position, timestamp, previous_timestamp):
            state = AllocationState(
                month=f"{scenario.name}:{position}",
                leverage=base_leverage(qqq_row, policy),
                semiconductor_active=semiconductor_wins(qqq_row, semi_row),
            )
        elif scenario.daily_risk_off:
            leverage = state.leverage
            volatility = qqq_row.get("volatility")
            if (
                not pd.isna(volatility)
                and float(volatility) >= policy.volatility_brake
                and leverage > policy.leverage_defensive
            ):
                leverage = policy.leverage_defensive
            semi_active = state.semiconductor_active
            if semi_active and not semiconductor_wins(qqq_row, semi_row):
                semi_active = False
            state = AllocationState(state.month, leverage, semi_active)

        weights = apply_jdss_overlay(
            base_weights(state, policy),
            active_tqqq=bool(active_tqqq.loc[timestamp]),
            active_soxl=bool(active_soxl.loc[timestamp]),
            policy=policy,
        )
        rows.append(
            {
                "trade_date": timestamp.date().isoformat(),
                "leverage": state.leverage,
                "semiconductor_active": state.semiconductor_active,
                "jdss_tqqq_active": bool(active_tqqq.loc[timestamp]),
                "jdss_soxl_active": bool(active_soxl.loc[timestamp]),
                **{
                    symbol: float(weights.get(symbol, 0.0))
                    for symbol in ALLOCATION_SYMBOLS
                },
            }
        )
        previous_timestamp = timestamp

    if not rows:
        raise RuntimeError(f"no targets for {scenario.name}")
    return pd.DataFrame(rows, index=index)


def run_with_targets(
    config,
    policy,
    frames,
    virtual,
    targets: pd.DataFrame,
    *,
    start: date,
    end: date,
    slippage: float,
):
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


def slice_metrics(result, start: str, end: str | None, annualization_days: int) -> dict:
    equity = result.equity_curve
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end) if end else equity.index[-1]
    view = equity[(equity.index >= lower) & (equity.index <= upper)]
    if len(view) < 2:
        return {}
    initial = float(view.iloc[0])
    final = float(view.iloc[-1])
    years = max((view.index[-1] - view.index[0]).days / 365.2425, 1 / 365.2425)
    cagr = (final / initial) ** (1 / years) - 1 if initial > 0 and final > 0 else -1.0
    mdd = maximum_drawdown(view)
    sharpe, sortino = risk_adjusted_metrics(view, annualization_days)
    dated_trades = [
        trade
        for trade in result.trades
        if lower.date() <= pd.Timestamp(trade["date"]).date() <= upper.date()
    ]
    notional = sum(float(t["quantity"]) * float(t["price"]) for t in dated_trades)
    mean_equity = float(view.mean())
    turnover_per_year = notional / mean_equity / years if mean_equity > 0 else 0.0
    fees = sum(float(t["fee"]) for t in dated_trades)
    return {
        "start": view.index[0].date().isoformat(),
        "end": view.index[-1].date().isoformat(),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(cagr / abs(mdd), 3) if mdd else 0.0,
        "trade_fills": len(dated_trades),
        "turnover_x_per_year": round(turnover_per_year, 3),
        "fees_usd": round(fees, 2),
    }


def score_candidate(row: dict, baseline: dict) -> float:
    # Train/validation selection score only. OOS is intentionally excluded.
    score = 0.0
    for window, weight in (("train_2011_2018", 0.4), ("validation_2019_2022", 0.6)):
        candidate = row["windows"][window]
        control = baseline["windows"][window]
        score += weight * (
            0.45 * (candidate["cagr_pct"] - control["cagr_pct"])
            + 4.0 * (candidate["calmar"] - control["calmar"])
            + 1.5 * (candidate["sharpe"] - control["sharpe"])
            - 0.04 * max(0.0, abs(candidate["mdd_pct"]) - abs(control["mdd_pct"]))
        )
    return round(score, 4)


def render_markdown(payload: dict) -> str:
    baseline = payload["baseline"]
    selected = payload["train_validation_selection"]
    lines = [
        "# JDSS V3.3 리밸런싱 주기 전수검증",
        "",
        f"- Production control: `{payload['strategy_version']}` / config `{payload['config_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- 변경 변수: 정규 allocation 재평가 주기만 변경",
        "- 동일 유지: 50/200 SMA, 21/63/126 momentum, 20d/30% vol brake 기준, RS126, HWM75, 5% JDSS overlay, 50→75→100 onboarding, 수수료/체결 방식",
        "- 체결: 신호일 종가 계산 → 다음 거래일 시가 리밸런싱 (production engine)",
        "- 후보 선정은 Train+Validation만 사용하며 OOS(2023~)는 선택 후 확인",
        "",
        "## 시나리오 정의",
        "",
        "| 시나리오 | 정규 재평가 | 기간 중 risk-off |",
        "|---|---|---|",
        "| DAILY | 매 거래일 | 해당 없음(매일 full reset) |",
        "| WEEKLY | 매주 첫 거래일 | 없음 |",
        "| BIWEEKLY_10D | 매 10거래일 | 없음 |",
        "| MONTHLY_CONTROL | 월 첫 거래일 | 현행: vol brake + SOXL RS 이탈 one-way |",
        "| WEEKLY_DAILY_RISK_OFF | 매주 첫 거래일 | 매일: vol brake + SOXL RS 이탈 one-way |",
        "",
        "## 기본 비용(슬리피지 0.10%) Full 결과",
        "",
        "| 시나리오 | CAGR | MDD | Sharpe | Calmar | 연환산 Turnover | Fills |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["base_cost_results"]:
        m = row["windows"]["full_2011_present"]
        lines.append(
            f"| {row['scenario']} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | "
            f"{m['sharpe']:.3f} | {m['calmar']:.3f} | {m['turnover_x_per_year']:.2f}x | {m['trade_fills']} |"
        )

    lines += ["", "## 기간별 CAGR / MDD", ""]
    for window in WINDOWS:
        lines += [
            f"### {window}",
            "",
            "| 시나리오 | CAGR | MDD | Sharpe | Calmar |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in payload["base_cost_results"]:
            m = row["windows"][window]
            lines.append(
                f"| {row['scenario']} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | "
                f"{m['sharpe']:.3f} | {m['calmar']:.3f} |"
            )
        lines.append("")

    lines += [
        "## Train + Validation 기준 사전 선택",
        "",
        f"- 1위: **{selected[0]['scenario']}** (selection score {selected[0]['selection_score']:+.4f})",
        f"- Control: MONTHLY_CONTROL full CAGR {baseline['windows']['full_2011_present']['cagr_pct']:+.2f}% / MDD {baseline['windows']['full_2011_present']['mdd_pct']:.2f}%",
        "- 아래 OOS 수치는 후보 선택에 사용하지 않았으며, 선택 후 독립 확인용입니다.",
        "",
        "| 순위 | 시나리오 | 선택점수 | OOS CAGR | OOS MDD | OOS Calmar | Recent CAGR |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(selected, 1):
        oos = row["windows"]["oos_2023_present"]
        recent = row["windows"]["recent_stress_2022_present"]
        lines.append(
            f"| {rank} | {row['scenario']} | {row['selection_score']:+.4f} | "
            f"{oos['cagr_pct']:+.2f}% | {oos['mdd_pct']:.2f}% | {oos['calmar']:.3f} | "
            f"{recent['cagr_pct']:+.2f}% |"
        )

    lines += [
        "",
        "## 비용 스트레스",
        "",
        "| 슬리피지 | 시나리오 | Full CAGR | Full MDD | Calmar | Turnover/yr |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for stress in payload["cost_stress"]:
        m = stress["windows"]["full_2011_present"]
        lines.append(
            f"| {stress['slippage'] * 100:.2f}% | {stress['scenario']} | {m['cagr_pct']:+.2f}% | "
            f"{m['mdd_pct']:.2f}% | {m['calmar']:.3f} | {m['turnover_x_per_year']:.2f}x |"
        )

    lines += [
        "",
        "## 판정 원칙",
        "",
        "- CAGR 단독 1등은 채택하지 않습니다.",
        "- Train/Validation과 OOS에서 방향이 일관되고 MDD/Calmar가 악화되지 않으며 비용 스트레스에서도 우위가 유지되어야 합니다.",
        "- OOS를 이미 확인한 이번 승자는 즉시 production 채택이 아니라 최대 `SHADOW` 후보입니다.",
        "- production 변경은 별도 구현 PR과 사용자 승인 후 진행합니다.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    clock = MarketClock()
    end = pd.Timestamp(args.end).date() if args.end else clock.latest_completed_session()
    config, policy, strategy_start, frames, virtual, active, common = prepare_history(end)
    qqq = frames["QQQ"].reindex(common)
    semi = frames[policy.rs_benchmark].reindex(common)

    targets_by_name = {
        scenario.name: build_targets(
            qqq,
            semi,
            active["TQQQ"],
            active["SOXL"],
            policy,
            scenario,
        )
        for scenario in SCENARIOS
    }

    # Baseline reproduction: research control must be byte-equivalent in targets
    # to the production replay before any candidate is evaluated.
    production_targets = replay_targets(
        qqq,
        semi,
        active["TQQQ"],
        active["SOXL"],
        policy,
    )
    pd.testing.assert_frame_equal(
        targets_by_name["MONTHLY_CONTROL"],
        production_targets,
        check_exact=True,
    )

    base_slippage = float(config.backtest.default_slippage)
    slippages = (0.0005, base_slippage, 0.0020)
    all_runs: dict[tuple[str, float], object] = {}
    for slippage in slippages:
        for scenario in SCENARIOS:
            all_runs[(scenario.name, slippage)] = run_with_targets(
                config,
                policy,
                frames,
                virtual,
                targets_by_name[scenario.name],
                start=strategy_start,
                end=end,
                slippage=slippage,
            )

    base_rows: list[dict] = []
    for scenario in SCENARIOS:
        result = all_runs[(scenario.name, base_slippage)]
        row = {
            "scenario": scenario.name,
            "slippage": base_slippage,
            "windows": {
                name: slice_metrics(result, start, finish, config.backtest.annualization_days)
                for name, (start, finish) in WINDOWS.items()
            },
        }
        base_rows.append(row)

    baseline = next(row for row in base_rows if row["scenario"] == "MONTHLY_CONTROL")
    for row in base_rows:
        row["selection_score"] = score_candidate(row, baseline)
    selected = sorted(base_rows, key=lambda row: row["selection_score"], reverse=True)

    stress_rows: list[dict] = []
    for slippage in slippages:
        for scenario in SCENARIOS:
            result = all_runs[(scenario.name, slippage)]
            stress_rows.append(
                {
                    "scenario": scenario.name,
                    "slippage": slippage,
                    "windows": {
                        name: slice_metrics(result, start, finish, config.backtest.annualization_days)
                        for name, (start, finish) in WINDOWS.items()
                    },
                }
            )

    payload = {
        "research_id": "V3.3-REBALANCE-FREQUENCY-SWEEP",
        "strategy_version": config.version,
        "config_version": config.config_version,
        "end_date": end.isoformat(),
        "base_slippage": base_slippage,
        "scenarios": [scenario.__dict__ for scenario in SCENARIOS],
        "windows": WINDOWS,
        "baseline_reproduction": "PASS",
        "baseline": baseline,
        "base_cost_results": base_rows,
        "train_validation_selection": selected,
        "cost_stress": stress_rows,
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
