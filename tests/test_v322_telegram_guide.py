from __future__ import annotations

from types import SimpleNamespace

import jd_holdings.infrastructure.telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.telegram_bot import _guide_cards
from jd_holdings.infrastructure.telegram_bot_v322 import (
    V322TelegramBotApp,
    _format_open_orders_shortcut,
    _v322_bot_commands,
    _v322_runtime_text,
)


def test_final_runtime_guide_matches_v322_contract():
    assert V322TelegramBotApp is not None
    cards = _guide_cards()
    guide = "\n".join(cards)
    for expected in (
        "V3.2.2",
        "QQQ",
        "0.5 / 1.0 / 1.25 / 1.5x",
        "30%",
        "SOXX",
        "126거래일",
        "$50,000",
        "75%",
        "최대 5%",
        "2단계 승인",
        "/onboarding",
        "50% → 75% → 100%",
        "매수 주문 검토하기",
        "forced dry-run",
        "SAFE_MODE",
    ):
        assert expected in guide
    for rejected in ("V3.1.1", "독립 매수전략입니다", "SGOV 자동", "live 주문"):
        assert rejected not in guide
    assert telegram_bot_module._guide_cards is _guide_cards
    assert len(cards) == 6
    assert all(len(card) < 4096 for card in cards)


def test_final_runtime_uses_operator_friendly_terms_and_hides_sgov_menu_line():
    legacy = (
        "JDSS V3.1.1 TWIN-H40-S3 | "
        "V3.1.1 TWIN-H40-S3 포트폴리오 | JDSS H40-S3 부스터\n"
        "📊 목표 / 현재 배분\n"
        "🎯 5% 가상 오버레이 입력\n"
        "V3.2.2 allocation 위험증가 BUY\n"
        "• <code>/sgov</code> : 💵 JDSS SGOV 유휴자금\n"
        "SGOV 추정수익: $0.00"
    )
    rendered = _v322_runtime_text(legacy)
    for expected in (
        "JDSS V3.2.2 RS6M-HWM75",
        "V3.2.2 QQQ/TQQQ/SOXL 포트폴리오",
        "JDSS 5% 추가 레버리지",
        "목표 / 현재 보유비중",
        "TQQQ/SOXL 추가매수 판단",
        "V3.2.2 목표비중 맞추기 · 매수 필요",
        "SGOV 운용수익(OFF)",
    ):
        assert expected in rendered
    for rejected in (
        "V3.1.1",
        "H40-S3",
        "/sgov",
        "오버레이",
        "allocation",
        "위험증가 BUY",
    ):
        assert rejected not in rendered


def test_v322_commands_avoid_internal_operator_jargon():
    commands = {item.command: item.description for item in _v322_bot_commands()}
    assert commands["portfolio"] == "목표비중·위험한도 현황"
    assert commands["status"] == "종목별 목표·보유 상세"
    assert commands["score"] == "TQQQ/SOXL 추가매수 판단"
    assert commands["signal"] == "매수 승인 대기"
    assert all("allocation" not in value for value in commands.values())
    assert all("오버레이" not in value for value in commands.values())


def test_dashboard_buttons_are_actionable_and_plain_language():
    app = object.__new__(V322TelegramBotApp)
    app.config = SimpleNamespace(enabled_symbols=("TQQQ", "SOXL"))
    payload = app._dashboard_markup().to_dict()
    labels = [
        button["text"]
        for row in payload["inline_keyboard"]
        for button in row
    ]
    callbacks = [
        button["callback_data"]
        for row in payload["inline_keyboard"]
        for button in row
    ]
    assert "📊 QQQ 보유/목표" in labels
    assert "📊 TQQQ 보유/목표" in labels
    assert "🎯 TQQQ 추가매수 판단" in labels
    assert "🎯 SOXL 추가매수 판단" in labels
    assert "🛒 매수 승인 대기 보기" in labels
    assert "📋 미체결 주문 보기" in labels
    assert "ops|signals" in callbacks
    assert "ops|orders" in callbacks
    assert all("배분" not in label for label in labels)
    assert all("오버레이" not in label for label in labels)


def test_open_order_shortcut_is_compact_and_operator_friendly():
    rendered = _format_open_orders_shortcut(
        [
            {
                "symbol": "QQQ",
                "side": "BUY",
                "qty": 3,
                "price": "612.34",
                "status": "SUBMITTED",
            }
        ]
    )
    assert "미체결 주문" in rendered
    assert "QQQ" in rendered
    assert "매수" in rendered
    assert "3주" in rendered
    assert "$612.34" in rendered
    assert "SUBMITTED" in rendered
    assert len(rendered) < 4096
