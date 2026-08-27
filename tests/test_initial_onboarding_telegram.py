from __future__ import annotations

import pytest

from jd_holdings.core.initial_onboarding import STATUS_ACTIVE, STATUS_NOT_STARTED
from jd_holdings.infrastructure.initial_onboarding_telegram import (
    InitialOnboardingTelegramBotApp,
    _augment_help_message,
    _parse_onboarding_callback,
    _validate_onboarding_callback,
)


def _snapshot(**overrides):
    value = {
        "status": STATUS_ACTIVE,
        "stage": 1,
        "total_stages": 3,
        "fraction": "0.50",
        "next_fraction": "0.75",
        "minimum_sessions_between_stages": 3,
        "stage_filled": True,
        "sessions_remaining": 0,
        "next_stage_ready": True,
        "can_start": False,
        "effective_targets": {},
        "full_targets": {},
    }
    value.update(overrides)
    return value


def test_help_message_exposes_onboarding_once():
    base = "🤖 <b>[JDSS 운영 도움말]</b>\n\n<b>자세히 보고 싶을 때</b>\n• <code>/signal</code>"
    extended = _augment_help_message(base)
    assert "<code>/onboarding</code>" in extended
    assert extended.count("<code>/onboarding</code>") == 1
    assert _augment_help_message(extended) == extended


def test_onboarding_markup_binds_expected_stage_to_callback():
    start = _snapshot(status=STATUS_NOT_STARTED, stage=0, can_start=True)
    start_markup = InitialOnboardingTelegramBotApp._onboarding_markup(start)
    assert start_markup is not None
    assert start_markup.keyboard[0][0].callback_data == "onboard|start|0"
    assert start_markup.keyboard[0][0].text == "🪜 1차 매수 한도 열기 · 50% (주문 전)"

    stage1_markup = InitialOnboardingTelegramBotApp._onboarding_markup(_snapshot(stage=1))
    assert stage1_markup is not None
    assert stage1_markup.keyboard[0][0].callback_data == "onboard|advance|1"
    assert stage1_markup.keyboard[0][0].text == (
        "🪜 2차 매수 한도 열기 · 누적 75% (주문 전)"
    )

    stage2_markup = InitialOnboardingTelegramBotApp._onboarding_markup(
        _snapshot(stage=2, fraction="0.75", next_fraction="1.0")
    )
    assert stage2_markup is not None
    assert stage2_markup.keyboard[0][0].callback_data == "onboard|advance|2"
    assert stage2_markup.keyboard[0][0].text == (
        "🪜 3차 매수 한도 열기 · 누적 100% (주문 전)"
    )


def test_stale_onboarding_callback_is_rejected():
    action, expected = _parse_onboarding_callback("onboard|advance|1")
    with pytest.raises(RuntimeError, match="오래된 단계 버튼"):
        _validate_onboarding_callback(action, expected, _snapshot(stage=2))


def test_advance_callback_requires_current_readiness():
    with pytest.raises(RuntimeError, match="아직 다음 단계"):
        _validate_onboarding_callback(
            "advance",
            1,
            _snapshot(stage=1, next_stage_ready=False),
        )


def test_start_callback_cannot_be_reused_after_activation():
    with pytest.raises(RuntimeError, match="현재 상태와 맞지 않는 시작 버튼"):
        _validate_onboarding_callback("start", 0, _snapshot(status=STATUS_ACTIVE, stage=1))


def test_invalid_onboarding_callback_shape_is_rejected():
    with pytest.raises(ValueError):
        _parse_onboarding_callback("onboard|advance")
    with pytest.raises(ValueError):
        _parse_onboarding_callback("onboard|advance|x")
