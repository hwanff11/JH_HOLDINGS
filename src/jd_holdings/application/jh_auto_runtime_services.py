from __future__ import annotations

from datetime import UTC, datetime

from jd_holdings.application.allocation_trading_service import _execution_now
from jd_holdings.application.live_runtime_services import LiveAllocationTradingService
from jd_holdings.application.trading_service import QuoteChangedError, ReviewQuote
from jd_holdings.core.models import OrderReceipt

AUTO_ENABLED_KEY = "jh_auto_enabled"


class JHAutoLiveAllocationTradingService(LiveAllocationTradingService):
    """Preserve the live V3.2.2 write path and add AUTO-only session hardening.

    Human/manual V3.2.2 can historically use every configured US session.  JH AUTO
    1.0.0 intentionally starts more conservatively: autonomous risk-increasing BUYs
    are valid only during the US regular session.  The check is repeated immediately
    before the existing live implementation creates/submits the order.
    """

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
