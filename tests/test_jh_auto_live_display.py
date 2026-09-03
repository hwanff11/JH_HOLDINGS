from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.infrastructure.jh_auto_live_display import LiveJHAutoTelegramBotApp


def _app(*, launch_authorized: bool) -> LiveJHAutoTelegramBotApp:
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app.auto_service = SimpleNamespace(
        settings=lambda: SimpleNamespace(launch_authorized=launch_authorized)
    )
    app._display_performance = lambda: {
        "high_water": Decimal("50000"),
        "risk_budget": Decimal("50000"),
    }
    return app


def test_prelaunch_hwm_display_never_presents_legacy_50k_as_live_limit():
    app = _app(launch_authorized=False)
    text = (
        "• 최고 평가액 : <code>$50,000.00</code>\n"
        "• HWM75 위험한도 : <code>$50,000.00</code>"
    )

    rendered = app._replace_hwm_lines(text)

    assert "최고 평가액 : <b>시작 전</b>" in rendered
    assert "HWM75 현재 위험예산 : <b>시작 전</b>" in rendered
    assert "HWM75 위험한도 : <code>$50,000.00</code>" not in rendered


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
