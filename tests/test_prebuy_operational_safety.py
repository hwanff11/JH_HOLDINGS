from __future__ import annotations

import threading
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.operational_safety import OPERATOR_BUY_HALT_KEY
from jd_holdings.automation.final_ops_hardening import (
    AUTO_RAMP_STAGE_AUTO_FILL_KEY,
    FinalOpsProductionJHAutoService,
)
from jd_holdings.automation.service import (
    AUTO_ACCOUNTING_STARTED_AT_KEY,
    AUTO_EFFECTIVE_PRINCIPAL_KEY,
    AUTO_LAUNCH_AUTHORIZED_KEY,
    AUTO_OPERATOR_HALT_LATCH_KEY,
    AUTO_QUARANTINE_KEY,
    AUTO_RAMP_BASE_KEY,
    AUTO_RAMP_STAGE_FILLED_KEY,
    AUTO_RAMP_STAGE_KEY,
    AUTO_RAMP_STAGE_STARTED_KEY,
    AUTO_RAMP_TARGET_KEY,
    AUTO_STATE_KEY,
    TARGET_QTY_GENERATION_KEY,
)
from jd_holdings.infrastructure.jh_auto_live_display import LiveJHAutoTelegramBotApp
from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp

SEOUL_TZ = ZoneInfo("Asia/Seoul")


class _CleanReconciliation:
    def run(self):
        return {}


class _Clock:
    def latest_completed_session(self, *args, **kwargs):
        return date(2026, 9, 4)

    def classify_session(self, now=None):
        return "regular"

    def completed_sessions_since(self, start, now=None):
        return 3


def _service(tmp_path, config, service_cls=FinalOpsProductionJHAutoService):
    repository = SQLiteRepository(tmp_path / "prebuy-safety.db", config)
    broker = DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
        },
        buying_power=Decimal("1000000"),
    )
    service = service_cls(
        config,
        repository,
        broker,
        _CleanReconciliation(),
        _Clock(),
    )
    return repository, broker, service


def _configure(service):
    service.set_base_capital("50000")
    service.set_ratio_percent("20")


def test_first_launch_is_one_transaction_and_rolls_back_on_failure(tmp_path, config):
    class _FailBeforeCommit(FinalOpsProductionJHAutoService):
        def _before_launch_commit(self, connection):
            del connection
            raise RuntimeError("injected launch write failure")

    repository, _broker, service = _service(tmp_path, config, _FailBeforeCommit)
    _configure(service)

    with pytest.raises(RuntimeError, match="injected launch write failure"):
        service.authorize_launch()

    settings = service.settings()
    assert not settings.launch_authorized
    assert settings.effective_principal == Decimal("0")
    assert repository.get_system_value(AUTO_ACCOUNTING_STARTED_AT_KEY) in (None, "")
    assert repository.get_system_value(AUTO_RAMP_STAGE_KEY) == "0"


def test_first_launch_commits_authorization_and_stage_one_together(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _configure(service)

    settings = service.authorize_launch()

    assert settings.launch_authorized
    assert settings.target_principal == Decimal("10000.00")
    assert settings.effective_principal == Decimal("5000.00")
    assert settings.ramp_stage == 1
    assert settings.quarantine
    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
    assert repository.get_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY) == "0"


def test_bootstrap_repairs_legacy_half_launch_fail_closed(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _configure(service)
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY, "0")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "1")
    repository.set_system_value(AUTO_STATE_KEY, "STARTUP_QUARANTINE")

    repaired = FinalOpsProductionJHAutoService(
        config,
        repository,
        service.broker,
        _CleanReconciliation(),
        _Clock(),
    )
    settings = repaired.settings()
    assert not settings.launch_authorized
    assert settings.effective_principal == Decimal("0")
    assert settings.quarantine
    assert settings.state == "READY"
    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"
    assert repository.get_system_value(TARGET_QTY_GENERATION_KEY) == ""


