from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.core.models import OrderRequest
from jd_holdings.infrastructure.live_runtime_hardening import (
    HardenedLiveInitialOnboardingPortfolioService,
    HardenedOperationalSafetyTelegramBotApp,
    _live_order_monitor_interval,
)
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "sell-hardening.db",
        log_path=tmp_path / "sell-hardening.log",
    )


def _seed_pending_core_sell(tmp_path, config, broker):
    repository = SQLiteRepository(tmp_path / "sell-hardening.db", config)
    manager = OrderManager(repository, broker, _settings(tmp_path))
    buy = OrderRequest(
        client_order_id="SEED-CORE-BUY",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=2,
        price=Decimal("100"),
        purpose="CORE_REBALANCE_BUY",
    )
    assert manager.submit(buy, cycle_id=None).status == "FILLED"
    repository.apply_core_fill(buy.client_order_id)

    sell = OrderRequest(
        client_order_id="PENDING-CORE-SELL",
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        quantity=2,
        price=Decimal("101"),
        purpose="CORE_REBALANCE_SELL",
    )
    receipt = manager.submit(sell, cycle_id=None)
    assert receipt.status == "PENDING"
    assert repository.get_core_position("TQQQ")["qty"] == 2
    return repository, sell, receipt


def test_reconciliation_applies_completed_sell_before_holdings_compare(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, sell, _receipt = _seed_pending_core_sell(tmp_path, config, broker)
    broker.set_price("TQQQ", Decimal("102"))
    broker.fill_open_orders("TQQQ")

    assert repository.get_core_position("TQQQ")["qty"] == 2
    assert broker.get_holdings("TQQQ") == []

    issues = ReconciliationService(config, repository, broker).run()

    assert issues == {}
    assert repository.get_core_position("TQQQ")["qty"] == 0
    assert repository.get_order_by_client_id(sell.client_order_id)["status"] == "FILLED"
    assert repository.get_system_value("v322_portfolio_safe_mode") != "1"


def test_reconciliation_allows_holdings_lag_after_order_fill(tmp_path, config):
    class HoldingsLagBroker(DryRunBroker):
        return_stale_holding_once = False

        def get_holdings(self, symbol=None):
            if self.return_stale_holding_once:
                self.return_stale_holding_once = False
                if symbol is None or symbol.upper() == "TQQQ":
                    return [{"symbol": "TQQQ", "quantity": "2"}]
            return super().get_holdings(symbol)

    broker = HoldingsLagBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, sell, _receipt = _seed_pending_core_sell(tmp_path, config, broker)
    broker.set_price("TQQQ", Decimal("102"))
    broker.fill_open_orders("TQQQ")
    broker.return_stale_holding_once = True

    first = ReconciliationService(config, repository, broker).run()

    assert first == {}
    assert repository.get_core_position("TQQQ")["qty"] == 0
    assert repository.get_order_by_client_id(sell.client_order_id)["status"] == "FILLED"
    assert repository.get_system_value("v322_portfolio_safe_mode") != "1"

    second = ReconciliationService(config, repository, broker).run()
    assert second == {}


def test_reconciliation_defers_fill_race_within_open_sell_envelope(tmp_path, config):
    class FillAfterRefreshBroker(DryRunBroker):
        fill_during_holdings = False

        def get_holdings(self, symbol=None):
            if self.fill_during_holdings:
                self.fill_during_holdings = False
                self.set_price("TQQQ", Decimal("102"))
                self.fill_open_orders("TQQQ")
            return super().get_holdings(symbol)

    broker = FillAfterRefreshBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, sell, _receipt = _seed_pending_core_sell(tmp_path, config, broker)
    broker.fill_during_holdings = True

    first = ReconciliationService(config, repository, broker).run()

    assert first == {}
    assert repository.get_core_position("TQQQ")["qty"] == 2
    assert repository.get_order_by_client_id(sell.client_order_id)["status"] == "PENDING"
    assert repository.get_system_value("v322_portfolio_safe_mode") != "1"

    second = ReconciliationService(config, repository, broker).run()

    assert second == {}
    assert repository.get_core_position("TQQQ")["qty"] == 0
    assert repository.get_order_by_client_id(sell.client_order_id)["status"] == "FILLED"


def test_reconciliation_does_not_hide_difference_outside_open_sell_envelope(
    tmp_path, config
):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, _sell, _receipt = _seed_pending_core_sell(tmp_path, config, broker)
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }

    issues = ReconciliationService(config, repository, broker).run()

    assert "BROKER_DB_QTY_MISMATCH:10!=2" in issues["TQQQ"]
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"


