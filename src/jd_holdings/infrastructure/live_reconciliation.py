from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jd_holdings.application.reconciliation import ReconciliationService

from .provider_recovery import (
    TRANSIENT_RECON_AT_KEY,
    TRANSIENT_RECON_REASON_KEY,
    TransientReconciliationError,
    retryable_toss_read_error,
)


class _HoldingsSnapshotBroker:
    """Reuse one proven holdings snapshot while leaving order reads fully live."""

    def __init__(self, broker: Any, holdings: list[dict[str, Any]]) -> None:
        self._broker = broker
        self._holdings = [dict(item) for item in holdings]

    def get_holdings(self, symbol: str | None = None):
        if symbol is None:
            return [dict(item) for item in self._holdings]
        normalized = symbol.upper()
        return [
            dict(item)
            for item in self._holdings
            if str(item.get("symbol") or item.get("stockCode") or "").upper()
            == normalized
        ]

    def __getattr__(self, name: str):
        return getattr(self._broker, name)


class ResilientLiveReconciliationService(ReconciliationService):
    """Separate proven transient holdings outages from structural SAFE_MODE.

    Only a retryable Toss *holdings GET* is eligible for automatic transient recovery.
    OPEN-order reads and in-flight order identity remain on the canonical reconciliation
    path because an unavailable order view can make execution state ambiguous; those
    conditions therefore keep the existing sticky SAFE_MODE/manual recovery contract.
    """

    def _mark_transient(self, reason: str, exc: BaseException) -> None:
        now = datetime.now(UTC).isoformat()
        self.repository.set_system_value(TRANSIENT_RECON_REASON_KEY, reason)
        self.repository.set_system_value(TRANSIENT_RECON_AT_KEY, now)
        self.repository.log_event(
            "WARNING",
            "RECONCILIATION_PROVIDER_TRANSIENT",
            "브로커 보유수량 읽기 일시 장애로 신규 BUY를 임시 차단하고 자동 재확인합니다",
            context={"reason": reason, "exception": type(exc).__name__},
        )

    def _clear_transient(self) -> None:
        if self.repository.get_system_value(TRANSIENT_RECON_REASON_KEY):
            self.repository.set_system_value(TRANSIENT_RECON_REASON_KEY, "")
            self.repository.set_system_value(TRANSIENT_RECON_AT_KEY, "")
            self.repository.log_event(
                "INFO",
                "RECONCILIATION_PROVIDER_RECOVERED",
                "브로커 보유수량 읽기가 정상화되어 계좌·원장 정합성 검증을 다시 완료했습니다",
            )

    def run(self) -> dict[str, list[str]]:
        broker = self.broker
        try:
            holdings = broker.get_holdings()
        except Exception as exc:
            if retryable_toss_read_error(exc):
                reason = "BROKER_HOLDINGS_LOOKUP_FAILED"
                self._mark_transient(reason, exc)
                raise TransientReconciliationError(
                    reason,
                    "브로커 보유수량 읽기 일시 장애로 신규 BUY를 임시 차단하고 자동 재확인합니다",
                ) from exc
            return super().run()

        snapshot_broker = _HoldingsSnapshotBroker(broker, list(holdings))
        self.broker = snapshot_broker
        try:
            result = super().run()
        finally:
            self.broker = broker
        self._clear_transient()
        return result
