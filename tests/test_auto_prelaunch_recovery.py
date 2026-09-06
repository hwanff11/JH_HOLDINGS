from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_jh_auto_final_ops_hardening import _service

from jd_holdings.application.database import SQLiteRepository
from jd_holdings.automation.service import (
    AUTO_EFFECTIVE_PRINCIPAL_KEY,
    AUTO_LAUNCH_AUTHORIZED_AT_KEY,
    AUTO_LAUNCH_AUTHORIZED_KEY,
    AUTO_UNITS_KEY,
)
from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp
from jd_holdings.infrastructure.live_runtime_hardening import HardenedOperationalSafetyTelegramBotApp
from jd_holdings.infrastructure.telegram_bot import TelegramBotApp


def configured(tmp_path, config):
    repo, broker, service = _service(tmp_path, config)
    service.set_base_capital("50000")
    service.set_ratio_percent("20")
    return repo, broker, service


def app_for(repo, service):
    app = object.__new__(JHAutoTelegramBotApp)
    app.repository = repo
    app.settings = SimpleNamespace(trading_mode="live")
    app.auto_service = service
    app.reconciliation_service = SimpleNamespace(run=lambda: {})
    app._auto_pending = {}
    app._portfolio_safe_mode = lambda: False
    return app


@pytest.mark.parametrize("key", [AUTO_LAUNCH_AUTHORIZED_AT_KEY, AUTO_UNITS_KEY])
def test_interrupted_launch_rolls_back_and_can_be_retried(tmp_path, config, monkeypatch, key):
    repo, broker, service = configured(tmp_path, config)
    before = service.settings()
    original = repo.set_system_value

    def broken_write(name, value):
        if name == key:
            raise OSError("injected storage failure")
        original(name, value)

    with monkeypatch.context() as patch:
        patch.setattr(repo, "set_system_value", broken_write)
        with pytest.raises(OSError):
            service.authorize_launch()
    assert service.settings() == before
    assert repo.get_system_value(AUTO_LAUNCH_AUTHORIZED_KEY) == "0"
    assert repo.get_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY) == "0"
    assert repo.get_system_value(AUTO_UNITS_KEY) == "0"
    with repo.transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM jh_auto_capital_events WHERE event_type='LAUNCH_STAGE_1'"
        ).fetchone()[0] == 0
    service.arm_startup_quarantine()
    assert service.authorize_launch().effective_principal == Decimal("5000")
    assert broker.orders == {}


def test_capital_failure_preserves_old_principal_and_units(tmp_path, config, monkeypatch):
    repo, _broker, service = configured(tmp_path, config)
    service.authorize_launch()
    before = service.settings()
    units = repo.get_system_value(AUTO_UNITS_KEY)
    original = repo.set_system_value

    def broken_write(key, value):
        if key == AUTO_UNITS_KEY:
            raise OSError("injected flow failure")
        original(key, value)

    monkeypatch.setattr(repo, "set_system_value", broken_write)
    with pytest.raises(OSError):
        service.set_ratio_percent("5")
    assert service.settings() == before
    assert repo.get_system_value(AUTO_UNITS_KEY) == units


@pytest.mark.parametrize("kind,value", [("start", "1"), ("capital", "100000"), ("ratio", "50")])
def test_old_confirmation_rejected_after_settings_change(tmp_path, config, kind, value):
    repo, broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    app._review_change(kind, value)
    token = next(reversed(app._auto_pending))
    service.set_ratio_percent("100")
    with pytest.raises(RuntimeError, match="검토 후"):
        app._confirm_pending(token)
    assert not service.settings().launch_authorized
    assert service.settings().target_principal == Decimal("50000")
    assert broker.orders == {}
    with pytest.raises(RuntimeError, match="만료"):
        app._confirm_pending(token)


def test_confirmation_detects_settings_changed_and_changed_back(tmp_path, config):
    repo, _broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    app._review_change("start", "1")
    token = next(reversed(app._auto_pending))
    service.set_ratio_percent("100")
    service.set_ratio_percent("20")
    with pytest.raises(RuntimeError, match="검토 후"):
        app._confirm_pending(token)


def test_same_confirmation_is_consumed_once_across_threads(tmp_path, config):
    repo, broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    app._review_change("start", "1")
    token = next(reversed(app._auto_pending))

    def confirm():
        try:
            app._confirm_pending(token)
            return True
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))
    assert sorted(results) == [False, True]
    assert service.settings().effective_principal == Decimal("5000")
    assert broker.orders == {}


