from __future__ import annotations

from datetime import UTC, datetime

from jd_holdings.application.allocation_trading_service import _execution_now
from jd_holdings.application.live_runtime_services import LiveAllocationTradingService
from jd_holdings.application.trading_service import QuoteChangedError, ReviewQuote
from jd_holdings.core.models import OrderReceipt

AUTO_ENABLED_KEY = "jh_auto_enabled"


class JHAutoLiveAllocationTradingService(LiveAllocationTradingService):
    """Preserve the live V3.2.2 write path and add AUTO-only session hardening."""

    def _execute_core_buy(self, signal: dict, quote: ReviewQuote) -> OrderReceipt:
        if self.repository.get_system_value(AUTO_ENABLED_KEY) == "1":
            current = _execution_now.get() or datetime.now(UTC)
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            session = self.market_clock.classify_session(current)
            if session != "regular":
                raise QuoteChangedError(
                    "JH AUTO 1.0.0 자동매수는 미국 정규장에서만 실행합니다"
                )
        return super()._execute_core_buy(signal, quote)

    def _execute_live_order(self, request, *, cycle_id: str | None):
        """Compatibility seam reserved for future live-order specialization."""
        return self.order_manager.submit(request, cycle_id=cycle_id)

    # LiveAllocationTradingService creates the OrderRequest in its implementation.
    # Override only the OrderManager call by temporarily exposing the active AUTO
    # cycle to the inherited method through OrderManager's cycle_id argument path.
    # The inherited method is intentionally not duplicated here; final cycle binding
    # is handled by OrderManager-aware monkey seam below.

    def _current_auto_cycle_id(self) -> str | None:
        value = getattr(self, "_jh_auto_cycle_id", None)
        return str(value) if value else None
