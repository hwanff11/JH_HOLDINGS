from __future__ import annotations

from datetime import UTC, date, datetime, time as clock_time
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from jd_holdings.infrastructure.morning_brief_runtime import (
    DAILY_BRIEF_SENT_KST_DATE_KEY,
    MorningBriefLiveJHAutoTelegramBotApp,
)
from jd_holdings.infrastructure.provider_recovery import DailyAnalysisRetryGate


class _Repository:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, str]] = []

    def get_system_value(self, key: str):
        return self.values.get(key)

    def set_system_value(self, key: str, value: str) -> None:
        self.values[key] = value
        self.writes.append((key, value))


class _Clock:
    def latest_completed_session(self, _now=None, *, delay_minutes=0):
        assert delay_minutes == 5
        return date(2026, 9, 14)


class _Portfolio:
    def __init__(self) -> None:
        self.policy = SimpleNamespace(
            initial_capital=Decimal("50000"),
            hwm_reinvestment_fraction=Decimal("0.75"),
        )
        self.calculate_calls = 0

    def _calculate_target(self, completed):
        assert completed == date(2026, 9, 14)
        self.calculate_calls += 1
        target = pd.DataFrame(
            [
                {
                    "QQQ": 0.40,
                    "TQQQ": 0.35,
                    "SOXL": 0.25,
                    "leverage": 1.25,
                    "semiconductor_active": True,
                    "jdss_tqqq_active": False,
                    "jdss_soxl_active": False,
                }
            ],
            index=[pd.Timestamp("2026-09-14")],
        )
        return {}, target

    def _completed_marked_equity(self, _raw, _timestamp):
        return Decimal("51234.56")


def _app(*, last_analysis="2026-09-14"):
    app = object.__new__(MorningBriefLiveJHAutoTelegramBotApp)
    app.config = SimpleNamespace(
        scheduler=SimpleNamespace(
            daily_analysis_time_kst=clock_time(7, 0),
            signal_delay_minutes=5,
            poll_interval_seconds=30,
        )
    )
    app.repository = _Repository(
        {
            "last_analysis_trade_date": last_analysis,
            "jh_auto_enabled": "1",
            "jh_auto_effective_principal": "0",
        }
    )
    app.market_clock = _Clock()
    app.portfolio_service = _Portfolio()
    app._morning_brief_retry = DailyAnalysisRetryGate()
    app.sent: list[str] = []
    app._send = app.sent.append
    return app


def test_morning_preview_sends_after_analysis_without_running_allocator():
    app = _app()
    now = datetime(2026, 9, 14, 22, 5, tzinfo=UTC)  # 07:05 KST

    assert app._run_morning_brief_cycle(now)

    assert app.portfolio_service.calculate_calls == 1
    assert len(app.sent) == 1
    assert "[JDSS 실거래 아침 브리핑]" in app.sent[0]
    assert "읽기전용 전략 계산" in app.sent[0]
    assert "QQQ   <code>40.0%</code>" in app.sent[0]
    assert "미국 정규장 안전주기" in app.sent[0]
    assert app.repository.values[DAILY_BRIEF_SENT_KST_DATE_KEY] == "2026-09-15"
    assert app.repository.values["daily_brief_sent_trade_date"] == "2026-09-14"


def test_morning_preview_waits_for_canonical_daily_analysis():
    app = _app(last_analysis="2026-09-11")
    now = datetime(2026, 9, 14, 22, 5, tzinfo=UTC)

    assert not app._run_morning_brief_cycle(now)
    assert app.portfolio_service.calculate_calls == 0
    assert app.sent == []


def test_morning_preview_never_sends_full_brief_after_cutoff():
    app = _app()
    now = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)  # 09:00 KST

    assert app._run_morning_brief_cycle(now)
    assert app.portfolio_service.calculate_calls == 0
    assert len(app.sent) == 1
    assert "오늘 아침 브리핑 미확정" in app.sent[0]
    assert "JDSS 실거래 아침 브리핑" not in app.sent[0]
    assert app.repository.values["daily_brief_cutoff_notice_kst_date"] == "2026-09-15"


def test_failed_delivery_does_not_mark_brief_as_sent():
    app = _app()
    now = datetime(2026, 9, 14, 22, 5, tzinfo=UTC)

    def fail_send(_text):
        raise RuntimeError("telegram unavailable")

    app._send = fail_send

    assert not app._run_morning_brief_cycle(now)
    assert DAILY_BRIEF_SENT_KST_DATE_KEY not in app.repository.values
    assert app.repository.values["jh_auto_notification_error"] == "RuntimeError"