def test_start_review_uses_same_cent_rounding_as_authorized_principal(tmp_path, config):
    repo, _broker, service = configured(tmp_path, config)
    service.set_base_capital("103")
    service.set_ratio_percent("1")
    app = app_for(repo, service)
    review, _markup = app._review_change("start", "1")
    assert "첫 단계 허용원금(50%) <code>$0.51</code>" in review
    app._confirm_pending(next(reversed(app._auto_pending)))
    assert service.settings().effective_principal == Decimal("0.51")


@pytest.mark.parametrize(
    "key", ["jh_auto_operator_halt_latched", "operator_buy_halt", "jh_auto_startup_quarantine"]
)
def test_ramp_cannot_advance_while_stopped(tmp_path, config, monkeypatch, key):
    repo, _broker, service = configured(tmp_path, config)
    service.authorize_launch()
    service.try_release_quarantine(safety_ready=True)
    monkeypatch.setattr(service, "_stage_filled", lambda: True)
    repo.set_system_value(key, "1")
    assert not service.advance_ramp_if_ready()
    assert service.settings().effective_principal == Decimal("5000")
    assert service.settings().ramp_stage == 1


def test_nested_transaction_reads_own_writes_and_rolls_back(tmp_path, config):
    repo = SQLiteRepository(tmp_path / "nested.db", config)
    with pytest.raises(OSError):
        with repo.transaction():
            repo.set_system_value("atomic_probe", "new")
            assert repo.get_system_value("atomic_probe") == "new"
            with sqlite3.connect(repo.db_path) as observer:
                assert observer.execute(
                    "SELECT value FROM system_state WHERE key='atomic_probe'"
                ).fetchone() is None
            raise OSError("injected transaction failure")
    assert repo.get_system_value("atomic_probe") is None


def test_delivery_failure_does_not_kill_scheduler_or_repeat_order(tmp_path, config, monkeypatch):
    repo, _broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    app.config = config
    app.trading_service = object()
    app._last_monitor = 0
    app._stop = threading.Event()
    calls = []
    result = SimpleNamespace(
        symbol="QQQ", quantity=1, filled_quantity=0,
        status="SUBMITTED", average_fill_price=None,
    )
    monkeypatch.setattr(HardenedOperationalSafetyTelegramBotApp, "_run_order_safety_cycle", lambda _: True)
    monkeypatch.setattr(service, "execute_one", lambda _: calls.append("order") or result)

    def fail_delivery(*args, **kwargs):
        raise ConnectionError("injected Telegram error")

    monkeypatch.setattr(TelegramBotApp, "_send", fail_delivery)

    def controlled_scheduler(self):
        assert repo.get_system_value("jh_auto_scheduler_heartbeat")
        assert repo.get_system_value("jh_auto_watchdog_version") == "1"
        self._record_scheduler_heartbeat()
        assert self._run_order_safety_cycle() is False
        self._stop.set()

    monkeypatch.setattr(HardenedOperationalSafetyTelegramBotApp, "_scheduler_loop", controlled_scheduler)
    app._scheduler_loop()
    assert calls == ["order"]
    assert repo.get_system_value("jh_auto_notification_error") == "ConnectionError"
    assert repo.get_system_value("jh_auto_scheduler_heartbeat")
    assert repo.get_system_value("jh_auto_last_safety_success_at")


def test_callback_delivery_failure_is_not_silently_hidden(tmp_path, config, monkeypatch):
    repo, _broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    monkeypatch.setattr(TelegramBotApp, "_send", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    with pytest.raises(ConnectionError):
        app._send("confirmation response")


def test_unexpected_scheduler_error_quarantines_before_next_iteration(tmp_path, config, monkeypatch):
    repo, broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    app._stop = threading.Event()
    app.config = SimpleNamespace(scheduler=SimpleNamespace(poll_interval_seconds=0))
    calls = []

    def scheduler(self):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("injected scheduler boundary failure")
        assert repo.get_system_value("operator_buy_halt") == "1"
        assert service.settings().quarantine
        self._stop.set()

    monkeypatch.setattr(HardenedOperationalSafetyTelegramBotApp, "_scheduler_loop", scheduler)
    app._scheduler_loop()
    assert len(calls) == 2
    assert broker.orders == {}


def test_recent_order_contains_time_price_amount_and_korean_status(tmp_path, config):
    repo, _broker, service = configured(tmp_path, config)
    app = app_for(repo, service)
    with repo.transaction() as connection:
        connection.execute(
            "INSERT INTO jh_auto_cycles(cycle_id,symbol,status,requested_qty,filled_qty,"
            "average_fill_price,started_at) VALUES ('test','QQQ','PARTIAL_FILLED',3,1,'500',?)",
            (datetime.now(UTC).isoformat(),),
        )
    message = app._format_recent_cycles()
    assert "부분체결" in message and "미체결 2주" in message
    assert "체결단가" in message and "체결금액" in message and "KST" in message
