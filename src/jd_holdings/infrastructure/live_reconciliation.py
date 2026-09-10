from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jd_holdings.application.reconciliation import (
    CORE_ORDER_PURPOSES,
    ReconciliationService,
)
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .provider_recovery import (
    TRANSIENT_RECON_AT_KEY,
    TRANSIENT_RECON_REASON_KEY,
    TransientReconciliationError,
    retryable_toss_read_error,
)


class _CachedReconciliationReads:
    """Serve broker snapshots already proven by the transient-read boundary."""

    def __init__(
        self,
        broker: Any,
        holdings: list[dict[str, Any]],
        open_orders: dict[str, list[dict[str, Any]]],
        orders: dict[str, dict[str, Any]],
    ) -> None:
        self._broker = broker
        self._holdings = [dict(item) for item in holdings]
        self._open_orders = {
            symbol: [dict(item) for item in items]
            for symbol, items in open_orders.items()
        }
        self._orders = {order_id: dict(item) for order_id, item in orders.items()}

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

    def list_orders(
        self,
        *,
        status: str,
        symbol: str | None = None,
        limit: int = 100,
    ):
        normalized_symbol = symbol.upper() if symbol else None
        if status.upper() == "OPEN" and normalized_symbol in self._open_orders:
            return [dict(item) for item in self._open_orders[normalized_symbol]][:limit]
        return self._broker.list_orders(status=status, symbol=symbol, limit=limit)

    def get_order(self, order_id: str):
        if order_id in self._orders:
            return dict(self._orders[order_id])
        return self._broker.get_order(order_id)

    def __getattr__(self, name: str):
        return getattr(self._broker, name)


class ResilientLiveReconciliationService(ReconciliationService):
    """Separate temporary read-provider outages from structural SAFE_MODE.

    Holdings, OPEN-order and known core-order status reads are side-effect-free. When
    one of those reads still fails with an explicitly retryable Toss error after the
    broker client's bounded retries, LIVE enters temporary BUY quarantine and retries
    later instead of falsely declaring ledger corruption. Once all reads succeed, the
    unchanged canonical reconciliation decides whether a *real* mismatch/UNKNOWN/order
    identity problem exists; those conditions remain sticky SAFE_MODE/manual recovery.
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
            holdings = list(broker.get_holdings())
        except Exception as exc:
            self._raise_if_transient("BROKER_HOLDINGS_LOOKUP_FAILED", exc)
            return super().run()

        # Core order status is refreshed before holdings are compared. A temporary GET
        # outage here used to become CORE_ORDER_REFRESH_FAILED SAFE_MODE even though no
        # contradictory broker state had been observed. Snapshot the read first so only
        # genuine status/identity problems reach canonical reconciliation.
        order_snapshots: dict[str, dict[str, Any]] = {}
        for local in list(self.repository.open_orders()):
            if str(local.get("purpose") or "") not in CORE_ORDER_PURPOSES:
                continue
            broker_order_id = str(local.get("broker_order_id") or "")
            if not broker_order_id:
                continue
            try:
                order_snapshots[broker_order_id] = dict(
                    broker.get_order(broker_order_id)
                )
            except Exception as exc:
                self._raise_if_transient(
                    f"BROKER_ORDER_LOOKUP_FAILED:{broker_order_id}",
                    exc,
                )
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

        cached = _CachedReconciliationReads(
            broker,
            holdings,
            open_orders,
            order_snapshots,
        )
        # Use a local canonical service rather than mutating self.broker. Telegram and
        # scheduler paths can call reconciliation concurrently; keeping the adapter
        # local prevents a second caller from ever observing the snapshot broker.
        result = ReconciliationService(
            self.config,
            self.repository,
            cached,
        ).run()
        self._clear_transient()
        return result
