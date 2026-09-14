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
    Scenario("BIWEEKLY", "biweekly", False),
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

BIWEEKLY_ANCHOR = pd.Timestamp("2010-01-04")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JDSS V3.3 rebalance-frequency research"
    )
    parser.add_argument("--end", default="")
    parser.add_argument(
        "--output-json",
        default="reports/v33-rebalance-frequency.json",
    )
    parser.add_argument(
        "--output-md",
        default="reports/v33-rebalance-frequency.md",
    )
    return parser.parse_args()


def load_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "v33-rebalance-frequency")
    symbols = ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
    raw = {
        symbol: source.daily(symbol, warmup_start, end.isoformat())
        for symbol in symbols
    }
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
    return config, policy, strategy_start, frames, virtual, active, index


def period_key(timestamp: pd.Timestamp, scenario: Scenario) -> str:
    if scenario.reset == "daily":
        return timestamp.date().isoformat()
    if scenario.reset == "weekly":
        iso = timestamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if scenario.reset == "biweekly":
        block = (timestamp.normalize() - BIWEEKLY_ANCHOR).days // 14
        return f"B{block}"
    raise ValueError(f"unsupported reset: {scenario.reset}")


def apply_daily_risk_off(
    state: AllocationState,
    qqq_row: pd.Series,
    semi_row: pd.Series,
    policy: V322Policy,
) -> AllocationState:
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
    return AllocationState(state.month, leverage, semi_active)


def build_candidate_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
    scenario: Scenario,
) -> pd.DataFrame:
    qqq = build_qqq_features(qqq_frame, policy)
    semi = build_rs_features(semi_frame, policy)
    index = qqq.index.intersection(semi.index)
    index = index.intersection(active_tqqq.index).intersection(active_soxl.index)
    state: AllocationState | None = None
    current_key: str | None = None
    rows: list[dict] = []

    for timestamp in index:
        qqq_row = qqq.loc[timestamp]
        semi_row = semi.loc[timestamp]
        key = period_key(timestamp, scenario)
        if state is None or key != current_key:
            state = AllocationState(
                month=key,
                leverage=base_leverage(qqq_row, policy),
                semiconductor_active=semiconductor_wins(qqq_row, semi_row),
            )
            current_key = key
        elif scenario.daily_risk_off:
            state = apply_daily_risk_off(state, qqq_row, semi_row, policy)

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
        row.update(
            {
                symbol: float(weights.get(symbol, 0.0))
                for symbol in ALLOCATION_SYMBOLS
            }
        )
        rows.append(row)

    if not rows:
        raise RuntimeError(f"no targets for {scenario.name}")
    return pd.DataFrame(rows, index=index)


def build_targets(
    qqq_frame: pd.DataFrame,
    semi_frame: pd.DataFrame,
    active_tqqq: pd.Series,
    active_soxl: pd.Series,
    policy: V322Policy,
    scenario: Scenario,
) -> pd.DataFrame:
    if scenario.name == "MONTHLY_CONTROL":
        return replay_targets(
            qqq_frame,
            semi_frame,
            active_tqqq,
            active_soxl,
            policy,
        )
    return build_candidate_targets(
        qqq_frame,
        semi_frame,
        active_tqqq,
        active_soxl,
        policy,
        scenario,
    )


