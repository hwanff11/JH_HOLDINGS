from __future__ import annotations

import copy
import fcntl
import logging
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from jd_holdings.application.managed_account import (
    current_v322_capital_state,
    marked_managed_equity,
    raw_managed_cash_balance,
)
from jd_holdings.application.order_monitor import TERMINAL_STATUSES, OrderMonitor
from jd_holdings.automation.final_ops_hardening import AUTO_RAMP_STAGE_AUTO_FILL_KEY
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .live_runtime_resilience import ResilientLiveInitialOnboardingPortfolioService

AUTO_RAMP_STAGE_KEY = "jh_auto_ramp_stage"
TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS = 1.0
LOGGER = logging.getLogger(__name__)


class _FrozenDisplayQuoteBroker:
    """Serve one read-only snapshot from an immutable bulk quote set."""

    def __init__(self, prices: dict[str, Decimal]) -> None:
        self._prices = dict(prices)

    def get_price(self, symbol: str) -> Decimal:
        key = symbol.upper()
        if key not in self._prices:
            raise RuntimeError(f"Telegram 표시용 시세가 없습니다: {key}")
        return self._prices[key]


class LiveRuntimeLock:
    """Prevent two live runtimes from operating the same SQLite ledger."""

    def __init__(self, database_path: str | Path) -> None:
        database = Path(database_path)
        self.path = database.with_name(f".{database.name}.runtime.lock")
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.chmod(self.path, 0o600)
        handle = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                "동일 실거래 원장에 다른 JH AUTO runtime이 이미 실행 중입니다"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class FinalOpsLiveInitialOnboardingPortfolioService(
    ResilientLiveInitialOnboardingPortfolioService
):
    """Regular-session allocator plus fast read-only Telegram portfolio snapshots.

    The snapshot cache is intentionally presentation-only.  Order sizing, broker
    preflight, reconciliation and OrderManager never consume it, so a faster Telegram
    response cannot weaken quote freshness or trading safety.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._display_snapshot_lock = threading.Lock()
        self._display_snapshot_cache: dict[str, object] | None = None
        self._display_snapshot_cached_at = 0.0

    def _invalidate_display_snapshot(self) -> None:
        with self._display_snapshot_lock:
            self._display_snapshot_cache = None
            self._display_snapshot_cached_at = 0.0

    def _required_display_symbols(self) -> tuple[str, ...]:
        symbols = {str(symbol).upper() for symbol in ALLOCATION_SYMBOLS}
        symbols.update(str(symbol).upper() for symbol in self.config.enabled_symbols)
        if self.config.idle_cash.enabled:
            symbols.add(str(self.config.idle_cash.symbol).upper())
        return tuple(sorted(symbols))

    def _load_bulk_display_prices(self) -> dict[str, Decimal]:
        symbols = self._required_display_symbols()
        raw_prices: dict[str, object] = {}
        getter = getattr(self.broker, "get_prices", None)
        if callable(getter):
            raw_prices.update(getter(symbols))

        prices: dict[str, Decimal] = {}
        for symbol in symbols:
            raw = raw_prices.get(symbol)
            if raw is None:
                # Preserve the previous display behavior for an unexpectedly missing
                # item in a bulk response.  This fallback is read-only and rare.
                raw = self.broker.get_price(symbol)
            price = Decimal(str(raw))
            if not price.is_finite() or price <= 0:
                raise RuntimeError(f"Telegram 표시용 시세가 유효하지 않습니다: {symbol}")
            prices[symbol] = price
        return prices

    def _fresh_display_snapshot(self) -> dict[str, object]:
        prices = self._load_bulk_display_prices()
        display_broker = _FrozenDisplayQuoteBroker(prices)
        marked = marked_managed_equity(self.config, self.repository, display_broker)
        cash = raw_managed_cash_balance(self.config, self.repository)
        high_water, risk_budget = current_v322_capital_state(
            self.config,
            self.repository,
        )
        rows: list[dict[str, object]] = []
        for core in self.repository.core_positions():
            symbol = str(core["symbol"]).upper()
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
            "quote_basis": "single_bulk_latest_available_read_only",
        }

    def snapshot(self) -> dict[str, object]:
        """Return a short-lived read-only snapshot with one bulk Toss quote call.

        A 1-second cache collapses repeated snapshot requests made while formatting a
        single Telegram command (and very fast follow-up views).  Safety state such as
        operator halt, SAFE_MODE, launch authorization and open orders is still read
        directly from SQLite by the Telegram layer on every command.
        """
        current = time.monotonic()
        cached = self._display_snapshot_cache
        if (
            cached is not None
            and current - self._display_snapshot_cached_at
            <= TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS
        ):
            return copy.deepcopy(cached)

        with self._display_snapshot_lock:
            current = time.monotonic()
            cached = self._display_snapshot_cache
            if (
                cached is not None
                and current - self._display_snapshot_cached_at
                <= TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS
            ):
                return copy.deepcopy(cached)

            started = time.perf_counter()
            snapshot = self._fresh_display_snapshot()
            elapsed = time.perf_counter() - started
            self._display_snapshot_cache = copy.deepcopy(snapshot)
            self._display_snapshot_cached_at = current

        log = LOGGER.info if elapsed >= 0.75 else LOGGER.debug
        log(
            "Telegram 표시 snapshot 갱신 %.3fs (묶음시세 1회, cache %.1fs)",
            elapsed,
            TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS,
        )
        return snapshot

    def run_allocation(self, now: datetime | None = None):
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if self.market_clock.classify_session(current) != "regular":
            return None
        result = super().run_allocation(current)
        self._invalidate_display_snapshot()
        return result


class FinalOpsOrderMonitor(OrderMonitor):
    """Bound stale AUTO BUY orders and keep AUTO-cycle fill state synchronized."""

    def _sync_auto_cycle(self, local: dict[str, Any], receipt: Any) -> None:
        cycle_id = str(local.get("cycle_id") or "")
        if not cycle_id:
            return
        status = str(receipt.status).upper()
        completed_at = (
            datetime.now(UTC).isoformat()
            if status in TERMINAL_STATUSES | {"UNKNOWN"}
            else None
        )
        with self.repository.transaction() as connection:
            connection.execute(
                """
                UPDATE jh_auto_cycles
                SET status = ?, requested_qty = ?, filled_qty = ?,
                    average_fill_price = ?, broker_order_id = ?, completed_at = ?
                WHERE cycle_id = ?
                """,
                (
                    status,
                    int(receipt.quantity),
                    int(receipt.filled_quantity),
                    str(receipt.average_fill_price)
                    if receipt.average_fill_price is not None
                    else None,
                    str(receipt.broker_order_id or ""),
                    completed_at,
                    cycle_id,
                ),
            )
        ramp_stage = int(self.repository.get_system_value(AUTO_RAMP_STAGE_KEY) or "0")
        if receipt.filled_quantity > 0 and ramp_stage > 0:
            self.repository.set_system_value(AUTO_RAMP_STAGE_AUTO_FILL_KEY, "1")

    def _handle_core_order(
        self,
        local: dict,
        receipt,
        *,
        prior_filled: int,
        current: datetime,
        events: list[str],
    ) -> None:
        symbol = str(local["symbol"])
        purpose = str(local["purpose"])
        if purpose == "CORE_REBALANCE_BUY" and receipt.status not in TERMINAL_STATUSES:
            created = datetime.fromisoformat(str(local["created_at"]))
            elapsed = max(0.0, (current - created).total_seconds())
            if elapsed >= self.config.global_.buy_fill_timeout_seconds:
                before_cancel_filled = int(receipt.filled_quantity)
                try:
                    self.broker.cancel_order(receipt.broker_order_id)
                    receipt = self.order_manager.refresh_order(
                        str(local["client_order_id"])
                    )
                except Exception as exc:
                    self._enter_symbol_safe_mode(symbol, "STALE_CORE_BUY_CANCEL_UNCONFIRMED")
                    self.repository.log_event(
                        "SAFE_MODE",
                        "STALE_CORE_BUY_CANCEL_UNCONFIRMED",
                        "자동 코어 BUY가 체결대기 한도를 넘었지만 취소를 확정할 수 없습니다",
                        symbol=symbol,
                        context={
                            "client_order_id": str(local["client_order_id"]),
                            "broker_order_id": str(local.get("broker_order_id") or ""),
                            "elapsed_seconds": elapsed,
                            "filled_before_cancel": before_cancel_filled,
                            "exception": type(exc).__name__,
                        },
                    )
                    events.append(f"{symbol} 자동 BUY 취소 확인 실패: SAFE_MODE")
                    return
                if receipt.status not in TERMINAL_STATUSES:
                    self._enter_symbol_safe_mode(symbol, "STALE_CORE_BUY_CANCEL_UNCONFIRMED")
                    self.repository.log_event(
                        "SAFE_MODE",
                        "STALE_CORE_BUY_CANCEL_UNCONFIRMED",
                        "자동 코어 BUY 취소 후에도 종료 상태를 확인할 수 없습니다",
                        symbol=symbol,
                        context={
                            "client_order_id": str(local["client_order_id"]),
                            "broker_order_id": str(local.get("broker_order_id") or ""),
                            "status": str(receipt.status),
                        },
                    )
                    events.append(f"{symbol} 자동 BUY 취소 미확정: SAFE_MODE")
                    return
                events.append(
                    f"{symbol} 자동 BUY {self.config.global_.buy_fill_timeout_seconds}초 초과로 "
                    f"취소·재검토 ({receipt.status})"
                )

        try:
            super()._handle_core_order(
                local,
                receipt,
                prior_filled=prior_filled,
                current=current,
                events=events,
            )
        finally:
            self._sync_auto_cycle(local, receipt)
