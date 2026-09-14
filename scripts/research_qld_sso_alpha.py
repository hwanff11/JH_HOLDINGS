from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from jd_holdings.infrastructure.market_data import YFinanceDataSource

ANNUAL_DAYS = 252
WINDOWS = {
    "train_2007_2014": ("2007-01-01", "2014-12-31"),
    "validation_2015_2020": ("2015-01-01", "2020-12-31"),
    "oos_2021_present": ("2021-01-01", None),
    "recent_stress_2022_present": ("2022-01-01", None),
    "full": ("2007-01-01", None),
}


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str


STRATEGIES = (
    Strategy("QLD_BUY_HOLD", "QLD 100% buy-and-hold"),
    Strategy("SSO_BUY_HOLD", "SSO 100% buy-and-hold"),
    Strategy("MIX_50_50", "QLD/SSO 50/50 fixed blend"),
    Strategy("RS63_MONTHLY", "Month-end: QQQ/SPY 63d relative-strength winner"),
    Strategy("RS126_MONTHLY", "Month-end: QQQ/SPY 126d relative-strength winner"),
    Strategy("RS126_WEEKLY", "Week-end: QQQ/SPY 126d relative-strength winner"),
    Strategy(
        "RS_DUAL_MONTHLY",
        "Month-end: QLD only when QQQ wins both 63d and 126d RS",
    ),
    Strategy(
        "RISK_ADJ126_MONTHLY",
        "Month-end: 126d return divided by 20d volatility winner",
    ),
    Strategy(
        "QLD_TREND_RS_MONTHLY",
        "Month-end: QLD only when QQQ>200DMA and QQQ 126d RS wins",
    ),
    Strategy(
        "DUAL_TREND_MONTHLY",
        "Month-end: trend-qualified 126d winner; if both weak use lower-vol 2x ETF",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JDSS QLD/SSO alpha rotation research"
    )
    parser.add_argument("--end", default="")
    parser.add_argument("--output-json", default="reports/qld-sso-alpha.json")
    parser.add_argument("--output-md", default="reports/qld-sso-alpha.md")
    return parser.parse_args()


def load_prices(end: date) -> pd.DataFrame:
    source = YFinanceDataSource(Path(".cache") / "qld-sso-alpha")
    frames = {
        symbol: source.daily(symbol, "2006-01-01", end.isoformat())
        for symbol in ("QQQ", "SPY", "QLD", "SSO")
    }
    index = frames["QQQ"].index
    for symbol in ("SPY", "QLD", "SSO"):
        index = index.intersection(frames[symbol].index)
    prices = pd.DataFrame(
        {symbol: frames[symbol].loc[index, "close"] for symbol in frames}
    )
    return prices.dropna().sort_index()


def features(prices: pd.DataFrame) -> pd.DataFrame:
    out = prices.copy()
    for symbol in ("QQQ", "SPY"):
        out[f"{symbol}_r63"] = prices[symbol].pct_change(63)
        out[f"{symbol}_r126"] = prices[symbol].pct_change(126)
        out[f"{symbol}_sma200"] = prices[symbol].rolling(200).mean()
        out[f"{symbol}_vol20"] = (
            prices[symbol].pct_change().rolling(20).std() * np.sqrt(ANNUAL_DAYS)
        )
    return out


def rebalance_mask(index: pd.DatetimeIndex, cadence: str) -> pd.Series:
    selected = pd.Series(False, index=index)
    if cadence == "monthly":
        periods = index.to_period("M")
    elif cadence == "weekly":
        periods = index.to_period("W-FRI")
    else:
        raise ValueError(cadence)
    selected.iloc[np.r_[periods[1:] != periods[:-1], True]] = True
    return selected


def choice_weights(frame: pd.DataFrame, strategy: str) -> pd.DataFrame:
    weights = pd.DataFrame(np.nan, index=frame.index, columns=["QLD", "SSO"])

    if strategy == "QLD_BUY_HOLD":
        weights.iloc[0] = (1.0, 0.0)
        return weights.ffill()
    if strategy == "SSO_BUY_HOLD":
        weights.iloc[0] = (0.0, 1.0)
        return weights.ffill()
    if strategy == "MIX_50_50":
        weights.iloc[0] = (0.5, 0.5)
        return weights.ffill()

    cadence = "weekly" if strategy == "RS126_WEEKLY" else "monthly"
    mask = rebalance_mask(frame.index, cadence)

    for timestamp in frame.index[mask]:
        row = frame.loc[timestamp]
        qld = False
        if strategy == "RS63_MONTHLY":
            qld = row["QQQ_r63"] >= row["SPY_r63"]
        elif strategy in ("RS126_MONTHLY", "RS126_WEEKLY"):
            qld = row["QQQ_r126"] >= row["SPY_r126"]
        elif strategy == "RS_DUAL_MONTHLY":
            qld = (
                row["QQQ_r63"] >= row["SPY_r63"]
                and row["QQQ_r126"] >= row["SPY_r126"]
            )
        elif strategy == "RISK_ADJ126_MONTHLY":
            q_score = row["QQQ_r126"] / row["QQQ_vol20"]
            s_score = row["SPY_r126"] / row["SPY_vol20"]
            qld = q_score >= s_score
        elif strategy == "QLD_TREND_RS_MONTHLY":
            qld = (
                row["QQQ"] > row["QQQ_sma200"]
                and row["QQQ_r126"] >= row["SPY_r126"]
            )
        elif strategy == "DUAL_TREND_MONTHLY":
            q_trend = row["QQQ"] > row["QQQ_sma200"]
            s_trend = row["SPY"] > row["SPY_sma200"]
            if q_trend and not s_trend:
                qld = True
            elif s_trend and not q_trend:
                qld = False
            elif q_trend and s_trend:
                qld = row["QQQ_r126"] >= row["SPY_r126"]
            else:
                qld = row["QQQ_vol20"] <= row["SPY_vol20"]
        else:
            raise ValueError(strategy)
        weights.loc[timestamp] = (1.0, 0.0) if bool(qld) else (0.0, 1.0)

    return weights.ffill()


def simulate(
    prices: pd.DataFrame,
    strategy: str,
    slippage: float,
) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    frame = features(prices)
    targets = choice_weights(frame, strategy)
    held = targets.shift(1).dropna()
    asset_returns = prices[["QLD", "SSO"]].pct_change().reindex(held.index)
    valid = held.notna().all(axis=1) & asset_returns.notna().all(axis=1)
    held = held.loc[valid]
    asset_returns = asset_returns.loc[valid]
    turnover = held.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = held.iloc[0].abs().sum()
    gross = (held * asset_returns).sum(axis=1)
    net = gross - turnover * slippage
    equity = (1.0 + net).cumprod()
    return equity, held, turnover


def benchmark_equity(price: pd.Series) -> pd.Series:
    returns = price.pct_change().dropna()
    return (1.0 + returns).cumprod()


def slice_equity(
    equity: pd.Series,
    start: str | None,
    end: str | None,
) -> pd.Series:
    selected = equity
    if start:
        selected = selected.loc[selected.index >= pd.Timestamp(start)]
    if end:
        selected = selected.loc[selected.index <= pd.Timestamp(end)]
    return selected


def metrics(equity: pd.Series, turnover: pd.Series | None = None) -> dict:
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
    cagr = (float(equity.iloc[-1]) / float(equity.iloc[0])) ** (1 / years) - 1
    drawdown = equity / equity.cummax() - 1.0
    mdd = float(drawdown.min())
    returns = equity.pct_change().dropna()
    std = returns.std(ddof=0)
    sharpe = (
        float(returns.mean() / std * np.sqrt(ANNUAL_DAYS)) if std > 0 else 0.0
    )
    turn = 0.0
    if turnover is not None:
        selected_turnover = turnover.reindex(equity.index).fillna(0.0)
        turn = float(selected_turnover.sum() / years)
    return {
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(cagr / abs(mdd), 3) if mdd < 0 else 0.0,
        "turnover_x_per_year": round(turn, 3),
    }


def window_metrics(
    equity: pd.Series,
    turnover: pd.Series | None = None,
) -> dict[str, dict]:
    output = {}
    for name, (start, end) in WINDOWS.items():
        view = slice_equity(equity, start, end)
        selected_turnover = turnover.reindex(view.index) if turnover is not None else None
        output[name] = metrics(view, selected_turnover)
    return output


def annual_returns(equity: pd.Series) -> dict[str, float]:
    output = {}
    for year, view in equity.groupby(equity.index.year):
        if len(view) >= 2:
            output[str(year)] = round(
                (float(view.iloc[-1]) / float(view.iloc[0]) - 1.0) * 100,
                2,
            )
    return output


def rolling_summary(
    candidate: pd.Series,
    benchmark: pd.Series,
    years: int,
) -> dict:
    common = candidate.index.intersection(benchmark.index)
    common = common[common >= pd.Timestamp("2007-01-01")]
    candidate = candidate.loc[common]
    benchmark = benchmark.loc[common]
    first = int(common[0].year)
    last = int(common[-1].year)
    deltas = []
    for start_year in range(first, last - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        end = pd.Timestamp(f"{start_year + years - 1}-12-31")
        candidate_view = candidate.loc[
            (candidate.index >= start) & (candidate.index <= end)
        ]
        benchmark_view = benchmark.loc[
            (benchmark.index >= start) & (benchmark.index <= end)
        ]
        if len(candidate_view) < 100 or len(benchmark_view) < 100:
            continue
        deltas.append(
            metrics(candidate_view)["cagr_pct"]
            - metrics(benchmark_view)["cagr_pct"]
        )
    return {
        "windows": len(deltas),
        "qqq_beat_rate_pct": (
            round(sum(value > 0 for value in deltas) / len(deltas) * 100, 1)
            if deltas
            else 0.0
        ),
        "median_cagr_delta_pctpt": (
            round(float(pd.Series(deltas).median()), 2) if deltas else 0.0
        ),
        "worst_cagr_delta_pctpt": round(min(deltas), 2) if deltas else 0.0,
    }


def selection_score(row: dict, qqq: dict, qld: dict) -> float:
    score = 0.0
    for window, weight in (
        ("train_2007_2014", 0.45),
        ("validation_2015_2020", 0.55),
    ):
        metric = row[window]
        qqq_metric = qqq[window]
        qld_metric = qld[window]
        mdd_penalty = max(
            0.0,
            abs(metric["mdd_pct"]) - abs(qld_metric["mdd_pct"]),
        )
        score += weight * (
            (metric["cagr_pct"] - qqq_metric["cagr_pct"])
            + 3.0 * (metric["calmar"] - qqq_metric["calmar"])
            + 0.75 * (metric["sharpe"] - qqq_metric["sharpe"])
            - 0.20 * mdd_penalty
        )
    return round(score, 4)


def render(payload: dict) -> str:
    base = payload["base_cost_results"]
    benchmarks = payload["benchmarks"]
    lines = [
        "# JDSS QLD/SSO Alpha Research — Phase 1",
        "",
        f"- 데이터: `{payload['start_date']}` ~ `{payload['end_date']}`",
        "- 목표: QQQ 대비 CAGR 최대화 + MDD는 QLD Buy&Hold보다 낮게",
        "- 신호는 QQQ/SPY 종가로 계산하고 다음 거래일부터 QLD/SSO 비중에 반영",
        "- 기본 슬리피지: 0.10% per traded notional",
        "",
        "## Benchmarks",
        "",
        "|Benchmark|Full CAGR|MDD|Sharpe|Calmar|",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("QQQ", "QLD", "SSO"):
        metric = benchmarks[name]["full"]
        lines.append(
            f"|{name}|{metric['cagr_pct']:+.2f}%|{metric['mdd_pct']:.2f}%|"
            f"{metric['sharpe']:.3f}|{metric['calmar']:.3f}|"
        )
    lines.extend(
        [
            "",
            "## Train+Validation 사전순위",
            "",
            "|순위|전략|점수|Full CAGR|MDD|Calmar|OOS CAGR|OOS MDD|Turnover|목표통과|",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for rank, row in enumerate(payload["train_validation_selection"], 1):
        metric = row["windows"]["full"]
        oos = row["windows"]["oos_2021_present"]
        lines.append(
            f"|{rank}|{row['strategy']}|{row['selection_score']:+.3f}|"
            f"{metric['cagr_pct']:+.2f}%|{metric['mdd_pct']:.2f}%|"
            f"{metric['calmar']:.3f}|{oos['cagr_pct']:+.2f}%|{oos['mdd_pct']:.2f}%|"
            f"{metric['turnover_x_per_year']:.2f}x|"
            f"{'YES' if row['objective_pass'] else 'NO'}|"
        )
    lines.extend(["", "## 기본비용 전체 결과", ""])
    lines.extend(
        [
            "|전략|CAGR|MDD|Sharpe|Calmar|3Y QQQ 승률|5Y QQQ 승률|",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(
        base,
        key=lambda value: value["windows"]["full"]["cagr_pct"],
        reverse=True,
    ):
        metric = row["windows"]["full"]
        lines.append(
            f"|{row['strategy']}|{metric['cagr_pct']:+.2f}%|{metric['mdd_pct']:.2f}%|"
            f"{metric['sharpe']:.3f}|{metric['calmar']:.3f}|"
            f"{row['rolling_3y_vs_qqq']['qqq_beat_rate_pct']:.1f}%|"
            f"{row['rolling_5y_vs_qqq']['qqq_beat_rate_pct']:.1f}%|"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    end = pd.Timestamp(args.end).date() if args.end else date.today()
    prices = load_prices(end)
    qqq_equity = benchmark_equity(prices["QQQ"])
    qld_equity = benchmark_equity(prices["QLD"])
    sso_equity = benchmark_equity(prices["SSO"])
    benchmarks = {
        "QQQ": window_metrics(qqq_equity),
        "QLD": window_metrics(qld_equity),
        "SSO": window_metrics(sso_equity),
    }

    costs = (0.0005, 0.0010, 0.0020)
    by_cost: dict[str, list[dict]] = {}
    equities: dict[str, pd.Series] = {}
    for slippage in costs:
        rows = []
        for strategy in STRATEGIES:
            equity, _held, turnover = simulate(prices, strategy.name, slippage)
            if slippage == 0.0010:
                equities[strategy.name] = equity
            windows = window_metrics(equity, turnover)
            full = windows["full"]
            objective_pass = (
                full["cagr_pct"] > benchmarks["QQQ"]["full"]["cagr_pct"]
                and abs(full["mdd_pct"]) < abs(benchmarks["QLD"]["full"]["mdd_pct"])
            )
            rows.append(
                {
                    "strategy": strategy.name,
                    "description": strategy.description,
                    "windows": windows,
                    "objective_pass": objective_pass,
                }
            )
        by_cost[f"{slippage:.4f}"] = rows

    base_rows = by_cost["0.0010"]
    ranked = []
    for row in base_rows:
        item = json.loads(json.dumps(row))
        item["selection_score"] = selection_score(
            item["windows"],
            benchmarks["QQQ"],
            benchmarks["QLD"],
        )
        item["rolling_3y_vs_qqq"] = rolling_summary(
            equities[row["strategy"]], qqq_equity, 3
        )
        item["rolling_5y_vs_qqq"] = rolling_summary(
            equities[row["strategy"]], qqq_equity, 5
        )
        item["annual_returns_pct"] = annual_returns(equities[row["strategy"]])
        row["rolling_3y_vs_qqq"] = item["rolling_3y_vs_qqq"]
        row["rolling_5y_vs_qqq"] = item["rolling_5y_vs_qqq"]
        ranked.append(item)
    ranked.sort(key=lambda value: value["selection_score"], reverse=True)

    first_research_day = prices.loc[
        prices.index >= pd.Timestamp("2007-01-01")
    ].index[0]
    payload = {
        "research": "JDSS QLD/SSO Alpha Research Phase 1",
        "start_date": first_research_day.date().isoformat(),
        "end_date": prices.index[-1].date().isoformat(),
        "objective": (
            "maximize CAGR versus QQQ while keeping MDD below QLD buy-and-hold"
        ),
        "benchmarks": benchmarks,
        "base_cost_results": base_rows,
        "train_validation_selection": ranked,
        "cost_stress_results": by_cost,
    }
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_md.write_text(render(payload), encoding="utf-8")
    print(output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
