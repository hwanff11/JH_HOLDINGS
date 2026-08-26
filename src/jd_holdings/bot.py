from __future__ import annotations

import html
import json
import logging
import logging.handlers
import os
from decimal import Decimal
from pathlib import Path

from jd_holdings.application.account_preflight import RealAccountPreflight
from jd_holdings.application.allocation_trading_service import AllocationTradingService
from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.broker import MarketDataDryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import IdleCashManager
from jd_holdings.application.initial_onboarding_portfolio import (
    InitialOnboardingPortfolioService,
)
from jd_holdings.application.managed_account import managed_cash_balance
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.operational_safety_telegram import (
    OperationalSafetyTelegramBotApp,
)
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def recover_unapplied_core_fills(repository: SQLiteRepository) -> tuple[str, ...]:
    """Close the crash window between order persistence and core-ledger apply."""
    recovered: list[str] = []
    for client_order_id in repository.unapplied_core_fill_order_ids():
        repository.apply_core_fill(client_order_id)
        recovered.append(client_order_id)
    return tuple(recovered)


def notify_startup_account_preflight(app, issues: tuple[str, ...]) -> None:
    """Warn about the read-only real-account check without gating dry-run orders."""
    if not issues:
        return
    lines = [
        "⚠️ <b>[실계좌 읽기 전용 사전점검]</b>",
        "",
        "토스 실계좌에서 아래 운영 준비 항목을 확인했습니다.",
        "이 경고는 모의투자 원장과 분리되며 모의주문을 자동 차단하지 않습니다.",
    ]
    lines.extend(f"• <code>{html.escape(issue)}</code>" for issue in issues)
    try:
        app._send("\n".join(lines))
    except Exception:
        logging.getLogger(__name__).exception("실계좌 사전점검 경고 Telegram 전송 실패")


def restore_dry_run_holdings(
    repository: SQLiteRepository,
    broker: MarketDataDryRunBroker,
) -> None:
    """Rebuild the in-memory dry-run account from the persisted V3.2.2 ledger."""
    totals: dict[str, tuple[int, Decimal]] = {}
    for core in repository.core_positions():
        quantity = int(core["qty"])
        if quantity <= 0:
            continue
        symbol = str(core["symbol"])
        cost = Decimal(str(core["cost_basis"] or "0"))
        old_qty, old_cost = totals.get(symbol, (0, Decimal("0")))
        totals[symbol] = (old_qty + quantity, old_cost + cost)
    # Direct JDSS quantities should stay zero in V3.2.2. Include them during
    # recovery so a stale/corrupt ledger cannot silently disappear before reconciliation.
    for symbol in repository.config.enabled_symbols:
        position = repository.get_position(symbol)
        if position.quantity <= 0:
            continue
        old_qty, old_cost = totals.get(symbol, (0, Decimal("0")))
        totals[symbol] = (
            old_qty + position.quantity,
            old_cost + position.current_cost_basis,
        )
    for symbol, (quantity, cost) in totals.items():
        broker.holdings[symbol] = {
            "quantity": quantity,
            "averagePurchasePrice": cost / Decimal(quantity),
        }
    if repository.config.idle_cash.enabled:
        cash_state = repository.get_idle_cash_state()
        if cash_state.managed_quantity > 0:
            broker.holdings[cash_state.symbol] = {
                "quantity": cash_state.managed_quantity,
                "averagePurchasePrice": cash_state.average_price,
            }
    broker.buying_power = max(
        Decimal("0"), managed_cash_balance(repository.config, repository)
    )


