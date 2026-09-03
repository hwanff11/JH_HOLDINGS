from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.managed_account import (
    current_v322_capital_state,
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
