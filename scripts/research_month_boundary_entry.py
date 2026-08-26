from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.research.initial_entry import EntryScenario
from scripts.research_initial_entry_scenarios import _market_context, _prepare_history

HORIZONS = (21, 63, 126)
CURRENT = EntryScenario("CURRENT_50_75_100_3D", (0.50, 0.75, 1.0), 3)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS month-boundary first-entry research")
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/month-boundary-entry.json")
    parser.add_argument("--output-md", default="reports/month-boundary-entry.md")
    return parser.parse_args()


def _maximum_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _weights(row: pd.Series) -> dict[str, float]:
    return {symbol: float(row[symbol]) for symbol in ALLOCATION_SYMBOLS}


def simulate_month_start(
    frames: dict[str, pd.DataFrame],
    targets: pd.DataFrame,
    *,
    start_position: int,
    initial_wait_sessions: int,
    initial_capital: float,
    hwm_reinvestment_fraction: float,
    buy_fee: float,
    sell_fee: float,
    slippage: float,
) -> dict[str, Any]:
    """Compare starting on month session 1 vs waiting for its close/reset.

    Both variants are measured from the same first-session date and therefore
    share the same 21/63/126-session endpoints.  When waiting one session, the
    account stays in cash on session 1 and begins the normal 50→75→100 onboarding
    on session 2 using session-1's completed target.
    """

    if initial_wait_sessions not in (0, 1):
        raise ValueError("initial_wait_sessions must be 0 or 1")
    max_horizon = max(HORIZONS)
    index = targets.index
    if start_position < 1 or start_position + max_horizon > len(index):
        raise ValueError("insufficient history around month start")

    cash = initial_capital
    high_water = initial_capital
    quantities = {symbol: 0 for symbol in ALLOCATION_SYMBOLS}
    pending = _weights(targets.loc[index[start_position - 1]])
    equity_values: list[float] = []
    trade_count = 0
    entry_date: str | None = None

    for offset, timestamp in enumerate(index[start_position : start_position + max_horizon]):
        opens = {
            symbol: float(frames[symbol].loc[timestamp, "open"])
            for symbol in ALLOCATION_SYMBOLS
        }
        closes = {
            symbol: float(frames[symbol].loc[timestamp, "close"])
            for symbol in ALLOCATION_SYMBOLS
        }
        open_equity = cash + sum(
            quantities[symbol] * opens[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        sizing_equity = initial_capital + hwm_reinvestment_fraction * max(
            0.0, high_water - initial_capital
        )
        sizing_equity = max(0.0, min(sizing_equity, open_equity))

        if offset < initial_wait_sessions:
            fraction = 0.0
        else:
            onboarding_offset = offset - initial_wait_sessions
            fraction = CURRENT.fraction_for_offset(onboarding_offset)

        desired: dict[str, int] = {}
        for symbol in ALLOCATION_SYMBOLS:
            buy_price = opens[symbol] * (1 + slippage)
            full_target = math.floor(
                sizing_equity
                * pending.get(symbol, 0.0)
                / (buy_price * (1 + buy_fee))
            )
            desired[symbol] = math.floor(full_target * fraction)

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference >= 0:
                continue
            quantity = -difference
            price = opens[symbol] * (1 - slippage)
            fee = quantity * price * sell_fee
            cash += quantity * price - fee
            quantities[symbol] -= quantity
            trade_count += 1

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference <= 0:
                continue
            price = opens[symbol] * (1 + slippage)
            affordable = math.floor(cash / (price * (1 + buy_fee)))
            quantity = min(difference, affordable)
            if quantity <= 0:
                continue
            fee = quantity * price * buy_fee
            cash -= quantity * price + fee
            quantities[symbol] += quantity
            trade_count += 1
            if entry_date is None:
                entry_date = timestamp.date().isoformat()

        liquidation = sum(
            quantities[symbol] * closes[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        high_water = max(high_water, equity)
        pending = _weights(targets.loc[timestamp])

    output: dict[str, Any] = {
        "month_start": index[start_position].date().isoformat(),
        "entry_date": entry_date,
        "trade_count": trade_count,
    }
    for horizon in HORIZONS:
        segment = equity_values[:horizon]
        output[f"return_{horizon}"] = segment[-1] / initial_capital - 1.0
        output[f"mdd_{horizon}"] = _maximum_drawdown(segment)
    return output


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"samples": 0}
    frame = pd.DataFrame(rows)
    result: dict[str, Any] = {"samples": int(len(frame))}
    for horizon in HORIZONS:
        first = frame[f"first_return_{horizon}"] * 100
        wait = frame[f"wait_return_{horizon}"] * 100
        first_mdd = frame[f"first_mdd_{horizon}"] * 100
        wait_mdd = frame[f"wait_mdd_{horizon}"] * 100
        edge = wait - first
        result.update(
            {
                f"first_median_return_{horizon}_pct": float(first.median()),
                f"wait_median_return_{horizon}_pct": float(wait.median()),
                f"first_p10_return_{horizon}_pct": float(first.quantile(0.10)),
                f"wait_p10_return_{horizon}_pct": float(wait.quantile(0.10)),
                f"first_median_mdd_{horizon}_pct": float(first_mdd.median()),
                f"wait_median_mdd_{horizon}_pct": float(wait_mdd.median()),
                f"wait_median_edge_{horizon}_pctpt": float(edge.median()),
                f"wait_mean_edge_{horizon}_pctpt": float(edge.mean()),
                f"wait_beat_first_{horizon}_pct": float((edge > 0).mean() * 100),
                f"wait_mdd_improvement_{horizon}_pctpt": float(
                    (wait_mdd - first_mdd).median()
                ),
            }
        )
    return result


def _fmt(value: float) -> str:
    return f"{value:+.2f}%"


def _table(lines: list[str], title: str, summary: dict[str, Any]) -> None:
    lines.extend(["", title, ""])
    if not summary.get("samples"):
        lines.append("- 표본 없음")
        return
    lines.extend(
        [
            f"- 표본: **{summary['samples']}개월**",
            "",
            "| 평가 | 첫 거래일 진입 | reset 후 둘째 거래일 진입 | 대기안 중앙 차이 | 대기안 승률 | MDD 중앙 개선 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for horizon in HORIZONS:
        lines.append(
            f"| {horizon}거래일 수익 | "
            f"{_fmt(summary[f'first_median_return_{horizon}_pct'])} | "
            f"{_fmt(summary[f'wait_median_return_{horizon}_pct'])} | "
            f"{_fmt(summary[f'wait_median_edge_{horizon}_pctpt'])} | "
            f"{summary[f'wait_beat_first_{horizon}_pct']:.1f}% | "
            f"{_fmt(summary[f'wait_mdd_improvement_{horizon}_pctpt'])} |"
        )
    lines.extend(
        [
            "",
            "| 하방 지표 | 첫 거래일 진입 | reset 후 둘째 거래일 진입 |",
            "|---|---:|---:|",
            f"| 21일 P10 | {_fmt(summary['first_p10_return_21_pct'])} | {_fmt(summary['wait_p10_return_21_pct'])} |",
            f"| 63일 P10 | {_fmt(summary['first_p10_return_63_pct'])} | {_fmt(summary['wait_p10_return_63_pct'])} |",
            f"| 63일 중앙 MDD | {_fmt(summary['first_median_mdd_63_pct'])} | {_fmt(summary['wait_median_mdd_63_pct'])} |",
        ]
    )


def _render(payload: dict[str, Any]) -> str:
    lines = [
        "# JDSS 월초 최초진입 타이밍 연구",
        "",
        f"- 데이터 종료일: `{payload['end_date']}`",
        "- 공통 최초진입: 50% → 75% → 100%, 단계 간 3 거래세션",
        "- A안: 월 첫 거래일에 전 거래일 완결 목표로 1차 진입",
        "- B안: 월 첫 거래일은 현금 대기, 그날 종가 reset 목표로 둘째 거래일에 1차 진입",
        "- 두 안 모두 월 첫 거래일부터 동일한 21/63/126 거래일 종료점으로 평가",
    ]
    _table(lines, "## 1. 전체 월초", payload["all"])
    _table(lines, "## 2. 최근 4년 월초", payload["recent_4y"])
    _table(lines, "## 3. 9월 월초만", payload["september"])
    _table(lines, "## 4. 전월 말 목표 레버리지 1.50x", payload["prior_150"])
    _table(lines, "## 5. 월초 reset에서 목표가 실제 변경된 달", payload["reset_changed"])
    _table(lines, "## 6. 현재와 유사한 월초", payload["current_like"])
    lines.extend(
        [
            "",
            "## 해석 원칙",
            "",
            "- 대기안이 근소하게 좋아도 1세션 시장 타이밍 규칙을 추가할 만큼 일관되지 않으면 현행을 유지합니다.",
            "- 특히 최근 4년, 9월, 1.50x prior leverage, reset 변경월, 현재 유사환경을 함께 봅니다.",
            "- 이 연구는 최초 계좌 시작 시점만 비교하며 V3.2.2의 장기 배분 규칙은 변경하지 않습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = _parse_args()
    clock = MarketClock()
    end: date = pd.Timestamp(args.end).date() if args.end else clock.latest_completed_session()
    config, policy, strategy_start, frames, targets = _prepare_history(end)
    index = targets.index
    first_position = max(1, int(index.searchsorted(pd.Timestamp(strategy_start))))
    last_position = len(index) - max(HORIZONS)

    latest_context = _market_context(frames["QQQ"], targets, index[-1])
    rows: list[dict[str, Any]] = []
    for position in range(first_position, last_position + 1):
        timestamp = index[position]
        prior_ts = index[position - 1]
        if timestamp.to_period("M") == prior_ts.to_period("M"):
            continue

        first = simulate_month_start(
            frames,
            targets,
            start_position=position,
            initial_wait_sessions=0,
            initial_capital=float(policy.initial_capital),
            hwm_reinvestment_fraction=float(policy.hwm_reinvestment_fraction),
            buy_fee=float(config.global_.buy_fee),
            sell_fee=float(config.global_.sell_fee),
            slippage=float(config.backtest.default_slippage),
        )
        wait = simulate_month_start(
            frames,
            targets,
            start_position=position,
            initial_wait_sessions=1,
            initial_capital=float(policy.initial_capital),
            hwm_reinvestment_fraction=float(policy.hwm_reinvestment_fraction),
            buy_fee=float(config.global_.buy_fee),
            sell_fee=float(config.global_.sell_fee),
            slippage=float(config.backtest.default_slippage),
        )
        context = _market_context(frames["QQQ"], targets, prior_ts)
        prior_weights = _weights(targets.loc[prior_ts])
        reset_weights = _weights(targets.loc[timestamp])
        reset_changed = any(
            abs(prior_weights[symbol] - reset_weights[symbol]) > 1e-12
            for symbol in ALLOCATION_SYMBOLS
        )
        row: dict[str, Any] = {
            "month_start": timestamp.date().isoformat(),
            "month": int(timestamp.month),
            "prior_leverage": float(targets.loc[prior_ts, "leverage"]),
            "reset_leverage": float(targets.loc[timestamp, "leverage"]),
            "reset_changed": reset_changed,
            "context_qqq_above_sma200": context["qqq_above_sma200"],
            "context_qqq_high_bucket": context["qqq_high_bucket"],
        }
        for horizon in HORIZONS:
            row[f"first_return_{horizon}"] = first[f"return_{horizon}"]
            row[f"wait_return_{horizon}"] = wait[f"return_{horizon}"]
            row[f"first_mdd_{horizon}"] = first[f"mdd_{horizon}"]
            row[f"wait_mdd_{horizon}"] = wait[f"mdd_{horizon}"]
        rows.append(row)

    recent_cutoff = (pd.Timestamp(end) - pd.DateOffset(years=4)).date().isoformat()
    current_like = [
        row
        for row in rows
        if abs(float(row["prior_leverage"]) - float(latest_context["prior_leverage"])) < 1e-12
        and bool(row["context_qqq_above_sma200"]) == bool(latest_context["qqq_above_sma200"])
        and str(row["context_qqq_high_bucket"]) == str(latest_context["qqq_high_bucket"])
    ]
    payload = {
        "end_date": end.isoformat(),
        "latest_context": latest_context,
        "recent_4y_cutoff": recent_cutoff,
        "all": _summary(rows),
        "recent_4y": _summary([row for row in rows if row["month_start"] >= recent_cutoff]),
        "september": _summary([row for row in rows if row["month"] == 9]),
        "prior_150": _summary([row for row in rows if abs(row["prior_leverage"] - 1.5) < 1e-12]),
        "reset_changed": _summary([row for row in rows if row["reset_changed"]]),
        "current_like": _summary(current_like),
    }

    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
