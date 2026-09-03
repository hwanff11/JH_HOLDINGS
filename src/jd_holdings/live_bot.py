from __future__ import annotations

import logging

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.jh_auto_guard import mark_repository_as_jh_auto_live
from jd_holdings.application.jh_auto_runtime_services import (
    JHAutoLiveAllocationTradingService,
)
from jd_holdings.application.live_commissioning import arm_live_startup_buy_halt
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.automation.final_ops_hardening import (
    FinalOpsProductionJHAutoService as ProductionJHAutoService,
)
from jd_holdings.bot import configure_logging, recover_unapplied_core_fills
from jd_holdings.config import load_config
from jd_holdings.infrastructure.final_ops_runtime import (
    FinalOpsLiveInitialOnboardingPortfolioService as HardenedLiveInitialOnboardingPortfolioService,
)
from jd_holdings.infrastructure.final_ops_runtime import (
    FinalOpsOrderMonitor as OrderMonitor,
)
from jd_holdings.infrastructure.final_ops_runtime import LiveRuntimeLock
from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp
from jd_holdings.infrastructure.live_runtime_resilience import (
    ResilientReadTossClient as TossClient,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.settings import load_runtime_settings


def _run_locked_live(settings) -> None:
    config = load_config(settings.config_path)
    if not config.portfolio.enabled:
        raise RuntimeError("V3.2.2 portfolio가 비활성화되어 있습니다")

    configure_logging(settings.log_path)
    repository = SQLiteRepository(settings.database_path, config)

    # Install AUTO schema/state before arming the low-level BUY barrier. Bootstrap is
    # deliberately fail-closed: launch_authorized=0, effective principal=0 and
    # startup_quarantine=1 on the first deployment. Deployment alone can never buy.
    ProductionJHAutoService.bootstrap_repository(repository)
    mark_repository_as_jh_auto_live(repository)

    # Every process start closes BUY first. JH AUTO may reopen only after a later
    # independent reconciliation cycle proves the runtime clean.
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
    trading_service = JHAutoLiveAllocationTradingService(
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
        market_clock,
    )
    portfolio_service = HardenedLiveInitialOnboardingPortfolioService(
        config,
        repository,
        broker,
        order_manager,
        data_source,
        market_clock,
        trading_mode=settings.trading_mode,
    )
    reconciliation_service = ReconciliationService(config, repository, broker)
    auto_service = ProductionJHAutoService(
        config,
        repository,
        broker,
        reconciliation_service,
        market_clock,
    )
    auto_service.arm_startup_quarantine()

    mismatches = reconciliation_service.run()
    if mismatches:
        logging.getLogger(__name__).error("live 시작 정합성 검사 실패: %s", mismatches)

    analysis_service = AnalysisService(config, repository, data_source, market_clock)
    app = JHAutoTelegramBotApp(
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
        auto_service=auto_service,
    )
    if mismatches:
        app.notify_startup_reconciliation(mismatches)
    app.run()


def main() -> None:
    """Run commissioned live JDSS strategy with JH AUTO fail-closed execution."""
    settings = load_runtime_settings()
    if settings.trading_mode != "live":
        raise RuntimeError("live runtime entrypoint는 JDSS_TRADING_MODE=live에서만 실행할 수 있습니다")
    settings.require_live_trading()

    # systemd normally guarantees one process, but the ledger lock also blocks an
    # accidental manual second process from running schedulers against the same DB.
    with LiveRuntimeLock(settings.database_path):
        _run_locked_live(settings)


if __name__ == "__main__":
    main()
