from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from jd_holdings import __version__
from jd_holdings.application.allocation_trading_service import AllocationTradingService
from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.telegram_bot_runtime import (
    RuntimeTelegramBotApp,
    _operator_text,
)
from jd_holdings.settings import RuntimeSettings

APPROVAL_TIME = datetime(2026, 8, 4, 12, tzinfo=UTC)
SIGNAL_DATE = date(2026, 8, 3)


class RejectedBroker(DryRunBroker):
    def place_order(self, request):
        self.sequence += 1
        return OrderReceipt(
            client_order_id=request.client_order_id,
            broker_order_id=f"REJECT-{self.sequence}",
            status="REJECTED",
            quantity=request.quantity,
            filled_quantity=0,
            average_fill_price=None,
            raw={"status": "REJECTED"},
        )


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "faults.db",
        log_path=tmp_path / "faults.log",
    )


def _core_signal(tmp_path, config, broker):
    repository = SQLiteRepository(tmp_path / "faults.db", config)
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    clock = MarketClock()
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.10"),
        signal_trade_date=SIGNAL_DATE,
    )
    signal_id, created = repository.create_core_buy_signal(
        symbol="TQQQ",
        trade_date=SIGNAL_DATE,
        signal_close=Decimal("100"),
        planned_budget=Decimal("5000"),
        valid_until=clock.next_session_close(SIGNAL_DATE),
        code_version=__version__,
    )
    assert created
    manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading = AllocationTradingService(
        config,
        repository,
        broker,
        order_manager,
        manager,
        tp_manager,
        clock,
    )
    monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        manager,
        tp_manager,
        clock,
    )
    return repository, trading, monitor, signal_id


def _manual_broker_order(
    *, order_id: str, client_id: str, side: str, quantity: int, filled: int, price: Decimal
):
    return {
        "orderId": order_id,
        "clientOrderId": client_id,
        "symbol": "TQQQ",
        "side": side,
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "status": "CANCELED",
        "price": str(price),
        "quantity": str(quantity),
        "execution": {
            "filledQuantity": str(filled),
            "averageFilledPrice": str(price) if filled else None,
            "filledAmount": str(price * filled) if filled else None,
            "commission": "0",
            "tax": "0",
            "filledAt": None,
            "settlementDate": None,
        },
        "_appliedFilledQuantity": str(filled),
        "_appliedFilledAmount": str(price * filled),
    }


