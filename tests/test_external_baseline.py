from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.external_baseline import (
    capture_external_baseline,
    external_baseline_quantity,
)
from jd_holdings.application.managed_account import marked_managed_equity
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.reconciliation import ReconciliationService


def _broker() -> DryRunBroker:
    return DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )


def test_matching_external_baseline_reconciles_without_becoming_jdss_inventory(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "baseline.db", config)
    broker = _broker()
    broker.holdings["SOXL"] = {
        "quantity": 7,
        "averagePurchasePrice": Decimal("40"),
    }
    capture_external_baseline(repository, {"SOXL": 7})

    result = ReconciliationService(config, repository, broker).run()

    assert result == {}
    assert external_baseline_quantity(repository, "SOXL") == 7
    assert int(repository.get_core_position("SOXL")["qty"]) == 0


def test_external_baseline_plus_jdss_core_quantity_reconciles_as_two_ledgers(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "baseline.db", config)
    broker = _broker()
    capture_external_baseline(repository, {"SOXL": 7})
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE core_positions SET qty = 3, avg_price = '50', cost_basis = '150' "
            "WHERE symbol = 'SOXL'"
        )
    broker.holdings["SOXL"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("43"),
    }

    result = ReconciliationService(config, repository, broker).run()

    assert result == {}
    assert int(repository.get_core_position("SOXL")["qty"]) == 3
    assert external_baseline_quantity(repository, "SOXL") == 7


def test_manual_change_to_external_baseline_triggers_safe_mode(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "baseline.db", config)
    broker = _broker()
    capture_external_baseline(repository, {"SOXL": 7})
    broker.holdings["SOXL"] = {
        "quantity": 6,
        "averagePurchasePrice": Decimal("40"),
    }

    result = ReconciliationService(config, repository, broker).run()

    assert "SOXL" in result
    assert "BROKER_BELOW_EXTERNAL_BASELINE" in result["SOXL"]
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"


def test_external_baseline_is_excluded_from_jdss_hwm_equity(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "baseline.db", config)
    broker = _broker()
    capture_external_baseline(repository, {"SOXL": 7})
    broker.holdings["SOXL"] = {
        "quantity": 7,
        "averagePurchasePrice": Decimal("40"),
    }

    equity = marked_managed_equity(config, repository, broker)

    assert equity == Decimal(str(config.total_strategy_capital))


class _RecordingOrderManager:
    def __init__(self) -> None:
        self.requests = []

    def submit(self, request, *, cycle_id):
        self.requests.append((request, cycle_id))
        return SimpleNamespace(
            status="PENDING",
            filled_quantity=0,
            quantity=request.quantity,
        )


def test_risk_reducing_sell_never_includes_external_baseline_quantity(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "baseline.db", config)
    broker = _broker()
    capture_external_baseline(repository, {"SOXL": 7})
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE core_positions
            SET qty = 3, avg_price = '50', cost_basis = '150',
                target_qty = 0, target_weight = '0', signal_trade_date = '2026-08-25'
            WHERE symbol = 'SOXL'
            """
        )
    broker.holdings["SOXL"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("43"),
    }
    order_manager = _RecordingOrderManager()
    service = PortfolioService(
        config,
        repository,
        broker,
        order_manager,
        data_source=None,
        market_clock=None,
        trading_mode="dry_run",
    )

    _signal, event = service._apply_target(
        "SOXL",
        datetime(2026, 8, 25, tzinfo=UTC).date(),
        allow_buy=False,
        current=datetime(2026, 8, 26, tzinfo=UTC),
    )

    assert event is not None
    assert len(order_manager.requests) == 1
    request, cycle_id = order_manager.requests[0]
    assert cycle_id is None
    assert request.side == "SELL"
    assert request.quantity == 3
    assert request.quantity != 10
