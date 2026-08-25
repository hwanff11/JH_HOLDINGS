from __future__ import annotations

import inspect
import threading
import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import jd_holdings.infrastructure.telegram_bot_v322 as batch_module
from jd_holdings.application.trading_service import QuoteChangedError
from jd_holdings.core.models import OrderReceipt
from jd_holdings.infrastructure.initial_onboarding_telegram import (
    InitialOnboardingTelegramBotApp,
)
from jd_holdings.infrastructure.telegram_bot_runtime import RuntimeTelegramBotApp
from jd_holdings.infrastructure.telegram_bot_v322 import (
    V322TelegramBotApp,
    _BatchBuyItem,
    _BatchBuyPlan,
    _format_batch_barrier,
)


class _Repo:
    def __init__(
        self,
        *,
        orders=None,
        marker="2026-08-24",
        generation="2026-08-01",
        target_generation="2026-08-01",
        targets=None,
    ):
        self.orders = list(orders or [])
        self.events = []
        self.system = {
            "v322_portfolio_safe_mode": "0",
            "last_v322_allocation_trade_date": marker,
            "v322_target_qty_generation": target_generation,
        }
        default_targets = {
            "QQQ": (5, 0),
            "TQQQ": (3, 0),
            "SOXL": (0, 0),
        }
        chosen = targets or default_targets
        self.cores = [
            {
                "symbol": symbol,
                "signal_trade_date": generation,
                "target_qty": target,
                "qty": qty,
            }
            for symbol, (target, qty) in chosen.items()
        ]

    def open_orders(self):
        return list(self.orders)

    def get_system_value(self, key):
        return self.system.get(key)

    def get_position(self, symbol):
        return SimpleNamespace(state="EMPTY")

    def core_positions(self):
        return [dict(item) for item in self.cores]

    def log_event(self, severity, event_type, message, *, context=None, **kwargs):
        self.events.append(
            {
                "severity": severity,
                "event_type": event_type,
                "message": message,
                "context": context or {},
            }
        )

    def get_signal(self, signal_id):
        symbols = {1: "QQQ", 2: "TQQQ", 3: "SOXL"}
        return {"signal_id": signal_id, "symbol": symbols[signal_id]}


class _Portfolio:
    def __init__(self, cash=Decimal("50000")):
        self.cash = cash

    def snapshot(self):
        return {"cash": self.cash}


class _MarketClock:
    def __init__(self, *, completed=date(2026, 8, 24), next_started=True):
        self.completed = completed
        self.next_started = next_started

    def latest_completed_session(self, *args, **kwargs):
        return self.completed

    def next_session_has_started(self, generation_date, current):
        return self.next_started


class _Reconciliation:
    def __init__(self, mismatches=None, *, error=None):
        self.mismatches = mismatches or {}
        self.error = error
        self.calls = 0

    def run(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.mismatches)


class _Trading:
    def __init__(self):
        self.created = []
        self.consumed = []
        self.canceled = []
        self.executed = []
        self.broker = object()
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


def _app(
    *,
    trading=None,
    repo=None,
    market_clock=None,
    reconciliation=None,
    cash=Decimal("50000"),
):
    app = object.__new__(V322TelegramBotApp)
    app._buy_batch_lock = threading.RLock()
    app._active_buy_batch = None
    app.config = SimpleNamespace(
        enabled_symbols=("TQQQ", "SOXL"),
        global_=SimpleNamespace(execution_token_ttl_seconds=60),
        scheduler=SimpleNamespace(signal_delay_minutes=0),
        idle_cash=SimpleNamespace(enabled=False, cash_buffer=Decimal("0")),
    )
    app.settings = SimpleNamespace(trading_mode="dry_run")
    app.repository = repo or _Repo()
    app.trading_service = trading or _Trading()
    app.portfolio_service = _Portfolio(cash)
    app.market_clock = market_clock or _MarketClock()
    app.reconciliation_service = reconciliation or _Reconciliation()
    return app


def _capacity(monkeypatch, value="50000"):
    monkeypatch.setattr(
        batch_module,
        "available_managed_cash",
        lambda config, repository, broker: Decimal(value),
    )


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


def test_stale_daily_calculation_is_not_reported_as_no_order(monkeypatch):
    _capacity(monkeypatch)
    repo = _Repo(marker="2026-08-21")
    trading = _Trading()
    trading.signals = []
    app = _app(repo=repo, trading=trading)

    text, markup = app._prepare_today_buy_batch()

    assert "전략 계산 확인 필요" in text
    assert "주문 없음으로 오인" in text
    assert markup is None
    assert trading.created == []


def test_changed_target_waits_for_next_us_session(monkeypatch):
    _capacity(monkeypatch)
    repo = _Repo(
        marker="2026-08-24",
        generation="2026-08-24",
        target_generation="",
    )
    app = _app(
        repo=repo,
        market_clock=_MarketClock(next_started=False),
    )

    text, markup = app._prepare_today_buy_batch()

    assert "다음 미국 거래일 대기" in text
    assert "주문 준비 대기" in text
    assert markup is None


def test_no_signal_with_unfilled_target_is_not_reported_as_no_order(monkeypatch):
    _capacity(monkeypatch)
    trading = _Trading()
    trading.signals = []
    app = _app(trading=trading)

    text, markup = app._prepare_today_buy_batch()

    assert "매수신호 생성 대기" in text
    assert "QQQ" in text
    assert markup is None