def test_operator_halt_freezes_capital_ramp(tmp_path, config):
    repository, _broker, service = _service(tmp_path, config)
    _configure(service)
    repository.set_system_value(AUTO_LAUNCH_AUTHORIZED_KEY, "1")
    repository.set_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY, "5000")
    repository.set_system_value(AUTO_QUARANTINE_KEY, "0")
    repository.set_system_value(AUTO_STATE_KEY, "RUNNING")
    repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
    repository.set_system_value(AUTO_OPERATOR_HALT_LATCH_KEY, "1")
    repository.set_system_value(AUTO_RAMP_STAGE_KEY, "1")
    repository.set_system_value(AUTO_RAMP_BASE_KEY, "0")
    repository.set_system_value(AUTO_RAMP_TARGET_KEY, "10000")
    repository.set_system_value(AUTO_RAMP_STAGE_STARTED_KEY, "2026-09-01")
    repository.set_system_value(AUTO_RAMP_STAGE_FILLED_KEY, "2026-09-01")
    repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "1")
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, "2026-09-04")
    for symbol in ("QQQ", "TQQQ", "SOXL"):
        repository.set_core_target(
            symbol,
            active=False,
            target_weight=Decimal("0"),
            signal_trade_date=date(2026, 9, 4),
            target_qty=0,
        )

    assert not service.advance_ramp_if_ready()
    assert service.settings().ramp_stage == 1
    assert service.settings().effective_principal == Decimal("5000")


def _fake_settings(*, ratio="0.20"):
    return SimpleNamespace(
        base_capital=Decimal("50000"),
        ratio=Decimal(ratio),
        target_principal=Decimal("50000") * Decimal(ratio),
        effective_principal=Decimal("0"),
        launch_authorized=False,
        ramp_stage=0,
        ramp_target_principal=Decimal("0"),
    )


def test_stale_start_confirmation_is_rejected_after_capital_state_changes():
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app._auto_pending = {}
    app._auto_pending_fingerprints = {}
    app._auto_confirm_lock = threading.RLock()
    state = {"settings": _fake_settings(ratio="0.20")}
    app.auto_service = SimpleNamespace(settings=lambda: state["settings"])

    token = app._new_pending("start", "1")
    state["settings"] = _fake_settings(ratio="1.00")

    with pytest.raises(RuntimeError, match="검토 후 자금설정"):
        app._confirm_pending(token)
    assert token not in app._auto_pending


def test_scheduler_telegram_failure_is_nonfatal(monkeypatch):
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app._scheduler_thread = threading.current_thread()
    events = []
    app.repository = SimpleNamespace(
        log_event=lambda *args, **kwargs: events.append((args, kwargs))
    )
    app._normalize_inherited_auto_text = lambda text: text
    app._daily_brief_display_window_open = lambda: True

    def _raise_send(*args, **kwargs):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(JHAutoTelegramBotApp, "_send", _raise_send)

    app._send("order already submitted")
    assert events
    assert events[0][0][1] == "JH_AUTO_SCHEDULER_TELEGRAM_SEND_FAILED"


def test_late_daily_brief_is_suppressed_without_suppressing_background_analysis(monkeypatch):
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app._scheduler_thread = None
    app._daily_brief_display_window_open = lambda: False
    sent = []
    monkeypatch.setattr(
        JHAutoTelegramBotApp,
        "_send",
        lambda self, text, **kwargs: sent.append(text),
    )

    app._send("☀️ [JDSS 실거래 아침 브리핑]\n기준일 2026-09-04")
    assert sent == []


def test_daily_brief_display_window_is_one_hour_from_configured_time():
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app.config = SimpleNamespace(
        scheduler=SimpleNamespace(
            daily_analysis_time_kst=datetime.strptime("07:00", "%H:%M").time()
        )
    )

    assert app._daily_brief_display_window_open(
        datetime(2026, 9, 6, 7, 30, tzinfo=SEOUL_TZ)
    )
    assert not app._daily_brief_display_window_open(
        datetime(2026, 9, 6, 8, 0, tzinfo=SEOUL_TZ)
    )
    assert not app._daily_brief_display_window_open(
        datetime(2026, 9, 6, 22, 31, tzinfo=SEOUL_TZ)
    )
