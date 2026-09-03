from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    managed_cash_balance,
    marked_managed_equity,
    raw_managed_cash_balance,
    reserve_buy_order_with_managed_cash,
)
from jd_holdings.application.operational_safety import OperatorSafetyService
from jd_holdings.automation.live_service import ProductionJHAutoService
from jd_holdings.automation.service import (
    AUTO_LAUNCH_AUTHORIZED_KEY,
    AUTO_OPERATOR_HALT_LATCH_KEY,
    AUTO_QUARANTINE_KEY,
    AUTO_RAMP_STAGE_KEY,
    AUTO_STATE_KEY,
    AUTO_UNITS_KEY,
    TARGET_QTY_GENERATION_KEY,
)
from jd_holdings.core.models import OrderReceipt
from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp


class _CleanReconciliation:
    def run(self):
        return {}


class _Clock:
    def __init__(self, session: str = "regular") -> None:
        self.session = session

    def latest_completed_session(self, *args, **kwargs):
        return date(2026, 9, 2)

    def classify_session(self, now=None):
        return self.session

    def completed_sessions_since(self, start, now=None):
        return 3


class _FakeTradingService:
    def __init__(self) -> None:
        self.executed: list[int] = []

    def active_signals(self):
        return [
            {"signal_id": 2, "symbol": "TQQQ", "action": "CORE_REBALANCE_BUY"},
            {"signal_id": 1, "symbol": "QQQ", "action": "CORE_REBALANCE_BUY"},
        ]

    def create_review_approval(self, signal_id, *, now=None):
        return signal_id + 100, f"review-{signal_id}"

    def consume_review(self, approval_id, token, *, now=None):
        signal_id = approval_id - 100
        return SimpleNamespace(
            execution_approval_id=signal_id + 200,
            execution_token=f"execute-{signal_id}",
        )

    def execute(self, approval_id, token, *, now=None):
        signal_id = approval_id - 200
        self.executed.append(signal_id)
        return OrderReceipt(
            client_order_id=f"AUTO-{signal_id}",
            broker_order_id=f"BROKER-{signal_id}",
            status="FILLED",
            quantity=1,
            filled_quantity=1,
            average_fill_price=Decimal("100"),
        )


def _broker() -> DryRunBroker:
    return DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
        },
        buying_power=Decimal("1000000"),
    )


def _service(tmp_path, config, *, clock=None):
    repository = SQLiteRepository(tmp_path / "auto.db", config)
    broker = _broker()
    service = ProductionJHAutoService(
        config,
        repository,
        broker,
        _CleanReconciliation(),
        clock or _Clock(),
    )
    return repository, broker, service


