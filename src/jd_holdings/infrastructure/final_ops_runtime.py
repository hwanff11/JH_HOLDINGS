from __future__ import annotations

import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jd_holdings.application.order_monitor import OrderMonitor, TERMINAL_STATUSES
from jd_holdings.automation.final_ops_hardening import AUTO_RAMP_STAGE_AUTO_FILL_KEY

from .live_runtime_resilience import ResilientLiveInitialOnboardingPortfolioService

AUTO_RAMP_STAGE_KEY = "jh_auto_ramp_stage"


class LiveRuntimeLock:
    """Prevent two live runtimes from operating the same SQLite ledger."""

    def __init__(self, database_path: str | Path) -> None:
        database = Path(database_path)
        self.path = database.with_name(f".{database.name}.runtime.lock")
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
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
    """Keep all autonomous allocation writes aligned to the US regular session."""

    def run_allocation(self, now: datetime | None = None):
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if self.market_clock.classify_session(current) != "regular":
            return None
        return super().run_allocation(current)


class FinalOpsOrderMonitor(OrderMonitor):
    """Bound stale AUTO BUY orders and keep AUTO-cycle fill state synchronized."""

    def _sync_auto_cycle(self, local: dict[str, Any], receipt: Any) -> None:
        cycle_id = str(local.get("cycle_id") or "")
        if not cycle_id:
            return
        status = str(receipt.status).upper()
        completed_at = (
            datetime.now(UTC).isoformat() if status in TERMINAL_STATUSES | {"UNKNOWN"} else None
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
