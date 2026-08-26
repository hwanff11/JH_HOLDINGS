from jd_holdings.infrastructure.telegram_bot_runtime import _format_daily_portfolio_brief


def _events() -> tuple[str, ...]:
    return (
        "V3.2.2 배분 레버리지 1.50x · RS6M ON · JDSS TQQQ ON / SOXL OFF · "
        "목표 QQQ 70.00% / TQQQ 17.50% / SOXL 12.50%",
        "HWM USD 50,000.00 · 위험예산 USD 50,000.00 · 완결종가 평가액 USD 50,000.00",
    )


def test_daily_portfolio_brief_uses_balanced_summary():
    brief, remaining = _format_daily_portfolio_brief(
        "2026-08-25",
        _events(),
        has_buy_signals=False,
    )

    assert remaining == ()
    assert brief is not None
    assert "JDSS 아침 운용 브리핑" in brief
    assert "오늘의 결론" in brief
    assert "현재 목표비중과 운용상태를 유지합니다" in brief
    assert "지금 승인할 신규 매수 주문은 없습니다" in brief
    assert "QQQ   <code>70.0%" in brief
    assert "TQQQ <code>17.5%" in brief
    assert "SOXL <code>12.5%" in brief
    assert "시장상태 : <b>🟢 공격적 운용</b>" in brief
    assert "목표 노출 : <code>1.50x</code>" in brief
    assert "반도체 상대강도 : ✅ 강세" in brief
    assert "추가 레버리지 : TQQQ ✅ / SOXL ➖" in brief
    assert "현재 평가액 : <code>$50,000.00</code>" in brief
    assert "최고 평가액 : <code>$50,000.00</code>" in brief
    assert "투자규모 계산 기준 : <code>$50,000.00</code>" in brief
    assert "수익의 75%만" in brief
    assert "RS6M" not in brief
    assert "위험예산" not in brief
    assert "완결종가 평가액" not in brief


def test_daily_portfolio_brief_highlights_buy_approval_when_signal_exists():
    brief, remaining = _format_daily_portfolio_brief(
        "2026-08-25",
        _events() + ("TQQQ 3주 매수 승인 대기",),
        has_buy_signals=True,
    )

    assert brief is not None
    assert "신규 매수 승인이 필요합니다" in brief
    assert "오늘 매수 검토 가능" in brief
    assert remaining == ("TQQQ 3주 매수 승인 대기",)


def test_daily_portfolio_brief_leaves_unrecognized_events_untouched():
    events = ("TQQQ 위험축소 2주 (FILLED, 2/2주)",)

    brief, remaining = _format_daily_portfolio_brief(
        "2026-08-25",
        events,
        has_buy_signals=False,
    )

    assert brief is None
    assert remaining == events
