from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderRequest
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "terminal-sell.db",
        log_path=tmp_path / "terminal-sell.log",
    )


def test_reconciliation_terminalizes_incomplete_sell_into_safe_mode(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository = SQLiteRepository(tmp_path / "terminal-sell.db", config)
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.10"),
        target_qty=2,
        signal_trade_date=date(2026, 8, 26),
    )
    manager = OrderManager(repository, broker, _settings(tmp_path))

    buy = OrderRequest(
        client_order_id="TERMINAL-SEED-BUY",
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
        client_order_id="TERMINAL-PARTIAL-SELL",
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        quantity=2,
        price=Decimal("101"),
        purpose="CORE_REBALANCE_SELL",
    )
    receipt = manager.submit(sell, cycle_id=None)
    assert receipt.status == "PENDING"

    raw = broker.orders[receipt.broker_order_id]
    raw["status"] = "CANCELED"
    raw["execution"]["filledQuantity"] = "1"
    raw["execution"]["averageFilledPrice"] = "101"
    raw["execution"]["filledAmount"] = "101"
    raw["_appliedFilledQuantity"] = "1"
    raw["_appliedFilledAmount"] = "101"
    broker.holdings["TQQQ"] = {
        "quantity": 1,
        "averagePurchasePrice": Decimal("100"),
    }

    with pytest.raises(RuntimeError, match="SAFE_MODE"):
        ReconciliationService(config, repository, broker).run()

    assert repository.get_core_position("TQQQ")["qty"] == 1
    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"
    assert any(
        event["event_type"] == "CORE_SELL_INCOMPLETE"
        for event in repository.recent_events(20)
    )
