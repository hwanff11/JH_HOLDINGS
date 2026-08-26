from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .broker import Broker
from .database import SQLiteRepository
from .reconciliation import ReconciliationService

OPERATOR_BUY_HALT_KEY = "operator_buy_halt"
OPERATOR_BUY_HALT_AT_KEY = "operator_buy_halt_at"


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

    The halt flag is intentionally independent from portfolio SAFE_MODE.  SAFE_MODE
    protects against detected state corruption, while this flag is a manual circuit
    breaker.  A halt blocks BUY at the final OrderManager boundary but leaves SELL,
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

    def halt(self) -> OperatorHaltResult:
        halted_at = datetime.now(UTC)

        # Set the barrier before touching broker state.  Even if cancellation fails,
        # no later BUY may pass the OrderManager boundary while the flag is set.
        self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "1")
        self.repository.set_system_value(
            OPERATOR_BUY_HALT_AT_KEY, halted_at.isoformat()
        )

        canceled_approvals = 0
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

        canceled: list[str] = []
        uncertain: list[str] = []
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
            except Exception:
                # Do not guess whether the cancellation reached the broker.  The
                # normal monitor/reconciliation loop will determine final state.
                uncertain.append(client_order_id)
            else:
                canceled.append(client_order_id)

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

        open_buys = [
            order
            for order in self.repository.open_orders()
            if str(order.get("side") or "").upper() == "BUY"
        ]
        if open_buys:
            raise RuntimeError(
                "미체결 BUY 주문이 남아 있어 BUY 차단을 해제할 수 없습니다"
            )

        self.repository.set_system_value(OPERATOR_BUY_HALT_KEY, "0")
        self.repository.log_event(
            "INFO",
            "OPERATOR_BUY_RESUMED",
            "정합성 확인 후 운영자 BUY 차단을 해제했습니다",
        )
        return {"resumed": True, "reason": "reconciliation_passed"}
