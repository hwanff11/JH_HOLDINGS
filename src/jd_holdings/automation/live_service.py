from __future__ import annotations

from decimal import Decimal

from .service import (
    V322_HWM_KEY,
    V322_RISK_BUDGET_KEY,
    JHAutoService,
)


class ProductionJHAutoService(JHAutoService):
    """Production hardening for migration from the pre-AUTO V3.2.2 live ledger.

    A clean pre-AUTO live database can legitimately contain the old fixed-$50k HWM
    keys even with zero holdings/orders.  Those values describe the former execution
    capital contract and must never leak into the new operator-delegated AUTO account.
    Reset them exactly once when the first AUTO capital flow is opened.  Later capital
    changes preserve HWM through the normal unitized flow accounting.
    """

    def _apply_external_flow(
        self,
        new_effective: Decimal,
        *,
        event_type: str,
        note: str,
    ) -> None:
        before = self.settings()
        if before.effective_principal == 0 and event_type == "LAUNCH_STAGE_1":
            self.repository.set_system_value(V322_HWM_KEY, "0")
            self.repository.set_system_value(V322_RISK_BUDGET_KEY, "0")
        super()._apply_external_flow(
            new_effective,
            event_type=event_type,
            note=note,
        )