def test_today_review_collects_all_buys_into_one_sequential_button(monkeypatch):
    _capacity(monkeypatch)
    trading = _Trading()
    app = _app(trading=trading)

    text, markup = app._prepare_today_buy_batch()

    assert trading.created == [1, 2]
    assert "오늘 주문 한번에 검토" in text
    assert text.index("QQQ") < text.index("TQQQ")
    assert "$2,802.80" in text
    assert "HWM75 매수가능한도" in text
    assert "개별 주문으로 순서대로 제출" in text
    payload = markup.to_dict()
    buttons = [
        button
        for row in payload["inline_keyboard"]
        for button in row
    ]
    assert any("2건 순차 실행" in button["text"] for button in buttons)
    assert any(
        button["callback_data"].startswith("batch|exec|")
        for button in buttons
    )
    assert app._active_buy_batch is not None


def test_aggregate_hwm_capacity_blocks_batch_before_final_button(monkeypatch):
    _capacity(monkeypatch, "2000")
    trading = _Trading()
    app = _app(trading=trading)

    text, markup = app._prepare_today_buy_batch()

    assert "전체 매수한도 부족" in text
    assert "$2,802.80" in text
    assert "$2,000.00" in text
    assert markup is None
    assert 201 in trading.canceled
    assert 202 in trading.canceled
    assert app._active_buy_batch is None


def test_today_review_does_not_create_buys_while_sell_is_open(monkeypatch):
    _capacity(monkeypatch)
    trading = _Trading()
    repo = _Repo(
        orders=[
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


def test_concurrent_review_clicks_create_only_one_batch(monkeypatch):
    _capacity(monkeypatch)
    trading = _Trading()
    app = _app(trading=trading)
    results = []

    def worker():
        results.append(app._prepare_today_buy_batch()[0])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert trading.created == [1, 2]
    assert sum("오늘 주문 한번에 검토" in text for text in results) == 1
    assert sum("이미 검토 중" in text for text in results) == 1


def test_review_failure_cleans_created_execution_approvals(monkeypatch):
    _capacity(monkeypatch)

    class _FailingReview(_Trading):
        def consume_review(self, approval_id, token):
            if approval_id == 102:
                raise RuntimeError("quote lookup failed")
            return super().consume_review(approval_id, token)

    trading = _FailingReview()
    app = _app(trading=trading)

    try:
        app._prepare_today_buy_batch()
    except RuntimeError as exc:
        assert "quote lookup failed" in str(exc)
    else:
        raise AssertionError("review failure was expected")

    assert 201 in trading.canceled
    assert app._active_buy_batch is None
    assert any(
        event["event_type"] == "BATCH_BUY_REVIEW_FAILED"
        for event in app.repository.events
    )


def test_final_execute_runs_immediate_reconciliation_and_blocks_mismatch(monkeypatch):
    _capacity(monkeypatch)
    reconciliation = _Reconciliation()
    trading = _Trading()
    app = _app(trading=trading, reconciliation=reconciliation)
    app._prepare_today_buy_batch()
    batch_id = app._active_buy_batch.batch_id
    reconciliation.mismatches = {"QQQ": ["BROKER_DB_QTY_MISMATCH"]}

    text = app._execute_today_buy_batch(batch_id)

    assert "계좌/원장 불일치" in text
    assert trading.executed == []
    assert 201 in trading.canceled
    assert 202 in trading.canceled
    assert reconciliation.calls == 2


def test_batch_execute_submits_every_reviewed_buy_once_and_logs_audit(monkeypatch):
    _capacity(monkeypatch)
    trading = _Trading()
    app = _app(trading=trading)
    app._prepare_today_buy_batch()
    batch_id = app._active_buy_batch.batch_id

    text = app._execute_today_buy_batch(batch_id)

    assert trading.executed == [(201, "exec-1"), (202, "exec-2")]
    assert "QQQ" in text and "TQQQ" in text
    assert "순서대로 제출했습니다" in text
    assert app._active_buy_batch is None
    event_types = [event["event_type"] for event in app.repository.events]
    assert "BATCH_BUY_EXECUTION_STARTED" in event_types
    assert "BATCH_BUY_EXECUTION_FINISHED" in event_types

    second = app._execute_today_buy_batch(batch_id)
    assert "서버 재시작" in second


def test_batch_execute_stops_after_changed_quote_and_cancels_remaining(monkeypatch):
    _capacity(monkeypatch)

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
        trade_date=original.trade_date,
        items=(*original.items, third),
    )

    text = app._execute_today_buy_batch(original.batch_id)

    assert trading.executed == [(201, "exec-1"), (202, "exec-2")]
    assert 203 in trading.canceled
    assert "부분 실행 결과" in text
    assert "남은 매수는 자동 중단" in text
    assert "앞에서 이미 제출된 주문은 되돌리지 않습니다" in text


def test_batch_ready_notification_is_single_operator_card():
    app = _app()
    sent = []

    def capture(text, *, markup=None, chat_id=None):
        sent.append((text, markup))

    app._send = capture
    app.notify_portfolio_buy_batch_ready((1, 2))

    assert len(sent) == 1
    text, markup = sent[0]
    assert "오늘 매수 검토 가능" in text
    assert "QQQ, TQQQ" in text
    labels = [
        button["text"]
        for row in markup.to_dict()["inline_keyboard"]
        for button in row
    ]
    assert labels == ["🧾 오늘 주문 한번에 검토"]


def test_runtime_and_onboarding_use_batch_notification_not_signal_card_loop():
    runtime_source = inspect.getsource(RuntimeTelegramBotApp._scheduler_loop)
    onboarding_source = inspect.getsource(
        InitialOnboardingTelegramBotApp._register_handlers
    )

    assert "notify_portfolio_buy_batch_ready(portfolio_run.signals)" in runtime_source
    assert "for signal_id in portfolio_run.signals" not in runtime_source
    assert "notify_portfolio_buy_batch_ready(result.signals)" in onboarding_source
