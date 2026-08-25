from __future__ import annotations

from types import SimpleNamespace

import jd_holdings.infrastructure.telegram_bot_runtime as runtime_module
from jd_holdings.infrastructure.initial_onboarding_telegram import (
    InitialOnboardingTelegramBotApp,
)
from jd_holdings.infrastructure.telegram_bot_runtime import RuntimeTelegramBotApp


class _SessionClock:
    def classify_session(self, now):
        return "closed"


class _OnboardingRepo:
    def core_positions(self):
        return [
            {"symbol": "QQQ", "qty": 5},
            {"symbol": "TQQQ", "qty": 3},
            {"symbol": "SOXL", "qty": 0},
        ]


class _FakeOnboardingService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def onboarding_snapshot(self):
        return dict(self.snapshot)


class _OnboardingApp(InitialOnboardingTelegramBotApp):
    @property
    def onboarding_service(self):
        return self._fake_onboarding_service


def test_runtime_batch_review_waits_outside_allowed_order_session(monkeypatch):
    app = object.__new__(RuntimeTelegramBotApp)
    app.market_clock = _SessionClock()
    app.config = SimpleNamespace(global_=SimpleNamespace(trading_sessions={}))
    monkeypatch.setattr(
        runtime_module,
        "_is_toss_order_maintenance_window",
        lambda now: False,
    )
    monkeypatch.setattr(runtime_module, "session_is_allowed", lambda session, config: False)

    text, markup = app._prepare_today_buy_batch()

    assert "주문 가능시간 대기" in text
    assert "closed" in text
    assert markup is None


def test_runtime_batch_review_blocks_toss_maintenance_window(monkeypatch):
    app = object.__new__(RuntimeTelegramBotApp)
    app.market_clock = _SessionClock()
    app.config = SimpleNamespace(global_=SimpleNamespace(trading_sessions={}))
    monkeypatch.setattr(
        runtime_module,
        "_is_toss_order_maintenance_window",
        lambda now: True,
    )

    text, markup = app._prepare_today_buy_batch()

    assert "토스 주문 점검시간" in text
    assert "08:50~08:59" in text
    assert markup is None


def test_onboarding_completed_stage_is_not_misreported_as_missing_buy_signal():
    app = object.__new__(_OnboardingApp)
    app.repository = _OnboardingRepo()
    app._fake_onboarding_service = _FakeOnboardingService(
        {
            "status": "ACTIVE",
            "stage": 1,
            "total_stages": 3,
            "effective_targets": {"QQQ": 5, "TQQQ": 3, "SOXL": 0},
            "stage_filled": True,
            "next_stage_ready": False,
            "sessions_remaining": 2,
        }
    )

    text = app._no_signal_message()

    assert "최초진입 단계 대기" in text
    assert "2일" in text
    assert "매수신호 생성 대기" not in text


def test_onboarding_ready_stage_tells_operator_to_open_next_limit():
    app = object.__new__(_OnboardingApp)
    app.repository = _OnboardingRepo()
    app._fake_onboarding_service = _FakeOnboardingService(
        {
            "status": "ACTIVE",
            "stage": 1,
            "total_stages": 3,
            "effective_targets": {"QQQ": 5, "TQQQ": 3, "SOXL": 0},
            "stage_filled": True,
            "next_stage_ready": True,
            "sessions_remaining": 0,
        }
    )

    text = app._no_signal_message()

    assert "현재 단계 완료" in text
    assert "/onboarding" in text
    assert "추가 BUY가 필요한 상태가 아니라" in text
