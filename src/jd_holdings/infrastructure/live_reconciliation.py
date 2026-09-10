from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .provider_recovery import (
    TRANSIENT_RECON_AT_KEY,
    TRANSIENT_RECON_REASON_KEY,
    TransientReconciliationError,
    retryable_toss_read_error,
)


class _CachedReconciliationReads:
    """Serve already-proven holdings/OPEN-order reads exactly once to reconciliation."""

    def __init__(
        self,
        broker: Any,
        holdings: list[dict[str, Any]],
        open_orders: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._broker = broker
        self._holdings = holdings
        self._open_orders = open_orders

    def get_holdings(self, symbol: str | None = None):
        if symbol is None:
            return [dict(item) for item in self._holdings]
        return self._broker.get_holdings(symbol)

    def list_orders(
        self,
        *,
        status: str,
        symbol: str | None = None,
        limit: int = 100,
    ):
        normalized_symbol = symbol.upper() if symbol else None
        if status.upper() == "OPEN" and normalized_symbol in self._open_orders:
            return [dict(item) for item in self._open_orders[normalized_symbol]]
        return self._broker.list_orders(status=status, symbol=symbol, limit=limit)

    def __getattr__(self, name: str):
        return getattr(self._broker, name)


class ResilientLiveReconciliationService(ReconciliationService):
    """Fail closed on transient broker reads without mislabeling them as corruption.

    Reconciliation still treats ambiguous in-flight order state, quantity mismatches,
    UNKNOWN orders and every other structural mismatch as sticky SAFE_MODE. Only
    read-only holdings/OPEN-order failures that are explicitly classified as retryable
    Toss provider errors are intercepted before the base reconciliation can convert
    them into persistent portfolio corruption state.
    """

    def _mark_transient(self, reason: str, exc: BaseException) -> None:
        now = datetime.now(UTC).isoformat()
        self.repository.set_system_value(TRANSIENT_RECON_REASON_KEY, reason)
        self.repository.set_system_value(TRANSIENT_RECON_AT_KEY, now)
        self.repository.log_event(
            "WARNING",
            "RECONCILIATION_PROVIDER_TRANSIENT",
            "브로커 읽기 일시 장애로 신규 BUY를 임시 차단하고 자동 재확인합니다",
            context={"reason": reason, "exception": type(exc).__name__},
        )

    def _clear_transient(self) -> None:
        if self.repository.get_system_value(TRANSIENT_RECON_REASON_KEY):
            self.repository.set_system_value(TRANSIENT_RECON_REASON_KEY, "")
            self.repository.set_system_value(TRANSIENT_RECON_AT_KEY, "")
            self.repository.log_event(
                "INFO",
                "RECONCILIATION_PROVIDER_RECOVERED",
                "브로커 읽기가 정상화되어 계좌·원장 정합성 검증을 다시 완료했습니다",
            )

    def _raise_if_transient(self, reason: str, exc: BaseException) -> None:
        if not retryable_toss_read_error(exc):
            return
        self._mark_transient(reason, exc)
        raise TransientReconciliationError(
            reason,
            "브로커 읽기 일시 장애로 신규 BUY를 임시 차단하고 자동 재확인합니다",
        ) from exc

    def run(self) -> dict[str, list[str]]:
        broker = self.broker
        try:
            holdings = broker.get_holdings()
        except Exception as exc:
            self._raise_if_transient("BROKER_HOLDINGS_LOOKUP_FAILED", exc)
            return super().run()

        symbols = list(ALLOCATION_SYMBOLS)
        if self.config.idle_cash.enabled:
            cash_symbol = str(self.config.idle_cash.symbol).upper()
            if cash_symbol not in symbols:
                symbols.append(cash_symbol)

        open_orders: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            try:
                open_orders[symbol] = list(
                    broker.list_orders(status="OPEN", symbol=symbol, limit=100)
                )
            except Exception as exc:
                self._raise_if_transient(
                    f"BROKER_OPEN_ORDER_LOOKUP_FAILED:{symbol}",
                    exc,
                )
                return super().run()

        cached = _CachedReconciliationReads(broker, list(holdings), open_orders)
        self.broker = cached
        try:
            result = super().run()
        finally:
            self.broker = broker
        self._clear_transient()
        return result