def run_with_targets(
    config,
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


def window_metrics(result, start: str, end: str | None, annual_days: int) -> dict:
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end) if end else result.equity_curve.index[-1]
    equity = result.equity_curve
    view = equity[(equity.index >= lower) & (equity.index <= upper)]
    if len(view) < 2:
        raise RuntimeError(f"insufficient equity data: {start}~{end}")
    initial = float(view.iloc[0])
    final = float(view.iloc[-1])
    years = max(
        (view.index[-1] - view.index[0]).days / 365.2425,
        1 / 365.2425,
    )
    cagr = (final / initial) ** (1 / years) - 1
    mdd = maximum_drawdown(view)
    sharpe, sortino = risk_adjusted_metrics(view, annual_days)
    trades = [
        trade
        for trade in result.trades
        if lower.date() <= pd.Timestamp(trade["date"]).date() <= upper.date()
    ]
    notional = sum(
        float(trade["quantity"]) * float(trade["price"])
        for trade in trades
    )
    mean_equity = float(view.mean())
    turnover = notional / mean_equity / years if mean_equity > 0 else 0.0
    fees = sum(float(trade["fee"]) for trade in trades)
    calmar = cagr / abs(mdd) if mdd else 0.0
    return {
        "start": view.index[0].date().isoformat(),
        "end": view.index[-1].date().isoformat(),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "trade_fills": len(trades),
        "turnover_x_per_year": round(turnover, 3),
        "fees_usd": round(fees, 2),
    }


def selection_score(row: dict, control: dict) -> float:
    score = 0.0
    weights = (("train_2011_2018", 0.4), ("validation_2019_2022", 0.6))
    for window, weight in weights:
        candidate = row["windows"][window]
        baseline = control["windows"][window]
        delta_cagr = candidate["cagr_pct"] - baseline["cagr_pct"]
        delta_calmar = candidate["calmar"] - baseline["calmar"]
        delta_sharpe = candidate["sharpe"] - baseline["sharpe"]
        extra_mdd = max(
            0.0,
            abs(candidate["mdd_pct"]) - abs(baseline["mdd_pct"]),
        )
        score += weight * (
            0.45 * delta_cagr
            + 4.0 * delta_calmar
            + 1.5 * delta_sharpe
            - 0.04 * extra_mdd
        )
    return round(score, 4)


def result_row(result, scenario: str, slippage: float, config) -> dict:
    return {
        "scenario": scenario,
        "slippage": slippage,
        "windows": {
            name: window_metrics(
                result,
                start,
                finish,
                config.backtest.annualization_days,
            )
            for name, (start, finish) in WINDOWS.items()
        },
    }


