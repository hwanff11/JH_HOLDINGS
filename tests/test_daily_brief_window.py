from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jd_holdings.infrastructure.operational_safety_telegram import (
    DAILY_BRIEF_CUTOFF_NOTICE_DATE_KEY,
    OperationalSafetyTelegramBotApp,
    _daily_brief_delivery_allowed,
    _should_suppress_scheduled_daily_summary,
)
from jd_holdings.infrastructure.provider_recovery import (
    DAILY_PROVIDER_RECOVERY_PENDING_KEY,
)

SEOUL_TZ = ZoneInfo("Asia/Seoul")


def _kst(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 15, hour, minute, tzinfo=SEOUL_TZ)


def test_regular_brief_is_only_delivered_between_0700_and_0900_kst():
    scheduled = time(7, 0)

    assert not _daily_brief_delivery_allowed(_kst(6, 59), scheduled)
    assert _daily_brief_delivery_allowed(_kst(7, 0), scheduled)
    assert _daily_brief_delivery_allowed(_kst(8, 59), scheduled)
    assert not _daily_brief_delivery_allowed(_kst(9, 0), scheduled)
    assert not _daily_brief_delivery_allowed(_kst(22, 30), scheduled)


def test_late_scheduler_summary_is_suppressed_but_operator_and_recovery_messages_remain():
    scheduled = time(7, 0)
    late = _kst(22, 30)

    assert _should_suppress_scheduled_daily_summary(
        "🌅 <b>[JDSS 실거래 아침 브리핑]</b>", late, scheduled
    )
    assert _should_suppress_scheduled_daily_summary(
        "🌅 <b>[JH AUTO 아침 브리핑]</b>", late, scheduled
    )
    assert _should_suppress_scheduled_daily_summary(
        "📊 V3.2.2 목표비중 유지", late, scheduled
    )
    assert not _should_suppress_scheduled_daily_summary(
        "📊 <b>[JH AUTO 포트폴리오]</b>", late, scheduled
    )
    assert not _should_suppress_scheduled_daily_summary(
        "✅ <b>[일일 시세 자동복구 완료]</b>", late, scheduled
    )
    assert not _should_suppress_scheduled_daily_summary(
        "✅ <b>[실주문 체결 완료]</b>", late, scheduled
    )


def test_jh_auto_brief_is_allowed_at_0700_but_blocked_after_0900():
    scheduled = time(7, 0)
    text = "🌅 <b>[JH AUTO 아침 브리핑]</b>"

    assert not _should_suppress_scheduled_daily_summary(text, _kst(7, 0), scheduled)
    assert not _should_suppress_scheduled_daily_summary(text, _kst(8, 59), scheduled)
    assert _should_suppress_scheduled_daily_summary(text, _kst(9, 0), scheduled)
    assert _should_suppress_scheduled_daily_summary(text, _kst(9, 13), scheduled)


class _Repository:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_system_value(self, key: str) -> str | None:
        return self.values.get(key)

    def set_system_value(self, key: str, value: str) -> None:
        self.values[key] = value


def test_cutoff_notice_is_sent_once_and_does_not_replace_background_recovery():
    app = OperationalSafetyTelegramBotApp.__new__(OperationalSafetyTelegramBotApp)
    app.repository = _Repository()
    app.config = SimpleNamespace(
        scheduler=SimpleNamespace(daily_analysis_time_kst=time(7, 0))
    )
    messages: list[str] = []
    app._send = lambda text, **_kwargs: messages.append(text)
    app.repository.set_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY, "1")

    app._maybe_send_daily_brief_cutoff_notice(_kst(8, 59))
    assert messages == []

    app._maybe_send_daily_brief_cutoff_notice(_kst(9, 0))
    assert len(messages) == 1
    assert "오늘 아침 브리핑 미확정" in messages[0]
    assert "백그라운드에서 계속 재시도" in messages[0]
    assert app.repository.get_system_value(DAILY_BRIEF_CUTOFF_NOTICE_DATE_KEY) == (
        "2026-09-15"
    )

    app._maybe_send_daily_brief_cutoff_notice(_kst(22, 30))
    assert len(messages) == 1
