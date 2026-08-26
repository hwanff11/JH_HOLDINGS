from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    V322Policy,
    replay_targets,
    virtual_active_series,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.research.initial_entry import (
    DEFAULT_SCENARIOS,
    leverage_breakdown,
    simulate_entry_window,
    summarize_scenarios,
)

HORIZONS = (21, 63, 126)
MIN_SIMILAR_SAMPLES = 30
BASELINE = "LUMP_100"
CURRENT = "CURRENT_50_75_100_3D"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS initial-entry scenario research")
    parser.add_argument(
        "--end",
        default="",
        help="YYYY-MM-DD; default latest completed XNYS session",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=5,
        help="historical launch-date stride in sessions",
    )
    parser.add_argument("--output-json", default="reports/initial-entry-research.json")
    parser.add_argument("--output-md", default="reports/initial-entry-research.md")
    return parser.parse_args()


def _prepare_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "initial-entry-research")
    end_text = end.isoformat()

    raw = {
        symbol: source.daily(symbol, warmup_start, end_text, refresh=False)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
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
    targets = replay_targets(
        raw["QQQ"].reindex(common),
        raw[policy.rs_benchmark].reindex(common),
        active["TQQQ"],
        active["SOXL"],
        policy,
    )
    frames = {symbol: raw[symbol] for symbol in ALLOCATION_SYMBOLS}
    return config, policy, strategy_start, frames, targets


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def _market_context(
    qqq: pd.DataFrame,
    targets: pd.DataFrame,
    timestamp: pd.Timestamp,
) -> dict[str, object]:
    closes = qqq.loc[:timestamp, "close"].dropna()
    if closes.empty:
        raise ValueError(f"QQQ context close missing: {timestamp}")
    close = float(closes.iloc[-1])
    sma200 = float(closes.tail(200).mean())
    high252 = float(closes.tail(252).max())
    drawdown_pct = (close / high252 - 1.0) * 100 if high252 > 0 else 0.0
    if drawdown_pct >= -5.0:
        high_bucket = "HIGH_0_5"
    elif drawdown_pct >= -10.0:
        high_bucket = "DD_5_10"
    else:
        high_bucket = "DD_10_PLUS"
    return {
        "context_date": timestamp.date().isoformat(),
        "prior_leverage": float(targets.loc[timestamp, "leverage"]),
        "qqq_above_sma200": bool(close >= sma200),
        "qqq_drawdown_252_high_pct": drawdown_pct,
        "qqq_high_bucket": high_bucket,
    }


def _subset_summary(rows: list[dict], dates: set[str] | None = None) -> list[dict]:
    if dates is None:
        selected = rows
    else:
        selected = [row for row in rows if row["start_date"] in dates]
    return summarize_scenarios(selected, horizons=HORIZONS) if selected else []


def _recent_rows(rows: list[dict], end: date, years: int = 4) -> tuple[list[dict], str]:
    cutoff = (pd.Timestamp(end) - pd.DateOffset(years=years)).date().isoformat()
    return [row for row in rows if row["start_date"] >= cutoff], cutoff


def _select_current_like_rows(
    rows: list[dict],
    latest_context: dict[str, object],
    *,
    minimum_samples: int = MIN_SIMILAR_SAMPLES,
) -> tuple[list[dict], str, int]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return [], "NO_DATA", 0
    leverage = float(latest_context["prior_leverage"])
    trend = bool(latest_context["qqq_above_sma200"])
    bucket = str(latest_context["qqq_high_bucket"])
    same_leverage = (frame["prior_leverage"].astype(float) - leverage).abs() < 1e-12
    same_trend = frame["context_qqq_above_sma200"].astype(bool) == trend
    same_bucket = frame["context_qqq_high_bucket"].astype(str) == bucket
    candidates = (
        ("same leverage + QQQ 200D trend + 252D-high bucket", same_leverage & same_trend & same_bucket),
        ("same leverage + QQQ 200D trend", same_leverage & same_trend),
        ("QQQ 200D trend + 252D-high bucket", same_trend & same_bucket),
        ("QQQ 200D trend", same_trend),
    )
    for index, (label, mask) in enumerate(candidates):
        selected = frame.loc[mask]
        samples = int(selected["start_date"].nunique())
        if samples >= minimum_samples or index == len(candidates) - 1:
            return selected.to_dict("records"), label, samples
    return [], "NO_MATCH", 0


def _worst_lump_comparison(rows: list[dict], count: int = 10) -> tuple[list[dict], list[dict]]:
    frame = pd.DataFrame(rows)
    lump = (
        frame[frame["scenario"] == BASELINE]
        .sort_values(["mdd_63", "return_63"], ascending=[True, True])
        .head(count)
        .set_index("start_date")
    )
    dates = set(lump.index.astype(str))
    current = frame[frame["scenario"] == CURRENT].set_index("start_date")
    comparisons: list[dict] = []
    for start_date, row in lump.iterrows():
        other = current.loc[start_date]
        comparisons.append(
            {
                "start_date": str(start_date),
                "lump_return_63_pct": float(row["return_63"] * 100),
                "lump_mdd_63_pct": float(row["mdd_63"] * 100),
                "current_return_63_pct": float(other["return_63"] * 100),
                "current_mdd_63_pct": float(other["mdd_63"] * 100),
                "current_edge_63_pctpt": float((other["return_63"] - row["return_63"]) * 100),
                "current_mdd_improvement_63_pctpt": float((other["mdd_63"] - row["mdd_63"]) * 100),
            }
        )
    return comparisons, _subset_summary(rows, dates)


def _append_summary_table(lines: list[str], title: str, summary: list[dict]) -> None:
    lines.extend(
        [
            "",
            title,
            "",
            "| 시나리오 | 21일 P10 | 63일 중앙수익 | 63일 P10 | 63일 중앙 MDD | "
            "63일 최악 MDD | 일시매수 승률 | 하락장 우위 | 상승장 기회비용 | 126일 중앙수익 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary:
        lines.append(
            f"| {row['scenario']} | {_fmt(row['p10_return_21_pct'])} | "
            f"{_fmt(row['median_return_63_pct'])} | {_fmt(row['p10_return_63_pct'])} | "
            f"{_fmt(row['median_mdd_63_pct'])} | {_fmt(row['worst_mdd_63_pct'])} | "
            f"{row['beat_lump_63_pct']:.1f}% | {_fmt(row['down_market_edge_63_pctpt'])} | "
            f"{_fmt(row['up_market_cost_63_pctpt'])} | {_fmt(row['median_return_126_pct'])} |"
        )


def _render_markdown(payload: dict) -> str:
    lines = [
        "# JDSS V3.2.2 2026-09-01 최초진입 의사결정 연구",
        "",
        f"- 전략: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        f"- 전체 시작점: `{payload['sample_start']} ~ {payload['sample_end']}`",
        f"- 시작점 간격: 미국 거래일 `{payload['stride']}`일마다",
        f"- 전체 시작점 수: `{payload['samples_per_scenario']}`개 / 시나리오",
        "- 평가기간: 21 / 63 / 126 미국 거래일",
        "- 공통 조건: V3.2.2 목표배분, HWM75, 매수·매도 수수료 0.1%, 슬리피지 0.1%",
        "- 모든 목표/체결수량은 정수 주(math.floor)이며 소수점 주식은 사용하지 않음",
        "- 차이는 최초진입 누적 투자비율과 단계 간격뿐이며 이후 전략 목표는 동일하게 추종",
    ]
    _append_summary_table(lines, "## 1. 전체기간 전수 비교", payload["summary"])
    lines.extend(
        [
            "",
            "### 지표 해석",
            "",
            "- `P10`: 시작점 중 하위 10% 수익률로, 높을수록 불리한 첫 진입 방어력이 좋습니다.",
            "- `최악 MDD`: 가장 불리했던 시작점의 낙폭입니다.",
            "- `하락장 우위`: 같은 시작일 LUMP_100이 손실인 경우 평균적으로 얼마나 덜 잃었는지입니다.",
            "- `상승장 기회비용`: 같은 시작일 LUMP_100이 수익인 경우 평균적으로 얼마나 덜 벌었는지입니다.",
        ]
    )
    _append_summary_table(
        lines,
        f"## 2. 최근 4년 비교 (시작일 {payload['recent_4y_cutoff']} 이후)",
        payload["recent_4y_summary"],
    )
    _append_summary_table(
        lines,
        "## 3. LUMP_100의 63일 MDD 최악 10개 시작점에서 비교",
        payload["worst10_summary"],
    )
    lines.extend(
        [
            "",
            "### 최악 10개 시작일에서 현행 50→75→100% 비교",
            "",
            "| 시작일 | LUMP 63일 수익 | LUMP MDD | 현행 63일 수익 | 현행 MDD | 수익 차이 | MDD 개선 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["worst10_starts"]:
        lines.append(
            f"| {row['start_date']} | {_fmt(row['lump_return_63_pct'])} | "
            f"{_fmt(row['lump_mdd_63_pct'])} | {_fmt(row['current_return_63_pct'])} | "
            f"{_fmt(row['current_mdd_63_pct'])} | {_fmt(row['current_edge_63_pctpt'])} | "
            f"{_fmt(row['current_mdd_improvement_63_pctpt'])} |"
        )
    latest = payload["latest_context"]
    lines.extend(
        [
            "",
            "## 4. 현재 시장과 유사한 과거 시작점",
            "",
            f"- 기준일: `{latest['context_date']}`",
            f"- V3.2.2 직전 목표 레버리지: `{latest['prior_leverage']:.2f}x`",
            f"- QQQ 200일선 위: `{'YES' if latest['qqq_above_sma200'] else 'NO'}`",
            f"- QQQ 252거래일 고점 대비: `{latest['qqq_drawdown_252_high_pct']:+.2f}%`",
            f"- 고점거리 bucket: `{latest['qqq_high_bucket']}`",
            f"- 사용한 유사조건: `{payload['similar_criteria']}`",
            f"- 과거 유사 시작점 수: `{payload['similar_samples']}`개 / 시나리오",
        ]
    )
    _append_summary_table(lines, "### 유사환경 시나리오 비교", payload["similar_summary"])
    lines.extend(
        [
            "",
            "## 5. 현행안(50 → 75 → 100%, 3거래일)의 시작 레버리지별 비교",
            "",
            "| 시작 레버리지 | 표본 | 63일 LUMP 대비 중앙 차이 | LUMP 승률 | LUMP 손실구간 평균 방어 |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["current_by_leverage"]:
        lines.append(
            f"| {row['leverage']:.2f}x | {row['samples']} | {_fmt(row['median_edge_pctpt'])} | "
            f"{row['beat_lump_pct']:.1f}% | {_fmt(row['down_market_edge_pctpt'])} |"
        )
    lines.extend(
        [
            "",
            "## 6. 최종 선택 원칙",
            "",
            "이번 연구는 장기 V3.2.2 전략을 재최적화하는 작업이 아니라 첫 계좌 진입의 경로위험만 비교합니다.",
            "최종 선택은 ① 21/63일 P10과 최악 MDD, ② LUMP 최악 10구간 방어, "
            "③ 최근 4년과 현재 유사환경 일관성, ④ 상승장 기회비용, ⑤ 운영 단순성 순으로 판단합니다.",
            "근소한 수익률 차이만으로 더 복잡한 분할안을 채택하지 않습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = _parse_args()
    if args.stride < 1:
        raise ValueError("--stride must be >= 1")
    clock = MarketClock()
    end = pd.Timestamp(args.end).date() if args.end else clock.latest_completed_session()
    config, policy, strategy_start, frames, targets = _prepare_history(end)

    start_position = max(1, int(targets.index.searchsorted(pd.Timestamp(strategy_start))))
    last_start = len(targets.index) - max(HORIZONS)
    positions = list(range(start_position, last_start + 1, args.stride))
    if not positions:
        raise RuntimeError("분석 가능한 최초진입 시작점이 없습니다")

    rows: list[dict] = []
    for position in positions:
        prior_ts = targets.index[position - 1]
        context = _market_context(frames["QQQ"], targets, prior_ts)
        for scenario in DEFAULT_SCENARIOS:
            result = simulate_entry_window(
                frames,
                targets,
                start_position=position,
                scenario=scenario,
                horizons=HORIZONS,
                initial_capital=float(policy.initial_capital),
                hwm_reinvestment_fraction=float(policy.hwm_reinvestment_fraction),
                buy_fee=float(config.global_.buy_fee),
                sell_fee=float(config.global_.sell_fee),
                slippage=float(config.backtest.default_slippage),
            )
            result["scenario"] = scenario.name
            result["context_qqq_above_sma200"] = context["qqq_above_sma200"]
            result["context_qqq_high_bucket"] = context["qqq_high_bucket"]
            result["context_qqq_drawdown_252_high_pct"] = context["qqq_drawdown_252_high_pct"]
            rows.append(result)

    summary = summarize_scenarios(rows, horizons=HORIZONS)
    recent_rows, recent_cutoff = _recent_rows(rows, end)
    recent_summary = summarize_scenarios(recent_rows, horizons=HORIZONS)
    worst10_starts, worst10_summary = _worst_lump_comparison(rows)
    latest_context = _market_context(frames["QQQ"], targets, targets.index[-1])
    similar_rows, similar_criteria, similar_samples = _select_current_like_rows(
        rows, latest_context
    )
    similar_summary = summarize_scenarios(similar_rows, horizons=HORIZONS)
    current_by_leverage = leverage_breakdown(
        rows,
        scenario_name=CURRENT,
        horizon=63,
    )
    payload = {
        "strategy_version": config.version,
        "config_version": config.config_version,
        "end_date": end.isoformat(),
        "sample_start": targets.index[positions[0]].date().isoformat(),
        "sample_end": targets.index[positions[-1]].date().isoformat(),
        "stride": args.stride,
        "samples_per_scenario": len(positions),
        "scenarios": [
            {
                "name": item.name,
                "fractions": list(item.fractions),
                "interval_sessions": item.interval_sessions,
            }
            for item in DEFAULT_SCENARIOS
        ],
        "summary": summary,
        "recent_4y_cutoff": recent_cutoff,
        "recent_4y_samples_per_scenario": len({row["start_date"] for row in recent_rows}),
        "recent_4y_summary": recent_summary,
        "worst10_starts": worst10_starts,
        "worst10_summary": worst10_summary,
        "latest_context": latest_context,
        "similar_criteria": similar_criteria,
        "similar_samples": similar_samples,
        "similar_summary": similar_summary,
        "current_by_leverage": current_by_leverage,
    }

    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
