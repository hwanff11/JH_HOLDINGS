from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.infrastructure.live_reconciliation import (
    ResilientLiveReconciliationService,
)
from jd_holdings.infrastructure.provider_recovery import (
    TRANSIENT_RECON_REASON_KEY,
    TransientReconciliationError,
)
from jd_holdings.infrastructure.toss_client import TossApiError


class _TransientCoreOrderBroker(DryRunBroker):
    def get_order(self, order_id: str):
        raise TossApiError(
            f"temporary order status outage: {order_id}",
            status_code=503,
            retryable=True,
        )


def test_live_reconciliation_transient_core_order_read_uses_quarantine_not_safe_mode(
    tmp_path,
    config,
):
    repository = SQLiteRepository(tmp_path / "transient-core-order.db", config)
    broker = _TransientCoreOrderBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")}
    )
    client_order_id = "CORE-TRANSIENT-READ"
    assert repository.reserve_order(
        client_order_id=client_order_id,
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("500"),
        quantity=1,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_order_id,
        status="PENDING",
        broker_order_id="BROKER-TRANSIENT-READ",
    )

    with pytest.raises(TransientReconciliationError, match="임시 차단"):
        ResilientLiveReconciliationService(config, repository, broker).run()

    assert repository.get_system_value("v322_portfolio_safe_mode") != "1"
    assert repository.get_system_value(TRANSIENT_RECON_REASON_KEY) == (
        "BROKER_ORDER_LOOKUP_FAILED:BROKER-TRANSIENT-READ"
    )
    # A read outage cannot mutate the persisted order into UNKNOWN or replay it.
    persisted = repository.get_order_by_client_id(client_order_id)
    assert persisted is not None
    assert persisted["status"] == "PENDING"
