from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings import __version__
from jd_holdings.application.allocation_trading_service import AllocationTradingService
from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import ApprovalError, SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import QuoteChangedError
from jd_holdings.bot import restore_dry_run_orders
from jd_holdings.core.enums import PositionState
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings

APPROVAL_TIME = datetime(2026, 8, 4, 12, tzinfo=UTC)
SIGNAL_DATE = date(2026, 8, 3)


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


def _build_core_signal(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    market_clock = MarketClock()
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
        valid_until=market_clock.next_session_close(SIGNAL_DATE),
        code_version=__version__,
    )
    assert created
    trading = AllocationTradingService(
        config,
        repository,
        broker,
        order_manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, order_manager),
        market_clock,
    )
    return repository, broker, trading, signal_id


def test_core_quote_never_exceeds_signal_time_planned_quantity(tmp_path, config):
    repository, broker, trading, signal_id = _build_core_signal(tmp_path, config)
    del repository
    broker.set_price("TQQQ", Decimal("90"))

    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)

    assert quote.quantity == 49


def test_core_quote_blocks_signal_day_after_hours(tmp_path, config):
    _repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    same_day_after_hours = datetime(2026, 8, 3, 21, 0, tzinfo=UTC)

    review_id, review_token = trading.create_review_approval(
        signal_id, now=same_day_after_hours
    )
    with pytest.raises(ApprovalError, match="다음 거래일부터"):
        trading.consume_review(review_id, review_token, now=same_day_after_hours)


def test_core_execution_rechecks_session_after_quote(tmp_path, config):
    _repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    premarket = datetime(2026, 8, 4, 13, 29, 50, tzinfo=UTC)
    regular_open = datetime(2026, 8, 4, 13, 30, 1, tzinfo=UTC)

    review_id, review_token = trading.create_review_approval(signal_id, now=premarket)
    quote = trading.consume_review(review_id, review_token, now=premarket)
    with pytest.raises(QuoteChangedError, match="새로운 최종 확인"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=regular_open,
        )


def test_old_review_cancel_also_cancels_active_execution_approval(tmp_path, config):
    repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)

    assert trading.cancel_approval(review_id)
    with pytest.raises(ApprovalError, match="이미 사용되었거나 취소"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=APPROVAL_TIME,
        )


def test_execution_expiry_message_uses_seconds_not_review_minutes(tmp_path, config):
    repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
            ((APPROVAL_TIME - timedelta(seconds=1)).isoformat(), quote.execution_approval_id),
        )

    with pytest.raises(ApprovalError, match=r"EXECUTION 승인 유효시간\(60초\)"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=APPROVAL_TIME,
        )


def test_core_quote_reduces_quantity_when_core_was_filled_after_signal(tmp_path, config):
    repository, broker, trading, signal_id = _build_core_signal(tmp_path, config)
    client_id = "CORE-CONCURRENT-FILL"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=10,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="FILLED",
        broker_order_id="DRY-CORE-CONCURRENT",
        filled_qty=10,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(client_id)
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }
    broker.buying_power = Decimal("49000")

    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)

    assert quote.quantity == 39


def test_core_quote_reserves_quantity_for_an_open_core_buy(tmp_path, config):
    repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    assert repository.reserve_order(
        client_order_id="CORE-PENDING-BUY",
        signal_id=signal_id,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=30,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        "CORE-PENDING-BUY",
        status="PENDING",
        broker_order_id="DRY-CORE-PENDING",
    )

    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)

    assert quote.quantity == 19


def test_core_signal_is_invalidated_when_symbol_is_in_safe_mode(tmp_path, config):
    repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    position = repository.get_position("TQQQ")
    repository.transition_position(
        "TQQQ",
        expected_state=position.state,
        new_state=PositionState.SAFE_MODE,
        reason_code="TEST_SAFE_MODE",
        expected_version=position.version,
    )

    with pytest.raises(ApprovalError, match="SIGNAL_SAFE_MODE"):
        trading.create_review_approval(signal_id, now=APPROVAL_TIME)

    assert repository.get_signal(signal_id)["status"] == "INVALID"


def test_disabled_idle_cash_safe_mode_flag_does_not_block_allocation(tmp_path, config):
    repository, _broker, trading, signal_id = _build_core_signal(tmp_path, config)
    repository.set_system_value("idle_cash_safe_mode", "1")

    review_id, _ = trading.create_review_approval(signal_id, now=APPROVAL_TIME)

    assert review_id > 0
    assert repository.get_signal(signal_id)["status"] == "ACTIVE"


def test_v322_rejects_legacy_direct_buy_signal(tmp_path, config):
    repository, _broker, trading, _ = _build_core_signal(tmp_path, config)
    snapshot = make_snapshot(close=Decimal("100"))
    score = make_score(84)
    decision = evaluate_entry(
        snapshot,
        score,
        repository.get_position("TQQQ"),
        config,
    )
    assert decision.allowed
    signal_id, created = repository.create_signal(
        symbol="TQQQ",
        trade_date=SIGNAL_DATE,
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=APPROVAL_TIME + timedelta(days=1),
        code_version="legacy-direct-signal-test",
        cycle_id=None,
    )
    assert created

    with pytest.raises(ApprovalError, match="V322_DIRECT_SIGNAL_DISABLED"):
        trading.create_review_approval(signal_id, now=APPROVAL_TIME)

    signal = repository.get_signal(signal_id)
    assert signal["status"] == "INVALID"
    assert signal["expired_reason"] == "V322_DIRECT_SIGNAL_DISABLED"


def test_restart_sequence_uses_completed_historical_dry_orders(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    assert repository.reserve_order(
        client_order_id="COMPLETED-HISTORY",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="ENTRY_1",
    )
    repository.update_order(
        "COMPLETED-HISTORY",
        status="FILLED",
        broker_order_id="DRY-00000042",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("49900"))

    restore_dry_run_orders(repository, broker)

    assert broker.sequence == 42
