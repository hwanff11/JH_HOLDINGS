from __future__ import annotations

from datetime import UTC, datetime

from jd_holdings.application.allocation_trading_service import (
    TERMINAL_STATUSES,
    AllocationTradingService,
    _execution_approval_id,
    _execution_now,
)
from jd_holdings.application.initial_onboarding_portfolio import (
    InitialOnboardingPortfolioService,
)
from jd_holdings.application.order_manager import build_client_order_id
from jd_holdings.application.trading_service import QuoteChangedError, ReviewQuote
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_clock import (
    is_toss_order_maintenance_window,
    session_is_allowed,
)


class LiveInitialOnboardingPortfolioService(InitialOnboardingPortfolioService):
    """Use the proven V3.2.2 allocator with the obsolete live hard-lock bypassed.

    `PortfolioService.trading_mode` is otherwise used only by its historical startup
    hard-lock. Order writes do not consult this attribute; they cross OrderManager,
    whose RuntimeSettings remain `live` and enforce live confirmation + BUY halt.
    """

    def run_allocation(self, now: datetime | None = None):
        if self.trading_mode != "live":
            return super().run_allocation(now)
        if not self.config.portfolio.live_enabled:
            raise RuntimeError("portfolio live capability가 비활성화되어 있습니다")
        self.trading_mode = "dry_run"
        try:
            return super().run_allocation(now)
        finally:
            self.trading_mode = "live"


class LiveAllocationTradingService(AllocationTradingService):
    """Live-capable V3.2.2 BUY execution with all existing safety gates preserved."""

    def _execute_core_buy(self, signal: dict, quote: ReviewQuote) -> OrderReceipt:
        if not self.config.portfolio.live_enabled:
            raise RuntimeError("portfolio live capability가 비활성화되어 있습니다")
        if self.order_manager.settings.trading_mode != "live":
            raise RuntimeError("live allocation service는 live runtime에서만 사용할 수 있습니다")

        symbol = str(signal["symbol"])
        signal_id = int(signal["signal_id"])
        current = _execution_now.get() or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        signal_date = datetime.fromisoformat(str(signal["trade_date"])).date()
        if not self.market_clock.next_session_has_started(signal_date, current):
            raise QuoteChangedError("완결봉의 다음 거래일 전이어서 승인을 취소했습니다")
        if is_toss_order_maintenance_window(current):
            raise QuoteChangedError("토스 주문 점검시간이어서 승인을 취소했습니다")
        session = self.market_clock.classify_session(current)
        if not session_is_allowed(session, self.config):
            raise QuoteChangedError(
                f"주문 허용 세션이 끝나 승인을 취소했습니다: {session}"
            )
        core = self.repository.get_core_position(symbol)
        if not bool(core["trend_active"]):
            raise QuoteChangedError("V3.2.2 목표비중이 0으로 바뀌어 승인을 취소했습니다")

        approval_id = _execution_approval_id.get()
        unique_context = f"v322-v{core['version']}-a{approval_id or 'direct'}"
        client_order_id = build_client_order_id(
            symbol=symbol,
            purpose="CORE_REBALANCE_BUY",
            signal_id=signal_id,
            unique_context=unique_context,
        )
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=symbol,
            side="BUY",
            order_type="LIMIT",
            quantity=quote.quantity,
            price=quote.limit_price,
            purpose="CORE_REBALANCE_BUY",
            signal_id=signal_id,
        )

        try:
            # OrderManager remains the final live boundary: it re-checks the
            # operator BUY halt under the shared execution lock and also requires
            # the exact live-confirmation phrase before touching the broker.
            receipt = self.order_manager.submit(request, cycle_id=None)
        except Exception as exc:
            local = self.repository.get_order_by_client_id(client_order_id)
            self._set_cash_intent_status(signal_id, "CANCELED")
            if local is not None and str(local["status"]) == "UNKNOWN":
                self._enter_symbol_safe_mode(symbol, "CORE_ORDER_SUBMISSION_UNKNOWN")
                self.repository.mark_signal(
                    signal_id,
                    status="UNKNOWN",
                    processed=True,
                    reason="CORE_ORDER_SUBMISSION_UNKNOWN",
                )
                self.repository.log_event(
                    "SAFE_MODE",
                    "CORE_ORDER_SUBMISSION_UNKNOWN",
                    "V3.2.2 매수 주문의 브로커 처리 결과를 확인할 수 없습니다",
                    symbol=symbol,
                    context={
                        "signal_id": signal_id,
                        "client_order_id": client_order_id,
                        "error": str(exc),
                    },
                )
            else:
                self._reopen_core_signal(signal_id, "CORE_BUY_SUBMISSION_FAILED")
                self.repository.log_event(
                    "WARNING",
                    "CORE_BUY_SUBMISSION_FAILED",
                    "V3.2.2 매수 주문 제출 실패로 동일 신호를 재승인 가능 상태로 되돌렸습니다",
                    symbol=symbol,
                    context={
                        "signal_id": signal_id,
                        "client_order_id": client_order_id,
                        "error": str(exc),
                    },
                )
            raise

        if receipt.filled_quantity > 0:
            self.repository.apply_core_fill(client_order_id)

        remaining = max(0, receipt.quantity - receipt.filled_quantity)
        if receipt.status == "UNKNOWN":
            self._enter_symbol_safe_mode(symbol, "CORE_ORDER_STATUS_UNKNOWN")
            self.repository.mark_signal(
                signal_id,
                status="UNKNOWN",
                processed=True,
                reason="CORE_ORDER_STATUS_UNKNOWN",
            )
            self._set_cash_intent_status(signal_id, "CANCELED")
            return receipt

        if receipt.status in TERMINAL_STATUSES and remaining > 0:
            self._reopen_core_signal(signal_id, "CORE_BUY_REAPPROVAL_REQUIRED")
            self.repository.log_event(
                "WARNING",
                "CORE_BUY_INCOMPLETE",
                "V3.2.2 매수가 전량 체결되지 않아 잔여 목표를 다시 승인해야 합니다",
                symbol=symbol,
                context={
                    "signal_id": signal_id,
                    "client_order_id": client_order_id,
                    "status": receipt.status,
                    "filled_quantity": receipt.filled_quantity,
                    "quantity": receipt.quantity,
                    "remaining_quantity": remaining,
                },
            )
            self._set_cash_intent_status(signal_id, "CANCELED")
            return receipt

        self.repository.mark_signal(signal_id, status="PROCESSED", processed=True)
        self._set_cash_intent_status(signal_id, "COMPLETED")
        return receipt
