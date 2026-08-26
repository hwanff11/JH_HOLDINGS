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


def _seed_pending_buy(repository, broker, *, client_order_id="PENDING-BUY-1"):
    request = OrderRequest(
        client_order_id=client_order_id,
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
    return request, receipt


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


def test_halt_marks_cancel_complete_only_after_original_order_is_canceled(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    request, receipt = _seed_pending_buy(repository, broker)

    safety = OperatorSafetyService(repository, broker, _Reconciliation())
    result = safety.halt()

    assert safety.is_halted()
    assert result.canceled_order_ids == (request.client_order_id,)
    assert result.uncertain_order_ids == ()
    assert broker.get_order(receipt.broker_order_id)["status"] == "CANCELED"


def test_halt_keeps_pending_cancel_uncertain(tmp_path, config):
    class PendingCancelBroker(DryRunBroker):
        def cancel_order(self, order_id: str) -> str:
            self.orders[order_id]["status"] = "PENDING_CANCEL"
            return "CANCEL-OP-1"

    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = PendingCancelBroker({"TQQQ": Decimal("100")})
    request, receipt = _seed_pending_buy(repository, broker)

    result = OperatorSafetyService(repository, broker, _Reconciliation()).halt()

    assert result.canceled_order_ids == ()
    assert result.uncertain_order_ids == (request.client_order_id,)
    assert broker.get_order(receipt.broker_order_id)["status"] == "PENDING_CANCEL"


def test_halt_keeps_concurrent_fill_uncertain(tmp_path, config):
    class FilledDuringCancelBroker(DryRunBroker):
        def cancel_order(self, order_id: str) -> str:
            self.orders[order_id]["status"] = "FILLED"
            self.orders[order_id]["execution"]["filledQuantity"] = "1"
            self.orders[order_id]["execution"]["averageFilledPrice"] = "100"
            self.orders[order_id]["execution"]["filledAmount"] = "100"
            return "CANCEL-OP-2"

    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = FilledDuringCancelBroker({"TQQQ": Decimal("100")})
    request, receipt = _seed_pending_buy(repository, broker)

    result = OperatorSafetyService(repository, broker, _Reconciliation()).halt()

    assert result.canceled_order_ids == ()
    assert result.uncertain_order_ids == (request.client_order_id,)
    assert broker.get_order(receipt.broker_order_id)["status"] == "FILLED"


def test_halt_keeps_cancel_uncertain_when_original_order_lookup_fails(tmp_path, config):
    class LookupFailureBroker(DryRunBroker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fail_lookup = False

        def cancel_order(self, order_id: str) -> str:
            self.orders[order_id]["status"] = "CANCELED"
            self.fail_lookup = True
            return "CANCEL-OP-3"

        def get_order(self, order_id: str):
            if self.fail_lookup:
                raise RuntimeError("temporary lookup failure")
            return super().get_order(order_id)

    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = LookupFailureBroker({"TQQQ": Decimal("100")})
    request, _ = _seed_pending_buy(repository, broker)

    result = OperatorSafetyService(repository, broker, _Reconciliation()).halt()

    assert result.canceled_order_ids == ()
    assert result.uncertain_order_ids == (request.client_order_id,)


def test_halt_rejects_canceled_snapshot_for_different_order_id(tmp_path, config):
    class WrongOrderSnapshotBroker(DryRunBroker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.return_wrong_snapshot = False

        def cancel_order(self, order_id: str) -> str:
            self.orders[order_id]["status"] = "CANCELED"
            self.return_wrong_snapshot = True
            return "CANCEL-OP-4"

        def get_order(self, order_id: str):
            raw = super().get_order(order_id)
            if self.return_wrong_snapshot:
                raw["orderId"] = "OTHER-ORDER-ID"
            return raw

    repository = SQLiteRepository(tmp_path / "ops.db", config)
    broker = WrongOrderSnapshotBroker({"TQQQ": Decimal("100")})
    request, _ = _seed_pending_buy(repository, broker)

    result = OperatorSafetyService(repository, broker, _Reconciliation()).halt()

    assert result.canceled_order_ids == ()
    assert result.uncertain_order_ids == (request.client_order_id,)


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