def restore_dry_run_orders(
    repository: SQLiteRepository,
    broker: MarketDataDryRunBroker,
) -> None:
    """Restore provable open DRY orders and never reuse a historical broker id."""
    max_sequence = broker.sequence
    with repository.transaction() as connection:
        historical_ids = connection.execute(
            "SELECT broker_order_id FROM orders WHERE broker_order_id LIKE 'DRY-%'"
        ).fetchall()
    for row in historical_ids:
        broker_order_id = str(row["broker_order_id"] or "")
        try:
            max_sequence = max(max_sequence, int(broker_order_id.removeprefix("DRY-")))
        except ValueError:
            continue

    for local in repository.open_orders():
        broker_order_id = str(local.get("broker_order_id") or "")
        if not broker_order_id.startswith("DRY-"):
            continue
        local_status = str(local.get("status") or "PENDING")
        filled_qty = int(local.get("filled_qty") or 0)
        if local_status not in {"CREATED", "SUBMITTED", "PENDING"}:
            continue
        if filled_qty > 0:
            continue
        try:
            raw = json.loads(str(local.get("raw_json") or "{}"))
        except json.JSONDecodeError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw.update(
            {
                "orderId": broker_order_id,
                "clientOrderId": str(local["client_order_id"]),
                "symbol": str(local["symbol"]).upper(),
                "side": str(local["side"]).upper(),
                "orderType": str(local["order_type"]).upper(),
                "timeInForce": raw.get("timeInForce", "DAY"),
                "status": "PENDING",
                "price": local.get("price"),
                "quantity": str(local["qty"]),
                "execution": {
                    "filledQuantity": "0",
                    "averageFilledPrice": None,
                    "filledAmount": None,
                    "commission": "0",
                    "tax": "0",
                    "filledAt": None,
                    "settlementDate": None,
                },
                "_appliedFilledQuantity": "0",
                "_appliedFilledAmount": "0",
            }
        )
        broker.orders[broker_order_id] = raw
    broker.sequence = max_sequence


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            ),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    settings = load_runtime_settings()
    config = load_config(settings.config_path)
    configure_logging(settings.log_path)
    repository = SQLiteRepository(settings.database_path, config)
    recovered_core_fills = recover_unapplied_core_fills(repository)
    if recovered_core_fills:
        logging.getLogger(__name__).warning(
            "시작 중 미반영 코어 체결 %d건을 원장에 복구했습니다",
            len(recovered_core_fills),
        )
    data_source = YFinanceDataSource(settings.cache_path)
    market_clock = MarketClock()
    if config.portfolio.enabled and settings.trading_mode == "live":
        raise RuntimeError("JDSS V3.2.2는 검증된 릴리즈라도 live 모드가 별도 승인 전까지 잠겨 있습니다")
    if settings.trading_mode == "live":
        settings.require_live_trading()
        broker = TossClient()
    else:
        broker = MarketDataDryRunBroker(
            data_source,
            buying_power=config.total_strategy_capital,
        )
        restore_dry_run_holdings(repository, broker)
        restore_dry_run_orders(repository, broker)
    account_client = (
        TossClient()
        if os.getenv("TOSS_APP_KEY") and os.getenv("TOSS_APP_SECRET")
        else None
    )
    account_preflight = (
        RealAccountPreflight(repository, account_client).run()
        if account_client is not None
        else None
    )
    if account_preflight is not None and account_preflight.issues:
        logging.getLogger(__name__).warning(
            "실계좌 읽기 전용 사전점검 경고: %s",
            account_preflight.issues,
        )
    order_manager = OrderManager(repository, broker, settings)
    idle_cash_manager = None
    if config.idle_cash.enabled:
        idle_cash_manager = IdleCashManager(
            config, repository, broker, order_manager, market_clock
        )
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading_service = AllocationTradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        market_clock,
        idle_cash_manager,
    )
    order_monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
    )
    # Constructing the service first ensures the QQQ allocation ledger row exists
    # before startup reconciliation runs.
    portfolio_service = InitialOnboardingPortfolioService(
        config,
        repository,
        broker,
        order_manager,
        data_source,
        market_clock,
        trading_mode=settings.trading_mode,
    )
    reconciliation_service = ReconciliationService(config, repository, broker)
    if idle_cash_manager is not None:
        idle_cash_manager.refresh_orders()
    mismatches = reconciliation_service.run()
    if mismatches:
        logging.getLogger(__name__).error("시작 정합성 검사 실패: %s", mismatches)
    analysis_service = AnalysisService(config, repository, data_source, market_clock)
    app = OperationalSafetyTelegramBotApp(
        config,
        settings,
        repository,
        analysis_service,
        trading_service,
        order_monitor,
        reconciliation_service,
        data_source,
        market_clock,
        account_client,
        idle_cash_manager,
        portfolio_service,
    )
    if mismatches:
        app.notify_startup_reconciliation(mismatches)
    if account_preflight is not None:
        notify_startup_account_preflight(app, account_preflight.issues)
    app.run()


if __name__ == "__main__":
    main()
