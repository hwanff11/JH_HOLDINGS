from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.jh_auto_runtime_services import JHAutoLiveAllocationTradingService
from jd_holdings.automation.final_ops_hardening import (
    AUTO_RAMP_STAGE_AUTO_FILL_KEY,
    FinalOpsProductionJHAutoService,
)
from jd_holdings.automation.service import (
    AUTO_RAMP_STAGE_KEY,
    TARGET_QTY_GENERATION_KEY,
)
from jd_holdings.infrastructure.final_ops_runtime import (
    FinalOpsLiveInitialOnboardingPortfolioService,
    FinalOpsOrderMonitor,
    LiveRuntimeLock,
)


class _CleanReconciliation:
    def run(self):
        return {}


class _Clock:
    def latest_completed_session(self, *args, **kwargs):
        return date(2026, 9, 2)

    def classify_session(self, now=None):
        return "regular"

    def completed_sessions_since(self, start, now=None):
        return 3


def _service(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "final-ops.db", config)
    broker = DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
        },
        buying_power=Decimal("1000000"),
    )
    service = FinalOpsProductionJHAutoService(
        config,
        repository,
        broker,
        _CleanReconciliation(),
        _Clock(),
    )
    return repository, broker, service


def test_live_runtime_lock_rejects_second_process_on_same_ledger(tmp_path):
    database_path = tmp_path / "live.db"
    first = LiveRuntimeLock(database_path)
    second = LiveRuntimeLock(database_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_live_allocation_returns_without_writes_outside_regular_session():
    service = object.__new__(FinalOpsLiveInitialOnboardingPortfolioService)
    service.market_clock = SimpleNamespace(classify_session=lambda _now: "pre_market")

    assert service.run_allocation(datetime(2026, 9, 3, 12, 0, tzinfo=UTC)) is None


def test_every_ramp_stage_requires_auto_fill_proof(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "2")
    repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "0")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-02")
    for symbol in ("QQQ", "TQQQ", "SOXL"):
        repository.set_core_target(
            symbol,
            active=False,
            target_weight=Decimal("0"),
            signal_trade_date=date(2026, 9, 2),
            target_qty=0,
        )

    assert not service._stage_filled()
    repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "1")
    assert service._stage_filled()


def test_order_monitor_updates_auto_cycle_when_fill_arrives_later(tmp_path, config):
    repository, _broker, _service_obj = _service(tmp_path, config)
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "1")
    with repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO jh_auto_cycles(
                cycle_id, signal_id, symbol, status, requested_qty,
                filled_qty, broker_order_id, started_at
            ) VALUES ('cycle-1', 1, 'QQQ', 'SUBMITTED', 2, 0, 'broker-1', ?)
            """,
            (datetime(2026, 9, 3, 13, 31, tzinfo=UTC).isoformat(),),
        )

    monitor = object.__new__(FinalOpsOrderMonitor)
    monitor.repository = repository
    receipt = SimpleNamespace(
        status="FILLED",
        quantity=2,
        filled_quantity=2,
        average_fill_price=Decimal("501.25"),
        broker_order_id="broker-1",
    )
    monitor._sync_auto_cycle({"cycle_id": "cycle-1"}, receipt)

    with repository.transaction() as connection:
        row = connection.execute(
            """
            SELECT status, filled_qty, completed_at
            FROM jh_auto_cycles
            WHERE cycle_id = 'cycle-1'
            """
        ).fetchone()
    assert row["status"] == "FILLED"
    assert int(row["filled_qty"]) == 2
    assert row["completed_at"]
    assert repository.get_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY) == "1"


def test_live_auto_order_submission_carries_current_cycle_id():
    source = inspect.getsource(JHAutoLiveAllocationTradingService._execute_core_buy)
    assert "_jh_auto_cycle_id" in source
    assert "cycle_id=str(cycle_id) if cycle_id else None" in source


def test_live_entrypoint_uses_final_ops_guards():
    from jd_holdings import live_bot

    source = inspect.getsource(live_bot)
    assert "FinalOpsProductionJHAutoService" in source
    assert "FinalOpsOrderMonitor" in source
    assert "FinalOpsLiveInitialOnboardingPortfolioService" in source
    assert "LiveRuntimeLock" in source
    assert "LiveJHAutoTelegramBotApp" in source
