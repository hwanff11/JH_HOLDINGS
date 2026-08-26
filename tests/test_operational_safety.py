from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.operational_safety import (
    OPERATOR_BUY_HALT_KEY,
    OperatorSafetyService,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.core.models import OrderRequest
from jd_holdings.settings import RuntimeSettings


class _Reconciliation:
    def __init__(self, result=None) -> None:
        self.result = result or {}
        self.calls = 0

    def run(self):
        self.calls += 1
        return self.result


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "ops.db",
        log_path=tmp_path / "ops.log",
    )


def test_operator_halt_blocks_buy_at_order_manager_boundary(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    manager = OrderManager(repository, broker, _settings(tmp_path))
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")

    request = OrderRequest(
        client_order_id="HALT-BUY-1",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("100"),
        purpose="CORE_REBALANCE_BUY",
    )

    with pytest.raises(RuntimeError, match="긴급정지"):
        manager.submit(request, cycle_id=None)

    assert broker.sequence == 0
    assert repository.get_order_by_client_id(request.client_order_id) is None


def test_invalid_live_confirmation_fails_before_order_reservation(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    settings = RuntimeSettings(
        trading_mode="live",
        live_confirmation="WRONG_CONFIRMATION",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "ops.db",
        log_path=tmp_path / "ops.log",
    )
    manager = OrderManager(repository, broker, settings)
    request = OrderRequest(
        client_order_id="LOCKED-LIVE-BUY",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("100"),
        purpose="CORE_REBALANCE_BUY",
    )

    with pytest.raises(PermissionError, match="실주문 잠금"):
        manager.submit(request, cycle_id=None)

    assert broker.sequence == 0
    assert repository.get_order_by_client_id(request.client_order_id) is None


def test_operator_halt_keeps_risk_reducing_sell_available(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("49900"))
    broker.holdings["TQQQ"] = {
        "quantity": 1,
        "averagePurchasePrice": Decimal("100"),
    }
    manager = OrderManager(repository, broker, _settings(tmp_path))
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")

    receipt = manager.submit(
        OrderRequest(
            client_order_id="HALT-SELL-1",
            symbol="TQQQ",
            side="SELL",
            order_type="LIMIT",
            quantity=1,
            price=Decimal("99"),
            purpose="CORE_REBALANCE_SELL",
        ),
        cycle_id=None,
    )

    assert receipt.status == "FILLED"
    assert receipt.filled_quantity == 1
    assert broker.holdings["TQQQ"]["quantity"] == 0


def test_halt_sets_barrier_before_canceling_open_buys(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    request = OrderRequest(
        client_order_id="PENDING-BUY-1",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("90"),
        purpose="CORE_REBALANCE_BUY",
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
    )

    safety = OperatorSafetyService(repository, broker, _Reconciliation())
    result = safety.halt()

    assert safety.is_halted()
    assert result.canceled_order_ids == (request.client_order_id,)
    assert result.uncertain_order_ids == ()
    assert broker.get_order(receipt.broker_order_id)["status"] == "CANCELED"


def test_resume_requires_clean_reconciliation(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    reconciliation = _Reconciliation({"TQQQ": ["BROKER_DB_QTY_MISMATCH"]})
    safety = OperatorSafetyService(repository, broker, reconciliation)

    with pytest.raises(RuntimeError, match="정합성 오류"):
        safety.resume()

    assert safety.is_halted()
    assert reconciliation.calls == 1


def test_resume_clears_halt_only_after_clean_state(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    reconciliation = _Reconciliation()
    safety = OperatorSafetyService(repository, broker, reconciliation)

    result = safety.resume()

    assert result["resumed"] is True
    assert not safety.is_halted()
    assert reconciliation.calls == 1
