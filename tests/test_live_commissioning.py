from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_commissioning import (
    LIVE_COMMISSIONED_KEY,
    LiveCommissioningPreflight,
    LiveReleaseGate,
    arm_live_startup_buy_halt,
)
from jd_holdings.application.operational_safety import OPERATOR_BUY_HALT_KEY
from jd_holdings.core.models import OrderRequest


class _AccountClient:
    def __init__(
        self,
        *,
        holdings=None,
        open_orders=None,
        buying_power=Decimal("50000"),
    ) -> None:
        self.holdings = holdings or []
        self.open_orders = open_orders or []
        self.buying_power = buying_power

    def get_holdings(self):
        return list(self.holdings)

    def list_orders(self, *, status):
        assert status == "OPEN"
        return list(self.open_orders)

    def get_buying_power(self, currency="USD"):
        assert currency == "USD"
        return self.buying_power


def test_fresh_separate_live_db_passes_empty_account_preflight(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    preflight = LiveCommissioningPreflight(repository, _AccountClient())

    result = preflight.run()

    assert result.safe
    assert result.issues == ()
    assert result.buying_power == Decimal("50000")


def test_live_preflight_arms_commissioned_fail_closed_buy_halt(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    preflight = LiveCommissioningPreflight(repository, _AccountClient())

    assert preflight.run().safe
    preflight.arm_buy_halt()

    assert repository.get_system_value(LIVE_COMMISSIONED_KEY) == "1"
    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"


def test_live_startup_requires_commissioned_ledger(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)

    with pytest.raises(RuntimeError, match="commissioning"):
        arm_live_startup_buy_halt(repository)


def test_live_restart_always_rearms_buy_halt(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    preflight = LiveCommissioningPreflight(repository, _AccountClient())
    assert preflight.run().safe
    preflight.arm_buy_halt()
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "0")

    arm_live_startup_buy_halt(repository)

    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
    assert any(
        event["event_type"] == "LIVE_STARTUP_BUY_HALT_ARMED"
        for event in repository.recent_events(10)
    )


def test_live_preflight_rejects_existing_managed_real_holding(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    client = _AccountClient(
        holdings=[{"symbol": "TQQQ", "quantity": "1"}],
    )

    result = LiveCommissioningPreflight(repository, client).run()

    assert not result.safe
    assert "REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:TQQQ" in result.issues


def test_live_preflight_rejects_reused_nonfresh_ledger(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    assert repository.reserve_order(
        client_order_id="OLD-DRY-ORDER",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="CORE_REBALANCE_SELL",
    )

    result = LiveCommissioningPreflight(repository, _AccountClient()).run()

    assert not result.safe
    assert "LIVE_DB_NOT_FRESH:orders:1" in result.issues


def test_live_preflight_rejects_insufficient_buying_power(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    client = _AccountClient(buying_power=Decimal("49999"))

    result = LiveCommissioningPreflight(repository, client).run()

    assert not result.safe
    assert any(issue.startswith("LIVE_BUYING_POWER_BELOW_CAPITAL:") for issue in result.issues)


def _release_broker() -> DryRunBroker:
    return DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )


def _commission_release_ledger(repository: SQLiteRepository) -> None:
    repository.set_system_value(LIVE_COMMISSIONED_KEY, "1")
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")


def test_live_release_gate_requires_commissioning_and_buy_halt(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "release.db", config)
    result = LiveReleaseGate(repository, _release_broker()).run()

    assert not result.safe
    assert "LIVE_RELEASE_NOT_COMMISSIONED" in result.issues
    assert "LIVE_RELEASE_BUY_HALT_NOT_ARMED" in result.issues


def test_live_release_gate_allows_reconciled_existing_managed_position(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "release.db", config)
    broker = _release_broker()
    request = OrderRequest(
        client_order_id="LIVE-RELEASE-SEED",
        symbol="TQQQ",
        side="BUY",
        order_type="MARKET",
        quantity=2,
        price=None,
        purpose="CORE_REBALANCE_BUY",
    )
    receipt = broker.place_order(request)
    assert receipt.status == "FILLED"
    assert repository.reserve_order(
        client_order_id=request.client_order_id,
        signal_id=None,
        cycle_id=None,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        price=request.price,
        quantity=request.quantity,
        purpose=request.purpose,
    )
    repository.update_order(
        request.client_order_id,
        status="FILLED",
        broker_order_id=receipt.broker_order_id,
        filled_qty=2,
        average_fill_price=Decimal("100"),
        raw=receipt.raw,
    )
    repository.apply_core_fill(request.client_order_id)
    _commission_release_ledger(repository)

    result = LiveReleaseGate(repository, broker).run()

    assert result.safe
    assert result.issues == ()


def test_live_release_gate_rejects_even_matching_open_order(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "release.db", config)
    broker = _release_broker()
    request = OrderRequest(
        client_order_id="LIVE-RELEASE-PENDING",
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("101"),
        purpose="CORE_REBALANCE_SELL",
    )
    receipt = broker.place_order(request)
    assert receipt.status == "PENDING"
    assert repository.reserve_order(
        client_order_id=request.client_order_id,
        signal_id=None,
        cycle_id=None,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        price=request.price,
        quantity=request.quantity,
        purpose=request.purpose,
    )
    repository.update_order(
        request.client_order_id,
        status="PENDING",
        broker_order_id=receipt.broker_order_id,
        raw=receipt.raw,
    )
    _commission_release_ledger(repository)

    result = LiveReleaseGate(repository, broker).run()

    assert not result.safe
    assert "LIVE_RELEASE_LOCAL_OPEN_ORDERS:1" in result.issues
    assert "LIVE_RELEASE_BROKER_OPEN_ORDERS:TQQQ:1" in result.issues
    assert not any("RECONCILIATION_FAILED" in issue for issue in result.issues)


def test_live_release_gate_rejects_broker_ledger_mismatch(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "release.db", config)
    broker = _release_broker()
    broker.holdings["TQQQ"] = {
        "quantity": 1,
        "averagePurchasePrice": Decimal("100"),
    }
    _commission_release_ledger(repository)

    result = LiveReleaseGate(repository, broker).run()

    assert not result.safe
    assert any(
        issue.startswith("LIVE_RELEASE_RECONCILIATION_FAILED:TQQQ:")
        for issue in result.issues
    )
