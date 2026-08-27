from __future__ import annotations

from decimal import Decimal

from jd_holdings.application.account_preflight import RealAccountPreflight
from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.live_commissioning import LiveCommissioningPreflight
from jd_holdings.application.managed_account import (
    available_managed_cash,
    managed_equity,
    marked_managed_equity,
)
from jd_holdings.application.reconciliation import ReconciliationService


class AccountClient:
    account_seq = "1"

    def __init__(self) -> None:
        self._holdings = [
            {"symbol": "NVDA", "quantity": "25"},
            {"symbol": "AAPL", "quantity": "10"},
        ]
        self._orders = [
            {"orderId": "PERSONAL-NVDA", "symbol": "NVDA", "status": "PENDING"},
        ]

    def get_accounts(self):
        return [{"accountSeq": 1}]

    def get_holdings(self):
        return list(self._holdings)

    def list_orders(self, *, status):
        assert status == "OPEN"
        return list(self._orders)

    def get_buying_power(self, currency="USD"):
        assert currency == "USD"
        return Decimal("150000")


def test_real_account_preflight_allows_nonmanaged_personal_assets(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "preflight.db", config)

    result = RealAccountPreflight(repository, AccountClient()).run()

    assert result.safe
    assert result.issues == ()
    assert result.buying_power == Decimal("150000")


def test_live_commissioning_allows_nonmanaged_personal_assets(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "live" / "jdss-live.db", config)

    result = LiveCommissioningPreflight(repository, AccountClient()).run()

    assert result.safe
    assert result.issues == ()


def test_reconciliation_ignores_nonmanaged_personal_holdings(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "reconciliation.db", config)
    broker = DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
            "NVDA": Decimal("200"),
        },
        buying_power=Decimal("150000"),
    )
    broker.holdings["NVDA"] = {
        "quantity": 25,
        "averagePurchasePrice": Decimal("180"),
    }

    result = ReconciliationService(config, repository, broker).run()

    assert result == {}
    assert repository.get_system_value("v322_portfolio_safe_mode") != "1"


def test_extra_account_liquidity_never_increases_jdss_managed_capital(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    broker = DryRunBroker(
        {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
            "NVDA": Decimal("200"),
        },
        buying_power=Decimal("150000"),
    )
    broker.holdings["NVDA"] = {
        "quantity": 25,
        "averagePurchasePrice": Decimal("180"),
    }

    assert available_managed_cash(config, repository, broker) == Decimal("50000")
    assert marked_managed_equity(config, repository, broker) == Decimal("50000")
    assert managed_equity(config, repository, broker) == Decimal("50000")