def test_live_portfolio_skips_toss_maintenance_without_touching_dependencies():
    service = object.__new__(HardenedLiveInitialOnboardingPortfolioService)
    maintenance_utc = datetime(2026, 8, 27, 23, 55, tzinfo=UTC)  # 08:55 KST

    assert service.run_allocation(maintenance_utc) is None


class _OneCycleStop:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _seconds) -> bool:
        self.calls += 1
        return self.calls > 1


class _Clock:
    def latest_completed_session(self, *, delay_minutes):
        del delay_minutes
        return date(2026, 8, 27)


class _Monitor:
    def __init__(self, calls):
        self.calls = calls

    def run_once(self):
        self.calls.append("monitor")
        return []


class _Reconciliation:
    def __init__(self, calls):
        self.calls = calls

    def run(self):
        self.calls.append("reconcile")
        return {}


class _Portfolio:
    def __init__(self, calls):
        self.calls = calls

    def run_month_end(self):
        self.calls.append("portfolio")
        return None


class _Analysis:
    def __init__(self, calls):
        self.calls = calls

    def analyze_all(self):
        self.calls.append("analysis")
        return []


def _scheduler_app(tmp_path, config, calls):
    app = object.__new__(HardenedOperationalSafetyTelegramBotApp)
    app.config = config
    app.repository = SQLiteRepository(tmp_path / "scheduler.db", config)
    app.market_clock = _Clock()
    app.order_monitor = _Monitor(calls)
    app.reconciliation_service = _Reconciliation(calls)
    app.portfolio_service = _Portfolio(calls)
    app.analysis_service = _Analysis(calls)
    app.idle_cash_manager = None
    app.settings = SimpleNamespace(trading_mode="dry_run")
    app._stop = _OneCycleStop()
    app._last_monitor = -1_000_000_000.0
    app._last_idle_cash_sweep = -1_000_000_000.0
    app._reconciliation_notice_at = {}
    app._send = lambda *args, **kwargs: None
    app._send_reconciliation_alert = lambda *args, **kwargs: None
    app._notify_runtime_error = lambda event_type, title, exc: (_ for _ in ()).throw(exc)
    app.notify_portfolio_buy_batch_ready = lambda *args, **kwargs: None
    app.notify_new_signals = lambda *args, **kwargs: None
    return app


def test_live_scheduler_settles_and_reconciles_before_portfolio(
    tmp_path, config, monkeypatch
):
    calls = []
    app = _scheduler_app(tmp_path, config, calls)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_hardening.is_toss_order_maintenance_window",
        lambda _now: False,
    )
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_hardening._daily_analysis_is_due",
        lambda *_args: True,
    )

    app._scheduler_loop()

    assert calls[:3] == ["monitor", "reconcile", "portfolio"]
    assert "analysis" in calls


def test_live_scheduler_skips_broker_jobs_during_toss_maintenance(
    tmp_path, config, monkeypatch
):
    calls = []
    app = _scheduler_app(tmp_path, config, calls)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_hardening.is_toss_order_maintenance_window",
        lambda _now: True,
    )
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_hardening._daily_analysis_is_due",
        lambda *_args: True,
    )

    app._scheduler_loop()

    assert calls == ["analysis"]


def test_live_order_monitor_uses_30_second_effective_cadence(config):
    assert config.scheduler.poll_interval_seconds == 30
    assert config.scheduler.order_monitor_interval_seconds == 60
    assert _live_order_monitor_interval(config) == 30
