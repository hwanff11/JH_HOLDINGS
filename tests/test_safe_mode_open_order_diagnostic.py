from __future__ import annotations

from types import SimpleNamespace

from jd_holdings.infrastructure.live_runtime_hardening import (
    HardenedOperationalSafetyTelegramBotApp,
)
from jd_holdings.infrastructure.toss_client import TossApiError


class _FailingBroker:
    def list_orders(self, **kwargs):
        assert kwargs == {"status": "OPEN", "symbol": "SOXL", "limit": 100}
        raise TossApiError(
            "temporary open-order lookup failure",
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            request_id="req-open-order-1",
            retryable=True,
        )


class _HealthyBroker:
    def list_orders(self, **kwargs):
        assert kwargs == {"status": "OPEN", "symbol": "SOXL", "limit": 100}
        return []


def _app_with_broker(broker):
    app = object.__new__(HardenedOperationalSafetyTelegramBotApp)
    app.trading_service = SimpleNamespace(broker=broker)
    return app


def test_failed_reconciliation_open_order_probe_preserves_toss_error_for_diagnostics():
    app = _app_with_broker(_FailingBroker())
    captured = []
    app._notify_runtime_error = lambda event, title, exc: captured.append(
        (event, title, exc)
    )

    app._probe_open_order_lookup_failure("SOXL")

    assert len(captured) == 1
    event, title, exc = captured[0]
    assert event == "RECONCILIATION_OPEN_ORDER_LOOKUP_ERROR"
    assert title == "SOXL 미체결 주문 조회 오류"
    assert isinstance(exc, TossApiError)
    assert exc.status_code == 503
    assert exc.code == "SERVICE_UNAVAILABLE"
    assert exc.request_id == "req-open-order-1"
    assert exc.retryable is True


def test_transient_reconciliation_open_order_probe_does_not_auto_resume_safe_mode():
    app = _app_with_broker(_HealthyBroker())
    sent = []
    app._send = sent.append

    app._probe_open_order_lookup_failure("SOXL")

    assert len(sent) == 1
    assert "재확인 성공" in sent[0]
    assert "SAFE_MODE는 자동 해제하지 않습니다" in sent[0]
    assert "/resume" in sent[0]