def report_table(rows: list[dict], window: str) -> list[str]:
    lines = [
        "| 시나리오 | CAGR | MDD | Sharpe | Sortino | Calmar | Turnover/yr |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        metric = row["windows"][window]
        lines.append(
            "| {scenario} | {cagr:+.2f}% | {mdd:.2f}% | {sharpe:.3f} | "
            "{sortino:.3f} | {calmar:.3f} | {turnover:.2f}x |".format(
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
    selected = payload["train_validation_selection"]
    control = payload["baseline"]
    full_control = control["windows"]["full_2011_present"]
    lines = [
        "# JDSS V3.3 리밸런싱 주기 전수검증",
        "",
        f"- Production control: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- 변경 변수: 정규 allocation 재평가 주기",
        "- 고정 변수: SMA50/200, momentum 21/63/126, Vol20/30%, RS126",
        "- 고정 변수: HWM75, 5% overlay, 50→75→100 onboarding, 비용/체결 엔진",
        "- 후보 선택: Train+Validation만 사용; OOS는 선택 후 확인",
        "",
        "## Full 결과 - 기본 슬리피지 0.10%",
        "",
    ]
    lines.extend(report_table(rows, "full_2011_present"))
    lines.extend(["", "## 기간별 결과", ""])
    for window in WINDOWS:
        lines.extend([f"### {window}", ""])
        lines.extend(report_table(rows, window))
        lines.append("")

    lines.extend(
        [
            "## Train + Validation 사전 선택",
            "",
            "| 순위 | 시나리오 | 선택점수 | OOS CAGR | OOS MDD | OOS Calmar |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for rank, row in enumerate(selected, 1):
        oos = row["windows"]["oos_2023_present"]
        lines.append(
            "| {rank} | {scenario} | {score:+.4f} | {cagr:+.2f}% | "
            "{mdd:.2f}% | {calmar:.3f} |".format(
                rank=rank,
                scenario=row["scenario"],
                score=row["selection_score"],
                cagr=oos["cagr_pct"],
                mdd=oos["mdd_pct"],
                calmar=oos["calmar"],
            )
        )

    lines.extend(
        [
            "",
            "## 비용 스트레스 - Full",
            "",
            "| 슬리피지 | 시나리오 | CAGR | MDD | Calmar | Turnover/yr |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["cost_stress"]:
        metric = row["windows"]["full_2011_present"]
        lines.append(
            "| {slip:.2f}% | {scenario} | {cagr:+.2f}% | {mdd:.2f}% | "
            "{calmar:.3f} | {turnover:.2f}x |".format(
                slip=row["slippage"] * 100,
                scenario=row["scenario"],
                cagr=metric["cagr_pct"],
                mdd=metric["mdd_pct"],
                calmar=metric["calmar"],
                turnover=metric["turnover_x_per_year"],
            )
        )

    lines.extend(
        [
            "",
            "## Control 참고",
            "",
            (
                f"- MONTHLY_CONTROL Full CAGR {full_control['cagr_pct']:+.2f}% / "
                f"MDD {full_control['mdd_pct']:.2f}% / "
                f"Calmar {full_control['calmar']:.3f}"
            ),
            "- CAGR 단독 1등은 채택하지 않습니다.",
            "- MDD/Calmar/OOS/비용 스트레스의 방향성이 함께 개선돼야 합니다.",
            "- OOS를 확인한 연구 승자는 즉시 채택하지 않고 최대 SHADOW입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    clock = MarketClock()
    end = pd.Timestamp(args.end).date() if args.end else clock.latest_completed_session()
    (
        config,
        policy,
        strategy_start,
        frames,
        virtual,
        active,
        index,
    ) = load_history(end)
    qqq = frames["QQQ"].reindex(index)
    semi = frames[policy.rs_benchmark].reindex(index)
    targets = {
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

    production_targets = replay_targets(
        qqq,
        semi,
        active["TQQQ"],
        active["SOXL"],
        policy,
    )
    pd.testing.assert_frame_equal(
        targets["MONTHLY_CONTROL"],
        production_targets,
        check_exact=True,
    )

    base_slippage = float(config.backtest.default_slippage)
    slippages = (0.0005, base_slippage, 0.0020)
    runs = {}
    for slippage in slippages:
        for scenario in SCENARIOS:
            runs[(scenario.name, slippage)] = run_with_targets(
                config,
                frames,
                virtual,
                targets[scenario.name],
                start=strategy_start,
                end=end,
                slippage=slippage,
            )

    base_rows = [
        result_row(
            runs[(scenario.name, base_slippage)],
            scenario.name,
            base_slippage,
            config,
        )
        for scenario in SCENARIOS
    ]
    control = next(
        row for row in base_rows if row["scenario"] == "MONTHLY_CONTROL"
    )
    for row in base_rows:
        row["selection_score"] = selection_score(row, control)
    selected = sorted(
        base_rows,
        key=lambda row: row["selection_score"],
        reverse=True,
    )

    stress_rows = [
        result_row(
            runs[(scenario.name, slippage)],
            scenario.name,
            slippage,
            config,
        )
        for slippage in slippages
        for scenario in SCENARIOS
    ]
    payload = {
        "research_id": "V3.3-REBALANCE-FREQUENCY-SWEEP",
        "strategy_version": config.version,
        "config_version": config.config_version,
        "end_date": end.isoformat(),
        "base_slippage": base_slippage,
        "scenarios": [scenario.__dict__ for scenario in SCENARIOS],
        "windows": WINDOWS,
        "baseline_reproduction": "PASS",
        "baseline": control,
        "base_cost_results": base_rows,
        "train_validation_selection": selected,
        "cost_stress": stress_rows,
    }

    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
