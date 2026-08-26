from __future__ import annotations

from decimal import Decimal

from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_commissioning import LiveCommissioningPreflight
from jd_holdings.application.operational_safety import OPERATOR_BUY_HALT_KEY


class _AccountClient:
    def __init__(
        self,
        *,
        holdings=None,
        open_orders=None,
        buying_power=Decimal("50000"),
    ) -> None:
        self.holdings = holdings or []
        self.open_orders = open_orders or []
        self.buying_power = buying_power

    def get_holdings(self):
        return list(self.holdings)

    def list_orders(self, *, status):
        assert status == "OPEN"
        return list(self.open_orders)

    def get_buying_power(self, currency="USD"):
        assert currency == "USD"
        return self.buying_power


def test_fresh_separate_live_db_passes_empty_account_preflight(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    preflight = LiveCommissioningPreflight(repository, _AccountClient())

    result = preflight.run()

    assert result.safe
    assert result.issues == ()
    assert result.buying_power == Decimal("50000")


def test_live_preflight_can_arm_fail_closed_buy_halt(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    preflight = LiveCommissioningPreflight(repository, _AccountClient())

    assert preflight.run().safe
    preflight.arm_buy_halt()

    assert repository.get_system_value(OPERATOR_BUY_HALT_KEY) == "1"


def test_live_preflight_rejects_existing_managed_real_holding(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    client = _AccountClient(
        holdings=[{"symbol": "TQQQ", "quantity": "1"}],
    )

    result = LiveCommissioningPreflight(repository, client).run()

    assert not result.safe
    assert "REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:TQQQ" in result.issues


def test_live_preflight_rejects_reused_nonfresh_ledger(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    assert repository.reserve_order(
        client_order_id="OLD-DRY-ORDER",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="CORE_REBALANCE_SELL",
    )

    result = LiveCommissioningPreflight(repository, _AccountClient()).run()

    assert not result.safe
    assert "LIVE_DB_NOT_FRESH:orders:1" in result.issues


def test_live_preflight_rejects_insufficient_buying_power(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss.db", config)
    client = _AccountClient(buying_power=Decimal("49999"))

    result = LiveCommissioningPreflight(repository, client).run()

    assert not result.safe
    assert any(issue.startswith("LIVE_BUYING_POWER_BELOW_CAPITAL:") for issue in result.issues)
