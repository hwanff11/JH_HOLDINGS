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
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.operational_safety_telegram import (
    _live_mode_operator_text,
)
from jd_holdings.settings import RuntimeSettings


class RecordingBroker:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    def get_buying_power(self, currency: str = "USD") -> Decimal:
        assert currency == "USD"
        return Decimal("50000")

    def place_order(self, request: OrderRequest) -> OrderReceipt:
        self.requests.append(request)
        return OrderReceipt(
            client_order_id=request.client_order_id,
            broker_order_id="BROKER-1",
            status="PENDING",
            quantity=request.quantity,
            filled_quantity=0,
            average_fill_price=None,
            raw={
                "clientOrderId": request.client_order_id,
                "symbol": request.symbol,
                "side": request.side,
                "quantity": str(request.quantity),
            },
        )


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "review.db",
        log_path=tmp_path / "review.log",
    )


def test_sell_limit_is_floored_to_us_tick_before_broker_submit(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "sell.db", config)
    broker = RecordingBroker()
    manager = OrderManager(repository, broker, _settings(tmp_path))
    raw_price = Decimal("69.97") * (Decimal("1") - Decimal("0.005"))

    receipt = manager.submit(
        OrderRequest(
            client_order_id="SELL-TICK",
            symbol="TQQQ",
            side="SELL",
            order_type="LIMIT",
            quantity=1,
            price=raw_price,
            purpose="CORE_REBALANCE_SELL",
        ),
        cycle_id=None,
    )

    assert receipt.status == "PENDING"
    assert broker.requests[0].price == Decimal("69.62")
    assert Decimal(repository.get_order_by_client_id("SELL-TICK")["price"]) == Decimal(
        "69.62"
    )


def test_sell_limit_under_one_dollar_uses_four_decimal_tick():
    normalized = OrderManager._normalize_sell_limit_request(
        OrderRequest(
            client_order_id="SELL-SUB1",
            symbol="TEST",
            side="SELL",
            order_type="LIMIT",
            quantity=1,
            price=Decimal("0.982662"),
            purpose="TEST",
        )
    )

    assert normalized.price == Decimal("0.9826")


def test_order_manager_blocks_buy_before_reservation_when_safe_mode_is_sticky(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "safe.db", config)
    repository.set_system_value("v322_portfolio_safe_mode", "1")
    broker = RecordingBroker()
    manager = OrderManager(repository, broker, _settings(tmp_path))
    request = OrderRequest(
        client_order_id="SAFE-BEFORE",
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("500"),
        purpose="TEST_BUY",
    )

    with pytest.raises(RuntimeError, match="SAFE_MODE"):
        manager.submit(request, cycle_id=None)

    assert repository.get_order_by_client_id(request.client_order_id) is None
    assert broker.requests == []


def test_order_manager_rechecks_safe_mode_at_final_broker_boundary(
    tmp_path, config, monkeypatch
):
    repository = SQLiteRepository(tmp_path / "race.db", config)
    broker = RecordingBroker()
    manager = OrderManager(repository, broker, _settings(tmp_path))
    checks = iter((False, True))
    monkeypatch.setattr(
        manager,
        "_buy_is_safe_mode_blocked",
        lambda _symbol: next(checks),
    )
    request = OrderRequest(
        client_order_id="SAFE-RACE",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("100"),
        purpose="TEST_BUY",
    )

    with pytest.raises(RuntimeError, match="SAFE_MODE"):
        manager.submit(request, cycle_id=None)

    assert repository.get_order_by_client_id(request.client_order_id)["status"] == "REJECTED"
    assert broker.requests == []


def test_resume_refuses_to_clear_buy_halt_while_portfolio_safe_mode_is_sticky(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "resume.db", config)
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value("v322_portfolio_safe_mode", "1")
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    service = OperatorSafetyService(
        repository,
        broker,
        ReconciliationService(config, repository, broker),
    )

    with pytest.raises(RuntimeError, match="sticky SAFE_MODE"):
        service.resume()

    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"


def test_live_operator_guidance_does_not_claim_forced_dry_run():
    original = "• 실거래는 잠겨 있고 forced dry-run만 허용합니다."

    live = _live_mode_operator_text(original, "live")
    dry = _live_mode_operator_text(original, "dry_run")

    assert "forced dry-run" not in live
    assert "실계좌가 연결" in live
    assert "2단계 승인" in live
    assert dry == original

    help_text = "🧪 모의운용에서는 승인해도 실제 토스 주문이 전송되지 않습니다."
    live_help = _live_mode_operator_text(help_text, "live")
    assert "모의운용" not in live_help
    assert "신규 매수 잠금 해제와 2단계 승인" in live_help
