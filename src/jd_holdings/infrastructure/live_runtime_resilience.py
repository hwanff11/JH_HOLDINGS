from __future__ import annotations

import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, TypeVar

from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    marked_managed_equity,
    raw_managed_cash_balance,
)
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .live_runtime_hardening import HardenedLiveInitialOnboardingPortfolioService
from .toss_client import TossApiError, TossClient

LOGGER = logging.getLogger(__name__)
READ_MAX_ATTEMPTS = 3
READ_RETRY_BASE_SECONDS = 0.5
T = TypeVar("T")


def _retryable_read_error(exc: TossApiError) -> bool:
    """Return whether a read-only Toss failure is safe to retry.

    Network/timeout/429/5xx failures are explicitly marked retryable by TossClient.
    A malformed successful 2xx read response is also safe to retry because the
    operation has no side effect. Authentication/configuration errors remain fail-fast.
    """
    if exc.retryable:
        return True
    status = exc.status_code
    return bool(status is not None and 200 <= status < 300 and "응답" in str(exc))


class ResilientReadTossClient(TossClient):
    """Retry only read-only Toss calls so brief API noise does not become SAFE_MODE.

    Order placement and cancellation are intentionally inherited unchanged. They are
    never automatically replayed because an ambiguous write result is trading risk.
    """

    def _retry_read(self, label: str, operation: Callable[[], T]) -> T:
        last_error: TossApiError | None = None
        for attempt in range(1, READ_MAX_ATTEMPTS + 1):
            try:
                result = operation()
            except TossApiError as exc:
                last_error = exc
                if not _retryable_read_error(exc) or attempt >= READ_MAX_ATTEMPTS:
                    raise
                LOGGER.warning(
                    "%s 일시 오류 (%d/%d), 조용히 재시도합니다: %s",
                    label,
                    attempt,
                    READ_MAX_ATTEMPTS,
                    exc,
                )
                time.sleep(READ_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
                continue
            if attempt > 1:
                LOGGER.info("%s 일시 오류가 재시도로 복구되었습니다", label)
            return result
        if last_error is None:  # pragma: no cover - defensive guard
            raise RuntimeError(f"{label} 재시도 상태가 올바르지 않습니다")
        raise last_error

    def get_prices(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Decimal]:
        return self._retry_read(
            "토스 현재가 묶음 조회",
            lambda: TossClient.get_prices(self, symbols),
        )

    def get_price(self, symbol: str) -> Decimal:
        return self._retry_read(
            f"{symbol.upper()} 토스 현재가 조회",
            lambda: TossClient.get_price(self, symbol),
        )

    def get_accounts(self) -> list[dict[str, Any]]:
        return self._retry_read("토스 계좌 목록 조회", lambda: TossClient.get_accounts(self))

    def get_holdings(self, symbol: str | None = None) -> list[dict[str, Any]]:
        label = f"{symbol.upper()} 보유수량 조회" if symbol else "토스 전체 보유수량 조회"
        return self._retry_read(
            label,
            lambda: TossClient.get_holdings(self, symbol),
        )

    def get_buying_power(self, currency: str = "USD") -> Decimal:
        return self._retry_read(
            f"토스 {currency.upper()} 매수가능금액 조회",
            lambda: TossClient.get_buying_power(self, currency),
        )

    def get_market_calendar(self, market_date: str | None = None) -> dict[str, Any]:
        return self._retry_read(
            "토스 미국 거래일 조회",
            lambda: TossClient.get_market_calendar(self, market_date),
        )

    def get_orderbook(self, symbol: str) -> dict[str, Any]:
        return self._retry_read(
            f"{symbol.upper()} 토스 호가 조회",
            lambda: TossClient.get_orderbook(self, symbol),
        )

    def list_orders(
        self,
        *,
        status: str,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        target = symbol.upper() if symbol else "전체"
        return self._retry_read(
            f"{target} 토스 {status.upper()} 주문 조회",
            lambda: TossClient.list_orders(
                self,
                status=status,
                symbol=symbol,
                limit=limit,
            ),
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._retry_read(
            "토스 개별 주문 조회",
            lambda: TossClient.get_order(self, order_id),
        )


class _DisplayQuoteBroker:
    """Use the latest available Toss price for read-only dashboards.

    TossClient.get_price intentionally rejects quotes older than 300 seconds because
    that path can size a real order. Dashboards are read-only, so weekends and market
    closures should display the latest available quote instead of failing entirely.
    """

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    def get_price(self, symbol: str) -> Decimal:
        getter = getattr(self._broker, "get_prices", None)
        if callable(getter):
            prices = getter((symbol.upper(),))
            raw = prices.get(symbol.upper())
            if raw is not None:
                price = Decimal(str(raw))
                if price.is_finite() and price > 0:
                    return price
        return Decimal(str(self._broker.get_price(symbol)))


class ResilientLiveInitialOnboardingPortfolioService(
    HardenedLiveInitialOnboardingPortfolioService
):
    """Keep strict execution quotes while making read-only portfolio views resilient."""

    def snapshot(self) -> dict[str, object]:
        display_broker = _DisplayQuoteBroker(self.broker)
        marked = marked_managed_equity(self.config, self.repository, display_broker)
        cash = raw_managed_cash_balance(self.config, self.repository)
        high_water, risk_budget = current_v322_capital_state(
            self.config,
            self.repository,
        )
        rows: list[dict[str, object]] = []
        for core in self.repository.core_positions():
            symbol = str(core["symbol"])
            if symbol not in ALLOCATION_SYMBOLS:
                continue
            price = display_broker.get_price(symbol)
            market_value = price * int(core["qty"])
            cost_basis = Decimal(str(core["cost_basis"]))
            rows.append(
                {
                    "symbol": symbol,
                    "underlying": core["underlying"],
                    "trend_active": bool(core["trend_active"]),
                    "target_weight": Decimal(str(core["target_weight"])),
                    "core_quantity": int(core["qty"]),
                    "core_market_value": market_value,
                    "price": price,
                    "cost_basis": cost_basis,
                    "unrealized_profit": market_value - cost_basis,
                    "core_weight": market_value / marked if marked else Decimal("0"),
                    "booster_quantity": 0,
                    "booster_cost_basis": Decimal("0"),
                    "signal_trade_date": core["signal_trade_date"],
                }
            )
        return {
            "equity": marked,
            "cash": cash,
            "invested_market_value": sum(
                Decimal(str(row["core_market_value"])) for row in rows
            ),
            "net_profit": marked - self.policy.initial_capital,
            "risk_budget": risk_budget,
            "high_water": high_water,
            "rows": rows,
            "quote_basis": "latest_available_read_only",
        }
