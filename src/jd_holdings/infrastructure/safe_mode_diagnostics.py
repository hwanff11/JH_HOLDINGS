from __future__ import annotations

from dataclasses import dataclass

from jd_holdings.infrastructure.toss_client import TossApiError


@dataclass(frozen=True)
class BrokerDiagnostic:
    source: str
    summary: str
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None
    retryable: bool | None = None


def broker_diagnostic(exc: BaseException) -> BrokerDiagnostic | None:
    """Find a Toss API failure in an exception chain without exposing credentials."""
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TossApiError):
            return BrokerDiagnostic(
                source="Toss Securities OpenAPI",
                summary=str(current),
                http_status=current.status_code,
                error_code=current.code,
                request_id=current.request_id,
                retryable=current.retryable,
            )
        current = current.__cause__ or current.__context__
    return None


def operator_action(reason: str) -> str:
    code = reason.split(":", 1)[0]
    actions = {
        "BROKER_HOLDINGS_LOOKUP_FAILED": (
            "토스 앱에서 보유수량·미체결 주문을 확인하고 임의 재주문하지 마세요."
        ),
        "BROKER_ORDER_LOOKUP_FAILED": (
            "토스 앱의 주문내역을 확인하고 주문 결과가 확정될 때까지 재주문하지 마세요."
        ),
        "CORE_ORDER_REFRESH_FAILED": (
            "토스 주문내역과 실제 보유수량을 확인하고 재주문하지 마세요."
        ),
        "CORE_ORDER_STATUS_UNKNOWN": (
            "토스 앱에서 해당 주문의 최종 상태를 확인하고 재주문하지 마세요."
        ),
        "CORE_SELL_INCOMPLETE": (
            "토스 앱에서 실제 매도 체결수량과 남은 미체결 수량을 확인하세요."
        ),
        "BROKER_DB_QTY_MISMATCH": (
            "토스 실제 보유수량을 기준으로 확인하고 DB를 임의 수정하지 마세요."
        ),
        "UNKNOWN_ORDER_STATUS": "토스 주문내역에서 주문의 최종 상태를 먼저 확인하세요.",
    }
    return actions.get(
        code,
        "토스 앱에서 보유수량·미체결·최근 체결을 확인하고 임의 주문하지 마세요.",
    )


def safe_mode_lines(reason: str, exc: BaseException | None = None) -> tuple[str, ...]:
    lines = [
        f"원인코드: {reason}",
        f"조치: {operator_action(reason)}",
        "상태: 신규 BUY 차단 · 위험축소 SELL/주문감시는 가능한 범위에서 유지",
        "복구: 원인 확인 → Toss/DB 정합성 확인 → /resume 2단계 검증",
    ]
    diagnostic = broker_diagnostic(exc) if exc is not None else None
    if diagnostic is not None:
        lines.insert(1, f"발생원: {diagnostic.source}")
        if diagnostic.http_status is not None:
            lines.insert(2, f"HTTP: {diagnostic.http_status}")
        if diagnostic.error_code:
            lines.insert(3, f"토스 오류코드: {diagnostic.error_code}")
        if diagnostic.request_id:
            lines.insert(4, f"요청 추적ID: {diagnostic.request_id}")
        lines.insert(5, f"재시도 가능 오류: {'예' if diagnostic.retryable else '아니오'}")
    return tuple(lines)
