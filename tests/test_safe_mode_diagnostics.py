from __future__ import annotations

from jd_holdings.infrastructure.safe_mode_diagnostics import (
    broker_diagnostic,
    operator_action,
    safe_mode_lines,
)
from jd_holdings.infrastructure.toss_client import TossApiError


def test_broker_diagnostic_finds_toss_error_through_cause_chain():
    toss = TossApiError(
        "rate limited",
        status_code=429,
        code="TOO_MANY_REQUESTS",
        request_id="req-safe-1",
        retryable=True,
    )
    outer = RuntimeError("브로커 보유수량 조회 실패로 SAFE_MODE입니다")
    outer.__cause__ = toss

    diagnostic = broker_diagnostic(outer)

    assert diagnostic is not None
    assert diagnostic.http_status == 429
    assert diagnostic.error_code == "TOO_MANY_REQUESTS"
    assert diagnostic.request_id == "req-safe-1"
    assert diagnostic.retryable is True


def test_safe_mode_lines_include_toss_metadata_and_operator_action():
    toss = TossApiError(
        "service unavailable",
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        request_id="req-safe-2",
        retryable=True,
    )

    lines = safe_mode_lines("BROKER_HOLDINGS_LOOKUP_FAILED", toss)
    text = "\n".join(lines)

    assert "HTTP: 503" in text
    assert "토스 오류코드: SERVICE_UNAVAILABLE" in text
    assert "요청 추적ID: req-safe-2" in text
    assert "임의 재주문하지 마세요" in text
    assert "/resume" in text


def test_operator_action_is_specific_for_incomplete_sell():
    action = operator_action("CORE_SELL_INCOMPLETE:PARTIAL_FILLED")
    assert "매도 체결수량" in action
    assert "미체결" in action