def test_rejected_core_buy_reopens_same_signal_with_new_attempt_id(tmp_path, config):
    broker = RejectedBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, trading, _monitor, signal_id = _core_signal(tmp_path, config, broker)

    for _ in range(2):
        review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
        quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)
        receipt = trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=APPROVAL_TIME,
        )
        assert receipt.status == "REJECTED"
        signal = repository.get_signal(signal_id)
        assert signal["status"] == "ACTIVE"
        assert signal["processed"] == 0
        assert signal["expired_reason"] == "CORE_BUY_REAPPROVAL_REQUIRED"

    with repository.transaction() as connection:
        rows = connection.execute(
            "SELECT client_order_id FROM orders WHERE purpose = 'CORE_REBALANCE_BUY' "
            "ORDER BY order_id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["client_order_id"] != rows[1]["client_order_id"]
    assert any(
        event["event_type"] == "CORE_BUY_INCOMPLETE"
        for event in repository.recent_events(20)
    )


def test_canceled_partial_core_buy_reopens_remaining_signal(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, _trading, monitor, signal_id = _core_signal(tmp_path, config, broker)
    repository.mark_signal(signal_id, status="PROCESSED", processed=True)
    client_id = "CORE-PARTIAL-BUY"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=signal_id,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=3,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="PARTIAL_FILLED",
        broker_order_id="DRY-00000090",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(client_id)
    broker.orders["DRY-00000090"] = _manual_broker_order(
        order_id="DRY-00000090",
        client_id=client_id,
        side="BUY",
        quantity=3,
        filled=1,
        price=Decimal("100"),
    )

    events = monitor.run_once(now=APPROVAL_TIME)

    signal = repository.get_signal(signal_id)
    assert signal["status"] == "ACTIVE"
    assert signal["processed"] == 0
    assert repository.get_core_position("TQQQ")["qty"] == 1
    assert any("다시 승인이 필요" in event for event in events)


def test_expired_pending_core_buy_is_canceled_by_monitor(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, _trading, monitor, signal_id = _core_signal(tmp_path, config, broker)
    repository.mark_signal(signal_id, status="PROCESSED", processed=True)
    request = OrderRequest(
        client_order_id="EXPIRED-PENDING-CORE-BUY",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=3,
        price=Decimal("99"),
        purpose="CORE_REBALANCE_BUY",
        signal_id=signal_id,
    )
    manager = monitor.order_manager
    assert manager.submit(request, cycle_id=None).status == "PENDING"

    events = monitor.run_once(now=datetime(2026, 8, 6, 22, tzinfo=UTC))

    local = repository.get_order_by_client_id(request.client_order_id)
    assert local["status"] == "CANCELED"
    assert repository.get_signal(signal_id)["status"] == "EXPIRED"
    assert any("재승인 가능 시간이 지나" in event for event in events)


def test_incomplete_core_sell_enters_safe_mode(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("49800"),
    )
    repository = SQLiteRepository(tmp_path / "faults.db", config)
    runtime = _settings(tmp_path)
    order_manager = OrderManager(repository, broker, runtime)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
    )

    buy_id = "CORE-SEED"
    assert repository.reserve_order(
        client_order_id=buy_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=2,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        buy_id,
        status="FILLED",
        broker_order_id="DRY-00000091",
        filled_qty=2,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(buy_id)

    sell_id = "CORE-PARTIAL-SELL"
    assert repository.reserve_order(
        client_order_id=sell_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("99"),
        quantity=2,
        purpose="CORE_REBALANCE_SELL",
    )
    repository.update_order(
        sell_id,
        status="PARTIAL_FILLED",
        broker_order_id="DRY-00000092",
        filled_qty=1,
        average_fill_price=Decimal("99"),
    )
    repository.apply_core_fill(sell_id)
    broker.orders["DRY-00000092"] = _manual_broker_order(
        order_id="DRY-00000092",
        client_id=sell_id,
        side="SELL",
        quantity=2,
        filled=1,
        price=Decimal("99"),
    )

    events = monitor.run_once(now=APPROVAL_TIME)

    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
    assert repository.get_core_position("TQQQ")["qty"] == 1
    assert any("SAFE_MODE" in event for event in events)
    assert any(
        event["event_type"] == "CORE_SELL_INCOMPLETE"
        for event in repository.recent_events(20)
    )


def test_operator_text_distinguishes_live_order_states():
    source = "✅ <b>[모의주문 전송 완료]</b> ✨\n• <b>처리 상태</b> : <code>{}</code>"
    pending = _operator_text(source.format("PENDING"))
    partial = _operator_text(source.format("PARTIAL_FILLED"))
    filled = _operator_text(source.format("FILLED"))
    rejected = _operator_text(source.format("REJECTED"))
    canceled = _operator_text(source.format("CANCELED"))
    replaced = _operator_text(source.format("REPLACED"))
    unknown = _operator_text(source.format("UNKNOWN"))

    assert "실주문 접수" in pending and "/order" in pending
    assert "실주문 부분체결" in partial and "남은 수량" in partial
    assert "실주문 체결 완료" in filled and "/account" in filled
    assert "실주문 거부" in rejected and "새 승인 여부" in rejected
    assert "실주문 취소 확인" in canceled and "새 승인" in canceled
    assert "실주문 변경 확인 필요" in replaced and "/order" in replaced
    assert "실주문 결과 확인 필요" in unknown
    assert "재주문하지 말고" in unknown and "/account" in unknown
    assert "모의주문" not in pending
    assert "모의주문" not in partial
    assert "모의주문" not in filled
    assert "모의주문" not in rejected
    assert "모의주문" not in canceled
    assert "모의주문" not in replaced
    assert "모의주문" not in unknown


def test_runtime_error_is_persisted_and_telegram_notice_is_rate_limited(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "faults.db", config)
    app = object.__new__(RuntimeTelegramBotApp)
    app.repository = repository
    app._runtime_error_notice_at = {}
    sent: list[str] = []
    app._send = lambda text, **kwargs: sent.append(text)

    error = RuntimeError("temporary data failure")
    app._notify_runtime_error("TEST_RUNTIME_ERROR", "테스트 자동 점검 오류", error)
    app._notify_runtime_error("TEST_RUNTIME_ERROR", "테스트 자동 점검 오류", error)

    assert len(sent) == 1
    events = [
        event
        for event in repository.recent_events(10)
        if event["event_type"] == "TEST_RUNTIME_ERROR"
    ]
    assert len(events) == 2
