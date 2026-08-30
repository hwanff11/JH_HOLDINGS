from __future__ import annotations

import logging

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_commissioning import arm_live_startup_buy_halt
from jd_holdings.application.live_runtime_services import LiveAllocationTradingService
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.bot import configure_logging, recover_unapplied_core_fills
from jd_holdings.config import load_config
from jd_holdings.infrastructure.live_runtime_hardening import (
    HardenedOperationalSafetyTelegramBotApp,
)
from jd_holdings.infrastructure.live_runtime_resilience import (
    ResilientLiveInitialOnboardingPortfolioService,
)
from jd_holdings.infrastructure.live_runtime_resilience import (
    ResilientReadTossClient as TossClient,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.settings import load_runtime_settings


def main() -> None:
    """Run the explicitly commissioned live runtime in fail-closed BUY-HALT mode."""
    settings = load_runtime_settings()
    if settings.trading_mode != "live":
        raise RuntimeError("live runtime entrypoint는 JDSS_TRADING_MODE=live에서만 실행할 수 있습니다")
    settings.require_live_trading()

    config = load_config(settings.config_path)
    if not config.portfolio.enabled:
        raise RuntimeError("V3.2.2 portfolio가 비활성화되어 있습니다")

    configure_logging(settings.log_path)
    repository = SQLiteRepository(settings.database_path, config)

    # The legacy strategy flag remains false so the normal/default runtime can never
    # drift into live. Only this explicitly commissioned entrypoint is allowed to use
    # the real broker. Every process start/restart re-arms BUY halt before TossClient
    # is constructed, so a previous Telegram resume is never inherited on restart.
    arm_live_startup_buy_halt(repository)

    recovered_core_fills = recover_unapplied_core_fills(repository)
    if recovered_core_fills:
        logging.getLogger(__name__).warning(
            "시작 중 미반영 코어 체결 %d건을 원장에 복구했습니다",
            len(recovered_core_fills),
        )

    data_source = YFinanceDataSource(settings.cache_path)
    market_clock = MarketClock()
    broker = TossClient()
    order_manager = OrderManager(repository, broker, settings)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading_service = LiveAllocationTradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        market_clock,
        None,
    )
    order_monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
    )
    portfolio_service = ResilientLiveInitialOnboardingPortfolioService(
        config,
        repository,
        broker,
        order_manager,
        data_source,
        market_clock,
        trading_mode=settings.trading_mode,
    )
    reconciliation_service = ReconciliationService(config, repository, broker)

    mismatches = reconciliation_service.run()
    if mismatches:
        logging.getLogger(__name__).error("live 시작 정합성 검사 실패: %s", mismatches)

    analysis_service = AnalysisService(config, repository, data_source, market_clock)
    app = HardenedOperationalSafetyTelegramBotApp(
        config,
        settings,
        repository,
        analysis_service,
        trading_service,
        order_monitor,
        reconciliation_service,
        data_source,
        market_clock,
        broker,
        None,
        portfolio_service,
    )
    if mismatches:
        app.notify_startup_reconciliation(mismatches)
    app.run()


if __name__ == "__main__":
    main()
