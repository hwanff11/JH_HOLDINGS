from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from jd_holdings import __version__
from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_runtime_services import LiveAllocationTradingService
from jd_holdings.application.operational_safety import OPERATOR_BUY_HALT_KEY
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.operational_safety_telegram import (
    RESUME_CONFIRM_CALLBACK,
    RESUME_REVIEW_CALLBACK,
    OperationalSafetyTelegramBotApp,
)
from jd_holdings.settings import LIVE_CONFIRMATION_PHRASE, RuntimeSettings


def _live_settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="live",
        live_confirmation=LIVE_CONFIRMATION_PHRASE,
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "jdss-live.db",
        log_path=tmp_path / "live.log",
    )


def _build_live_buy(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss-live.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    settings = _live_settings(tmp_path)
    manager = OrderManager(repository, broker, settings)
    clock = MarketClock()
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("0.50"),
        signal_trade_date=date(2026, 8, 3),
    )
    signal_id, created = repository.create_core_buy_signal(
        symbol="QQQ",
        trade_date=date(2026, 8, 3),
        signal_close=Decimal("500"),
        planned_budget=Decimal("25000"),
        valid_until=clock.next_session_close(date(2026, 8, 3)),
        code_version=__version__,
    )
    assert created
    trading = LiveAllocationTradingService(
        config,
        repository,
        broker,
        manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, manager),
        clock,
    )
    return repository, broker, trading, signal_id


def _approve(trading, signal_id):
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    review_id, review_token = trading.create_review_approval(signal_id, now=now)
    quote = trading.consume_review(review_id, review_token, now=now)
    return quote, now


def test_live_service_cannot_cross_broker_boundary_while_buy_halted(tmp_path, config):
    repository, broker, trading, signal_id = _build_live_buy(tmp_path, config)
    repository.set_system_value("live_commissioned", "1")
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    quote, now = _approve(trading, signal_id)

    with pytest.raises(RuntimeError, match="긴급정지"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=now,
        )

    assert broker.sequence == 0
    assert repository.open_orders("QQQ") == []
    assert repository.get_signal(signal_id)["status"] == "ACTIVE"


def test_live_service_submits_only_after_operator_halt_is_released(tmp_path, config):
    repository, broker, trading, signal_id = _build_live_buy(tmp_path, config)
    repository.set_system_value("live_commissioned", "1")
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "0")
    quote, now = _approve(trading, signal_id)

    receipt = trading.execute(
        quote.execution_approval_id,
        quote.execution_token,
        now=now,
    )

    assert receipt.status == "FILLED"
    assert broker.sequence == 1
    assert repository.get_core_position("QQQ")["qty"] == quote.quantity


def test_live_service_rejects_uncommissioned_or_missing_buy_halt_state(tmp_path, config):
    repository, broker, trading, signal_id = _build_live_buy(tmp_path, config)
    quote, now = _approve(trading, signal_id)

    with pytest.raises(RuntimeError, match="준비 전환"):
        trading.execute(quote.execution_approval_id, quote.execution_token, now=now)
    assert broker.orders == {}

    repository.set_system_value("live_commissioned", "1")
    quote, now = _approve(trading, signal_id)
    with pytest.raises(RuntimeError, match="잠금 상태"):
        trading.execute(quote.execution_approval_id, quote.execution_token, now=now)
    assert broker.orders == {}


def test_telegram_resume_uses_two_distinct_confirmation_buttons():
    review = OperationalSafetyTelegramBotApp._resume_review_markup()
    confirm = OperationalSafetyTelegramBotApp._resume_confirm_markup()

    assert review.keyboard[0][0].callback_data == RESUME_REVIEW_CALLBACK
    assert confirm.keyboard[0][0].callback_data == RESUME_CONFIRM_CALLBACK
    assert RESUME_REVIEW_CALLBACK != RESUME_CONFIRM_CALLBACK
