from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.infrastructure.jh_auto_live_display import LiveJHAutoTelegramBotApp


def _app(*, launch_authorized: bool) -> LiveJHAutoTelegramBotApp:
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app.auto_service = SimpleNamespace(
        settings=lambda: SimpleNamespace(launch_authorized=launch_authorized)
    )
    app._display_performance = lambda: {
        "equity": Decimal("11500"),
        "high_water": Decimal("12000"),
        "risk_budget": Decimal("11500"),
    }
    return app


def test_prelaunch_hwm_display_never_presents_legacy_50k_as_live_limit():
    app = _app(launch_authorized=False)
    text = (
        "• 최고 평가액 : <code>$12,000.00</code>\n"
        "• HWM75 위험한도 : <code>$11,500.00</code>"
    )

    rendered = app._replace_hwm_lines(text)

    assert "최고 평가액 : <b>시작 전</b>" in rendered
    assert "HWM75 현재 위험예산 : <b>시작 전</b>" in rendered
    assert "HWM75 위험한도 : <code>$11,500.00</code>" not in rendered


def test_postlaunch_hwm_display_keeps_value_but_clarifies_meaning():
    app = _app(launch_authorized=True)
    text = "• HWM75 위험한도 : <code>$11,500.00</code>"

    rendered = app._replace_hwm_lines(text)

    assert rendered == "• HWM75 현재 위험예산 : <code>$11,500.00</code>"


def test_capital_notes_are_idempotent():
    marker = "🛡️ <b>안전상태</b>"
    note = "JDSS 연구 기준값 안내"
    once = LiveJHAutoTelegramBotApp._insert_before(marker, marker, note)
    twice = LiveJHAutoTelegramBotApp._insert_before(once, marker, note)

    assert once == twice
    assert once.count(note) == 1


def test_prelaunch_daily_brief_hides_legacy_capital_and_manual_approval_language():
    app = _app(launch_authorized=False)
    text = "\n".join(
        [
            "🌅 <b>[JDSS 실거래 아침 브리핑]</b>",
            "목표비중 조정을 위한 <b>신규 매수 승인이 필요합니다.</b>",
            "아래에 이어지는 <b>‘오늘 매수 검토 가능’</b>에서 최종 확인해 주세요.",
            "💵 <b>자금관리</b>",
            "• 현재 평가액 : <code>$50,000.00</code>",
            "• 최고 평가액 : <code>$50,000.00</code>",
            "• 투자규모 계산 기준 : <code>$50,000.00</code>",
            "━━━━━━━━━━━━━━",
        ]
    )

    rendered = app._normalize_inherited_auto_text(text)

    assert "[JH AUTO 아침 브리핑]" in rendered
    assert "자동매수 후보가 있습니다" in rendered
    assert "개별 BUY 승인은 필요하지 않으며" in rendered
    assert "자동운용자산 : <b>시작 전</b>" in rendered
    assert "최고 평가액 : <b>시작 전</b>" in rendered
    assert "HWM75 현재 위험예산 : <b>시작 전</b>" in rendered
    assert "$50,000.00</code>" not in rendered
    assert "공식 연구·백테스트 기준값" in rendered


def test_postlaunch_daily_brief_uses_live_auto_performance_values():
    app = _app(launch_authorized=True)
    text = "\n".join(
        [
            "🌅 <b>[JDSS 실거래 아침 브리핑]</b>",
            "💵 <b>자금관리</b>",
            "• 현재 평가액 : <code>$50,000.00</code>",
            "• 최고 평가액 : <code>$50,000.00</code>",
            "• 투자규모 계산 기준 : <code>$50,000.00</code>",
            "━━━━━━━━━━━━━━",
        ]
    )

    rendered = app._normalize_inherited_auto_text(text)

    assert "자동운용자산 : <code>$11,500.00</code>" in rendered
    assert "최고 평가액 : <code>$12,000.00</code>" in rendered
    assert "HWM75 현재 위험예산 : <code>$11,500.00</code>" in rendered
    assert "투자규모 계산 기준" not in rendered


def test_resume_and_help_text_use_auto_execution_contract():
    app = _app(launch_authorized=True)
    text = (
        "• 위험증가 BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동입니다.\n"
        "각 BUY는 기존 2단계 주문 승인이 추가로 필요합니다.\n"
        "이제 신규 BUY 승인 절차를 진행할 수 있습니다.\n"
        "⚠️ 실제 주문은 여전히 기존 JDSS 2단계 승인 후에만 제출됩니다."
    )

    rendered = app._normalize_inherited_auto_text(text)

    assert "Telegram 2단계 승인" not in rendered
    assert "추가로 필요합니다" not in rendered
    assert "JH AUTO 내부 2단계 검증" in rendered
    assert "다음 독립 안전주기" in rendered
