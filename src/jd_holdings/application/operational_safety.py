from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jd_holdings.core.enums import PositionState

from .broker import Broker
from .database import SQLiteRepository
from .reconciliation import ReconciliationService

OPERATOR_BUY_HALT_KEY = "operator_buy_halt"
OPERATOR_BUY_HALT_AT_KEY = "operator_buy_halt_at"
BUY_EXECUTION_LOCK = threading.RLock()


@dataclass(frozen=True)
class OperatorHaltResult:
    halted_at: datetime
    canceled_order_ids: tuple[str, ...]
    uncertain_order_ids: tuple[str, ...]
    canceled_approvals: int

    @property
    def fully_settled(self) -> bool:
        return not self.uncertain_order_ids


class OperatorSafetyService:
    """Operator-controlled fail-closed barrier for risk-increasing BUY orders.

    The halt flag is intentionally independent from portfolio SAFE_MODE. SAFE_MODE
    protects against detected state corruption, while this flag is a manual circuit
    breaker. A halt blocks BUY at the final OrderManager boundary but leaves SELL,
    order monitoring and reconciliation available for risk reduction and recovery.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        broker: Broker,
        reconciliation: ReconciliationService,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.reconciliation = reconciliation

    def is_halted(self) -> bool:
        return self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"

    def _safe_mode_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            reasons.append("V322_PORTFOLIO_SAFE_MODE")
        for symbol in self.repository.config.enabled_symbols:
            if self.repository.get_position(symbol).state.value == "SAFE_MODE":
                reasons.append(f"{symbol}_SAFE_MODE")
        return tuple(reasons)

    def _recover_clean_reconciliation_safe_mode(self) -> tuple[str, ...]:
        """Clear only stale reconciliation SAFE_MODE after a fresh clean proof.

        V3.2.2 reconciliation intentionally sets sticky SAFE_MODE on uncertainty. A
        later clean reconciliation is necessary but was previously unable to clear
        that sticky state, which made /resume permanently impossible. Recovery is
        deliberately narrow: enabled-symbol legacy position rows must be empty and
        have no TP plan. Any non-empty/active legacy state remains fail-closed and
        requires explicit investigation rather than an automatic reset.
        """
        blocked: list[str] = []
        recoverable: list[str] = []
        for symbol in self.repository.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            if position.state != PositionState.SAFE_MODE:
                continue
            if position.quantity != 0 or self.repository.active_tp_plan(symbol) is not None:
                blocked.append(f"{symbol}_SAFE_MODE_ACTIVE_STATE")
                continue
            recoverable.append(symbol)

        if blocked:
            return tuple(blocked)

        for symbol in recoverable:
            position = self.repository.get_position(symbol)
            self.repository.transition_position(
                symbol,
                expected_state=PositionState.SAFE_MODE,
                new_state=PositionState.EMPTY,
                reason_code="CLEAN_RECONCILIATION_SAFE_MODE_RECOVERY",
                expected_version=position.version,
            )

        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            self.repository.set_system_value("v322_portfolio_safe_mode", "0")

        if recoverable:
            self.repository.log_event(
                "INFO",
                "SAFE_MODE_RECOVERED",
                "깨끗한 브로커/원장 재대조 후 비활성 SAFE_MODE를 해제했습니다",
                context={"symbols": recoverable},
            )
        return ()

    @staticmethod
    def _cancel_is_confirmed(raw_order: dict[str, Any], broker_order_id: str) -> bool:
        """Treat a cancellation as settled only when the original order says CANCELED.

        Toss cancellation returns a separate operation order id. Acceptance of that
        operation is not proof that the original order has already reached CANCELED;
        it may still be PENDING_CANCEL, may have filled in the race, or the cancel may
        later be rejected. Any state other than an explicitly matching CANCELED
        original order remains uncertain until OrderMonitor/reconciliation proves it.
        """
        returned_id = str(raw_order.get("orderId") or "")
        status = str(raw_order.get("status") or "").upper()
        return returned_id == broker_order_id and status == "CANCELED"

    def halt(self) -> OperatorHaltResult:
        halted_at = datetime.now(UTC)
        canceled: list[str] = []
        uncertain: list[str] = []

        # Serialize with OrderManager's final BUY submission section. If a BUY is
        # already inside broker.place_order, halt waits for that call to settle and
        # then attempts cancellation. Otherwise the flag is set before any new BUY
        # can cross the broker boundary.
        with BUY_EXECUTION_LOCK:
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
            self.repository.set_system_value(
                OPERATOR_BUY_HALT_AT_KEY, halted_at.isoformat()
            )

            with self.repository.transaction() as connection:
                cursor = connection.execute(
                    "UPDATE approvals SET status = 'CANCELED' WHERE status = 'ACTIVE'"
                )
                canceled_approvals = cursor.rowcount
                connection.execute(
                    """
                    UPDATE cash_release_intents
                    SET status = 'CANCELED', updated_at = ?
                    WHERE status IN ('WAITING_SGOV_FILL', 'AWAITING_EXECUTION')
                    """,
                    (halted_at.isoformat(),),
                )

            for order in self.repository.open_orders():
                if str(order.get("side") or "").upper() != "BUY":
                    continue
                client_order_id = str(order["client_order_id"])
                broker_order_id = str(order.get("broker_order_id") or "")
                if not broker_order_id:
                    uncertain.append(client_order_id)
                    continue
                try:
                    self.broker.cancel_order(broker_order_id)
                    raw_original = self.broker.get_order(broker_order_id)
                except Exception:
                    uncertain.append(client_order_id)
                else:
                    if self._cancel_is_confirmed(raw_original, broker_order_id):
                        canceled.append(client_order_id)
                    else:
                        uncertain.append(client_order_id)

        self.repository.log_event(
            "SAFE_MODE",
            "OPERATOR_BUY_HALT",
            "운영자 긴급정지로 신규 BUY를 차단했습니다",
            context={
                "canceled_order_ids": canceled,
                "uncertain_order_ids": uncertain,
                "canceled_approvals": canceled_approvals,
            },
        )
        return OperatorHaltResult(
            halted_at=halted_at,
            canceled_order_ids=tuple(canceled),
            uncertain_order_ids=tuple(uncertain),
            canceled_approvals=canceled_approvals,
        )

    def resume(self) -> dict[str, Any]:
        if not self.is_halted():
            return {"resumed": False, "reason": "already_running"}

        mismatches = self.reconciliation.run()
        if mismatches:
            raise RuntimeError(
                "브로커/원장 정합성 오류가 남아 있어 BUY 차단을 해제할 수 없습니다"
            )

        recovery_blocks = self._recover_clean_reconciliation_safe_mode()
        if recovery_blocks:
            raise RuntimeError(
                "SAFE_MODE에 실제 보유/TP 상태가 남아 자동 복구할 수 없습니다 "
                f"({', '.join(recovery_blocks)})"
            )

        safe_reasons = self._safe_mode_reasons()
        if safe_reasons:
            raise RuntimeError(
                "SAFE_MODE가 남아 있어 BUY 차단을 해제할 수 없습니다 "
                f"({', '.join(safe_reasons)})"
            )

        open_buys = [
            order
            for order in self.repository.open_orders()
            if str(order.get("side") or "").upper() == "BUY"
        ]
        if open_buys:
            raise RuntimeError(
                "미체결 BUY 주문이 남아 있어 BUY 차단을 해제할 수 없습니다"
            )

        with BUY_EXECUTION_LOCK:
            if self.repository.open_orders():
                open_buys = [
                    order
                    for order in self.repository.open_orders()
                    if str(order.get("side") or "").upper() == "BUY"
                ]
                if open_buys:
                    raise RuntimeError(
                        "BUY 차단 해제 직전에 새 미체결 BUY가 확인되었습니다"
                    )
            safe_reasons = self._safe_mode_reasons()
            if safe_reasons:
                raise RuntimeError(
                    "BUY 차단 해제 직전에 SAFE_MODE가 확인되었습니다 "
                    f"({', '.join(safe_reasons)})"
                )
            self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "0")

        self.repository.log_event(
            "INFO",
            "OPERATOR_BUY_RESUMED",
            "정합성 확인 후 운영자 BUY 차단을 해제했습니다",
        )
        return {"resumed": True, "reason": "reconciliation_passed"}