def test_auto_bootstrap_is_fail_closed_and_order_free(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    settings = service.settings()

    assert settings.version == "1.0.0"
    assert settings.base_capital is None
    assert settings.ratio is None
    assert settings.target_principal == 0
    assert settings.effective_principal == 0
    assert not settings.launch_authorized
    assert settings.quarantine
    assert repository.open_orders() == []


def test_setting_capital_and_ratio_never_authorizes_or_orders(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)

    service.set_base_capital("50000")
    settings = service.set_ratio_percent("20")

    assert settings.target_principal == Decimal("10000.00")
    assert settings.effective_principal == 0
    assert not settings.launch_authorized
    assert repository.get_system_value(AUTO_LAUNCH_AUTHORIZED_KEY) == "0"
    assert repository.open_orders() == []


def test_first_launch_only_opens_stage_one_and_does_not_order(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    # Simulate the old V3.2.2 fixed-capital state that can exist before AUTO migration.
    repository.set_system_value("v322_high_water_equity", "50000")
    repository.set_system_value("v322_risk_budget", "50000")
    service.set_base_capital("50000")
    service.set_ratio_percent("20")

    before_orders = len(repository.open_orders())
    settings = service.authorize_launch()

    assert len(repository.open_orders()) == before_orders == 0
    assert settings.launch_authorized
    assert settings.target_principal == Decimal("10000.00")
    assert settings.effective_principal == Decimal("5000.00")
    assert settings.ramp_stage == 1
    assert settings.quarantine
    high_water, risk_budget = current_v322_capital_state(config, repository)
    assert high_water == Decimal("5000.00")
    assert risk_budget == Decimal("5000.00")


def test_final_cash_gate_rejects_buy_before_launch(tmp_path, config):
    repository, broker, _service_obj = _service(tmp_path, config)
    repository.set_system_value("operator_buy_halt", "0")

    with pytest.raises(RuntimeError, match="최초 시작승인"):
        reserve_buy_order_with_managed_cash(
            config,
            repository,
            broker,
            client_order_id="AUTO-BLOCK-BEFORE-LAUNCH",
            signal_id=None,
            cycle_id=None,
            symbol="QQQ",
            order_type="LIMIT",
            price=Decimal("500"),
            quantity=1,
            purpose="CORE_REBALANCE_BUY",
        )

    assert repository.get_order_by_client_id("AUTO-BLOCK-BEFORE-LAUNCH") is None


def test_added_capital_uses_new_money_only_50_percent_stage(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()

    # Finish the initial target to emulate a stable 20% AUTO allocation.
    service._apply_external_flow(
        Decimal("10000"),
        event_type="TEST_COMPLETE_INITIAL_RAMP",
        note="test",
    )
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")

    updated = service.set_ratio_percent("40")

    assert updated.target_principal == Decimal("20000.00")
    # Existing 10k + 50% of the newly delegated 10k.
    assert updated.effective_principal == Decimal("15000.00")
    assert updated.ramp_stage == 1


def test_risk_reduction_is_immediate_not_staged(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    service._apply_external_flow(
        Decimal("10000"),
        event_type="TEST_COMPLETE_INITIAL_RAMP",
        note="test",
    )
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
    service.set_ratio_percent("40")

    reduced = service.set_ratio_percent("10")

    assert reduced.target_principal == Decimal("5000.00")
    assert reduced.effective_principal == Decimal("5000.00")
    assert reduced.ramp_stage == 0


def test_unitized_return_is_not_changed_by_external_capital_flow(tmp_path, config, monkeypatch):
    repository, _broker_obj, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    service._apply_external_flow(
        Decimal("10000"),
        event_type="TEST_COMPLETE_INITIAL_RAMP",
        note="test",
    )
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")

    import jd_holdings.automation.service as service_module

    monkeypatch.setattr(
        service_module,
        "marked_managed_equity",
        lambda *args, **kwargs: Decimal("12000"),
    )
    before_units = Decimal(repository.get_system_value(AUTO_UNITS_KEY) or "0")
    assert before_units == Decimal("10000")

    service._apply_external_flow(
        Decimal("15000"),
        event_type="TEST_EXTERNAL_FLOW",
        note="test capital flow",
    )
    after_units = Decimal(repository.get_system_value(AUTO_UNITS_KEY) or "0")
    # New 5k is issued at NAV 1.2 instead of being mistaken for investment profit.
    assert after_units == pytest.approx(Decimal("14166.66666666666666666666667"))

    monkeypatch.setattr(
        service_module,
        "marked_managed_equity",
        lambda *args, **kwargs: Decimal("17000"),
    )
    monkeypatch.setattr(
        service_module,
        "raw_managed_cash_balance",
        lambda *args, **kwargs: Decimal("17000"),
    )
    perf = service.performance()
    assert perf.cumulative_return == pytest.approx(Decimal("0.20"))


def test_operator_halt_sets_durable_latch_and_resume_clears_after_reconciliation(tmp_path, config):
    repository, broker, _service_obj = _service(tmp_path, config)
    repository.set_system_value("operator_buy_halt", "0")
    safety = OperatorSafetyService(repository, broker, _CleanReconciliation())

    result = safety.halt()
    assert result.fully_settled
    assert repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "1"
    assert repository.get_system_value("operator_buy_halt") == "1"

    resumed = safety.resume()
    assert resumed["resumed"]
    assert repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "0"
    assert repository.get_system_value("operator_buy_halt") == "0"


def test_startup_quarantine_can_release_only_after_launch_and_clean_cycle(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    repository.set_system_value("operator_buy_halt", "1")

    assert not service.try_release_quarantine(safety_ready=True)
    assert repository.get_system_value("operator_buy_halt") == "1"

    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    assert service.try_release_quarantine(safety_ready=True)
    assert repository.get_system_value(AUTO_QUARANTINE_KEY) == "0"
    assert repository.get_system_value("operator_buy_halt") == "0"
    assert repository.get_system_value(AUTO_STATE_KEY) == "RUNNING"


def test_auto_executor_handles_at_most_one_signal_per_clean_cycle(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config, clock=_Clock("regular"))
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_OPERATOR_HALT_LATCH_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value("operator_buy_halt", "0")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")
    trading = _FakeTradingService()

    result = service.execute_one(
        trading,
        now=datetime(2026, 9, 3, 15, 0, tzinfo=UTC),
    )

    assert result is not None
    assert result.symbol == "QQQ"
    assert trading.executed == [1]
    assert len(service.recent_cycles()) == 1


def test_auto_executor_does_nothing_outside_regular_session(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config, clock=_Clock("after_hours"))
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value("operator_buy_halt", "0")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")
    trading = _FakeTradingService()

    assert service.execute_one(trading) is None
    assert trading.executed == []


def test_auto_final_buy_gate_fails_closed_when_halt_latch_is_missing(tmp_path, config):
    repository, broker, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    assert service.try_release_quarantine(safety_ready=True)
    with repository.transaction() as connection:
        connection.execute(
            "DELETE FROM system_state WHERE key = ?",
            (AUTO_OPERATOR_HALT_LATCH_KEY,),
        )

    with pytest.raises(RuntimeError, match="누락·손상"):
        reserve_buy_order_with_managed_cash(
            config,
            repository,
            broker,
            client_order_id="AUTO-MISSING-LATCH",
            signal_id=None,
            cycle_id=None,
            symbol="QQQ",
            order_type="LIMIT",
            price=Decimal("500"),
            quantity=1,
            purpose="CORE_REBALANCE_BUY",
        )


def test_capital_reduction_keeps_withdrawal_liability_in_marked_equity(tmp_path, config):
    repository, broker, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    service.authorize_launch()
    service._apply_external_flow(
        Decimal("10000"),
        event_type="TEST_COMPLETE_INITIAL_RAMP",
        note="test",
    )
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "0")
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("1"),
        signal_trade_date=date(2026, 9, 2),
        target_qty=20,
    )
    assert repository.reserve_order(
        client_order_id="AUTO-CAPITAL-REDUCTION-BUY",
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("500"),
        quantity=20,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        "AUTO-CAPITAL-REDUCTION-BUY",
        status="FILLED",
        broker_order_id="DRY-AUTO-CAPITAL-REDUCTION-BUY",
        filled_qty=20,
        average_fill_price=Decimal("500"),
    )
    repository.apply_core_fill("AUTO-CAPITAL-REDUCTION-BUY")
    # The $10 buy fee is a real strategy loss and must stay in marked equity.
    assert marked_managed_equity(config, repository, broker) == Decimal("9990.000")

    reduced = service.set_ratio_percent("10")

    assert reduced.effective_principal == Decimal("5000.00")
    assert raw_managed_cash_balance(config, repository) == Decimal("-5010.000")
    assert managed_cash_balance(config, repository) == Decimal("0")
    assert marked_managed_equity(config, repository, broker) == Decimal("4990.000")


class _UnknownTradingService(_FakeTradingService):
    def execute(self, approval_id, token, *, now=None):
        signal_id = approval_id - 200
        self.executed.append(signal_id)
        return OrderReceipt(
            client_order_id=f"AUTO-{signal_id}",
            broker_order_id=f"BROKER-{signal_id}",
            status="UNKNOWN",
            quantity=1,
            filled_quantity=0,
            average_fill_price=None,
        )


def test_auto_unknown_receipt_immediately_enters_quarantine(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config, clock=_Clock("regular"))
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_OPERATOR_HALT_LATCH_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value("operator_buy_halt", "0")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")

    result = service.execute_one(
        _UnknownTradingService(),
        now=datetime(2026, 9, 3, 15, 0, tzinfo=UTC),
    )

    assert result is not None and result.status == "UNKNOWN"
    assert repository.get_system_value(AUTO_QUARANTINE_KEY) == "1"
    assert repository.get_system_value(AUTO_STATE_KEY) == "AUTO_QUARANTINE"
    assert repository.get_system_value("operator_buy_halt") == "1"
    assert repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "0"


def test_auto_telegram_views_and_launch_confirmation_are_order_free(tmp_path, config):
    repository, _broker_obj, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")

    rows = []
    for symbol, price in (("QQQ", "500"), ("TQQQ", "100"), ("SOXL", "50")):
        rows.append(
            {
                "symbol": symbol,
                "core_quantity": 0,
                "core_market_value": Decimal("0"),
                "price": Decimal(price),
                "cost_basis": Decimal("0"),
                "unrealized_profit": Decimal("0"),
                "target_weight": Decimal("0"),
            }
        )
    snapshot = {
        "equity": Decimal("0"),
        "cash": Decimal("0"),
        "invested_market_value": Decimal("0"),
        "high_water": Decimal("0"),
        "risk_budget": Decimal("0"),
        "rows": rows,
    }
    app = object.__new__(JHAutoTelegramBotApp)
    app.auto_service = service
    app.repository = repository
    app.portfolio_service = SimpleNamespace(snapshot=lambda: snapshot)
    app.market_clock = _Clock("regular")
    app.reconciliation_service = _CleanReconciliation()
    app._auto_pending = {}
    app._portfolio_safe_mode = lambda: False

    dashboard = app._format_auto_dashboard()
    portfolio = app._format_portfolio_message()
    today, markup = app._prepare_today_buy_batch()
    control, control_markup = app._format_auto_control()
    start_review, start_markup = app._review_change("start", "1")

    assert "JDSS 3.2.2" in dashboard and "JH AUTO 1.0.0" in dashboard
    assert "누적 운용수익률" in dashboard
    assert "QQQ" in portfolio and "보유수익률" in portfolio
    assert "최초 시작승인 전" in today and markup is None
    assert "운용 기준자금" in control and control_markup.keyboard
    assert "이 확인 버튼 자체는 주문을 보내지 않습니다" in start_review
    assert start_markup.keyboard
    assert repository.open_orders() == []

    token = next(reversed(app._auto_pending))
    result = app._confirm_pending(token)

    assert "이 버튼에서는 0건" in result
    assert repository.open_orders() == []
    assert service.settings().launch_authorized
    assert service.settings().quarantine

    app.notify_portfolio_buy_batch_ready((101, 102))
    events = repository.recent_events(limit=5)
    assert any(event["event_type"] == "JH_AUTO_SIGNAL_READY" for event in events)
