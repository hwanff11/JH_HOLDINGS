from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_commissioning import LiveCommissioningPreflight
from jd_holdings.backtest.runner import run_production_backtest, serialize_backtest_run
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import V322Policy
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JDSS 운영 도구")
    parser.add_argument("--config", default="strategy.yaml", help="strategy.yaml 경로")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config", help="설정 검증")
    subparsers.add_parser("init-db", help="SQLite 스키마 생성")
    analyze = subparsers.add_parser("analyze", help="최신 완결 일봉 JDSS 분석")
    analyze.add_argument("--symbol", choices=["TQQQ", "SOXL"])
    backtest = subparsers.add_parser("backtest", help="yfinance 장기 백테스트")
    backtest.add_argument("--symbol", choices=["TQQQ", "SOXL", "ALL"], default="ALL")
    backtest.add_argument("--start")
    backtest.add_argument("--end")
    backtest.add_argument("--slippage", type=float)
    backtest.add_argument("--refresh", action="store_true")
    backtest.add_argument("--output", type=Path)
    subparsers.add_parser("toss-smoke", help="주문 없이 Toss 인증·시세·장상태만 조회")
    live_preflight = subparsers.add_parser(
        "live-preflight",
        help="별도 신규 live DB와 실계좌의 최초기동 안전조건 점검",
    )
    live_preflight.add_argument(
        "--arm-buy-halt",
        action="store_true",
        help="점검 통과 시 live DB를 BUY 긴급정지 상태로 시작",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    V322Policy.from_config(config)
    if args.command == "validate-config":
        print(f"OK strategy={config.version} config={config.config_version}")
        return 0

    settings = load_runtime_settings()
    repository = SQLiteRepository(settings.database_path, config)
    if args.command == "init-db":
        print(f"OK database={settings.database_path}")
        return 0

    if args.command == "live-preflight":
        preflight = LiveCommissioningPreflight(repository, TossClient())
        result = preflight.run()
        if result.safe and args.arm_buy_halt:
            preflight.arm_buy_halt()
        print(
            json.dumps(
                {
                    "safe": result.safe,
                    "issues": list(result.issues),
                    "buying_power": (
                        str(result.buying_power)
                        if result.buying_power is not None
                        else None
                    ),
                    "buy_halt_armed": bool(result.safe and args.arm_buy_halt),
                    "database": str(settings.database_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.safe else 1

    data_source = YFinanceDataSource("data/cache")
    market_clock = MarketClock()
    if args.command == "analyze":
        results = AnalysisService(config, repository, data_source, market_clock).analyze_all()
        for result in results:
            if args.symbol and result.symbol != args.symbol:
                continue
            print(
                json.dumps(
                    {
                        "symbol": result.symbol,
                        "trade_date": result.trade_date.isoformat(),
                        "score": result.score.detail(),
                        "decision": {
                            "action": result.decision.action.value,
                            "allowed": result.decision.allowed,
                            "reason_codes": result.decision.reason_codes,
                            "planned_budget": str(result.decision.planned_budget),
                        },
                        "signal_id": result.signal_id,
                        "signal_created": result.signal_created,
                        "execution_mode": "VIRTUAL_OVERLAY_SIGNAL_ONLY",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    if args.command == "backtest":
        completed = market_clock.latest_completed_session()
        start = args.start or config.backtest.default_start
        end = args.end or completed.isoformat()
        symbols = config.enabled_symbols if args.symbol == "ALL" else (args.symbol,)
        run = run_production_backtest(
            config,
            data_source,
            symbols=symbols,
            start=start,
            end=end,
            slippage=args.slippage,
            refresh=args.refresh,
        )
        for warning in run.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for symbol, result in run.results.items():
            metrics = result.metrics
            print(
                f"{symbol} virtual-JDSS: return={metrics['total_return_pct']:+.2f}% "
                f"CAGR={metrics['cagr_pct']:+.2f}% MDD={metrics['mdd_pct']:.2f}% "
                f"cycles={metrics['closed_cycles']} signals={metrics['signals']}"
            )
        output = serialize_backtest_run(
            run,
            strategy_version=config.version,
            config_version=config.config_version,
            generated_at=datetime.now(UTC).isoformat(),
        )
        portfolio_metrics = output["portfolio_metrics"]
        print(
            "PORTFOLIO: "
            f"return={portfolio_metrics['total_return_pct']:+.2f}% "
            f"CAGR={portfolio_metrics['cagr_pct']:+.2f}% "
            f"MDD={portfolio_metrics['mdd_pct']:.2f}% "
            f"Sharpe={portfolio_metrics.get('sharpe', 0):.3f}"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"saved={args.output}")
        return 0

    if args.command == "toss-smoke":
        client = TossClient()
        smoke_symbols = ["QQQ", *config.enabled_symbols]
        if config.idle_cash.enabled:
            smoke_symbols.append(config.idle_cash.symbol)
        prices = client.get_prices(list(dict.fromkeys(smoke_symbols)))
        calendar = client.get_market_calendar()
        print(
            json.dumps(
                {
                    "authenticated": True,
                    "prices": {key: str(value) for key, value in prices.items()},
                    "market_dates": {
                        key: value.get("date")
                        for key, value in calendar.items()
                        if isinstance(value, dict)
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
