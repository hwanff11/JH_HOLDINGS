from __future__ import annotations

import threading
import time
from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.application.trading_service import QuoteChangedError
from jd_holdings.core.models import OrderReceipt
from jd_holdings.infrastructure.telegram_bot_v322 import (
    V322TelegramBotApp,
    _BatchBuyItem,
    _BatchBuyPlan,
    _format_batch_barrier,
)


class _Repo:
    def __init__(self, orders=None):
        self.orders = list(orders or [])

    def open_orders(self):
        return list(self.orders)

    def get_system_value(self, key):
        assert key == "v322_portfolio_safe_mode"
        return "0"

    def get_position(self, symbol):
        return SimpleNamespace(state="EMPTY")


class _Portfolio:
    def snapshot(self):
        return {"cash": Decimal("50000")}


class _Trading:
    def __init__(self):
        self.created = []
        self.consumed = []
        self.canceled = []
        self.executed = []
        self.signals = [
            {"signal_id": 2, "symbol": "TQQQ"},
            {"signal_id": 1, "symbol": "QQQ"},
        ]

    def active_signals(self):
        return list(self.signals)

    def create_review_approval(self, signal_id):
        self.created.append(signal_id)
        return signal_id + 100, f"review-{signal_id}"

    def consume_review(self, approval_id, token):
        self.consumed.append((approval_id, token))
        signal_id = approval_id - 100
        if signal_id == 1:
            return SimpleNamespace(
                execution_approval_id=201,
                execution_token="exec-1",
                symbol="QQQ",
                quantity=5,
                limit_price=Decimal("500"),
                estimated_fee=Decimal("2.50"),
            )
        return SimpleNamespace(
            execution_approval_id=202,
            execution_token="exec-2",
            symbol="TQQQ",
            quantity=3,
            limit_price=Decimal("100"),
            estimated_fee=Decimal("0.30"),
        )

    def cancel_approval(self, approval_id):
        self.canceled.append(approval_id)
        return True

    def execute(self, approval_id, token):
        self.executed.append((approval_id, token))
        symbol = "QQQ" if approval_id == 201 else "TQQQ"
        quantity = 5 if symbol == "QQQ" else 3
        return OrderReceipt(
            client_order_id=f"client-{symbol}",
            broker_order_id=f"broker-{symbol}",
            status="FILLED",
            quantity=quantity,
            filled_quantity=quantity,
        )


def _app(trading=None, repo=None):
    app = object.__new__(V322TelegramBotApp)
    app._buy_batch_lock = threading.RLock()
    app._active_buy_batch = None
    app.config = SimpleNamespace(
        enabled_symbols=("TQQQ", "SOXL"),
        global_=SimpleNamespace(execution_token_ttl_seconds=60),
    )
    app.settings = SimpleNamespace(trading_mode="dry_run")
    app.repository = repo or _Repo()
    app.trading_service = trading or _Trading()
    app.portfolio_service = _Portfolio()
    return app


def test_sell_order_is_hard_barrier_for_batch_buy_review():
    text = _format_batch_barrier(
        [
            {
                "symbol": "SOXL",
                "side": "SELL",
                "qty": 10,
                "filled_qty": 4,
                "status": "PARTIAL_FILLED",
            }
        ]
    )
    assert text is not None
    assert "매도 완료 대기" in text
    assert "SOXL" in text
    assert "4/10주" in text
    assert "새 매수는 자동으로 막혀" in text


def test_today_review_collects_all_buys_into_one_final_button():
    trading = _Trading()
    app = _app(trading=trading)

    text, markup = app._prepare_today_buy_batch()

    assert trading.created == [1, 2]
    assert "오늘 주문 한번에 검토" in text
    assert text.index("QQQ") < text.index("TQQQ")
    assert "$2,802.80" in text
    payload = markup.to_dict()
    buttons = [
        button
        for row in payload["inline_keyboard"]
        for button in row
    ]
    assert any("2건 전체 실행" in button["text"] for button in buttons)
    assert any(button["callback_data"].startswith("batch|exec|") for button in buttons)
    assert app._active_buy_batch is not None


def test_today_review_does_not_create_buys_while_sell_is_open():
    trading = _Trading()
    repo = _Repo(
        [
            {
                "symbol": "QQQ",
                "side": "SELL",
                "qty": 7,
                "filled_qty": 0,
                "status": "SUBMITTED",
            }
        ]
    )
    app = _app(trading=trading, repo=repo)

    text, markup = app._prepare_today_buy_batch()

    assert "매도 완료 대기" in text
    assert markup is None
    assert trading.created == []


def test_batch_execute_submits_every_reviewed_buy_once():
    trading = _Trading()
    app = _app(trading=trading)
    app._prepare_today_buy_batch()
    batch_id = app._active_buy_batch.batch_id

    text = app._execute_today_buy_batch(batch_id)

    assert trading.executed == [(201, "exec-1"), (202, "exec-2")]
    assert "QQQ" in text and "TQQQ" in text
    assert "모두 제출했습니다" in text
    assert app._active_buy_batch is None
    second = app._execute_today_buy_batch(batch_id)
    assert "이미 사용" in second


def test_batch_execute_stops_after_changed_quote_and_cancels_remaining():
    class _FailingTrading(_Trading):
        def execute(self, approval_id, token):
            self.executed.append((approval_id, token))
            if approval_id == 202:
                raise QuoteChangedError("가격 또는 수량이 바뀌었습니다")
            return OrderReceipt(
                client_order_id="client-QQQ",
                broker_order_id="broker-QQQ",
                status="FILLED",
                quantity=5,
                filled_quantity=5,
            )

    trading = _FailingTrading()
    app = _app(trading=trading)
    third = _BatchBuyItem(
        execution_approval_id=203,
        execution_token="exec-3",
        symbol="SOXL",
        quantity=2,
        limit_price=Decimal("30"),
        estimated_fee=Decimal("0.06"),
    )
    app._prepare_today_buy_batch()
    original = app._active_buy_batch
    app._active_buy_batch = _BatchBuyPlan(
        batch_id=original.batch_id,
        created_monotonic=time.monotonic(),
        items=(*original.items, third),
    )

    text = app._execute_today_buy_batch(original.batch_id)

    assert trading.executed == [(201, "exec-1"), (202, "exec-2")]
    assert 203 in trading.canceled
    assert "남은 매수는 자동 중단" in text
    assert "앞에서 이미 제출된 주문은 되돌리지 않습니다" in text
