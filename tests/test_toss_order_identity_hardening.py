from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import (
    BrokerReceiptValidationError,
    OrderManager,
)
from jd_holdings.core.models import OrderRequest
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import RuntimeSettings


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self.payload


class MissingClientOrderIdSession:
    def post(self, url, **kwargs):
        assert url.endswith("/oauth2/token")
        return FakeResponse({"access_token": "test-token", "expires_in": 86400})

    def request(self, method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/api/v1/orders")
        assert kwargs["json"]["clientOrderId"] == "JDSS-TQQQ-ID-check"
        return FakeResponse({"result": {"orderId": "broker-identity-1"}})


def _request() -> OrderRequest:
    return OrderRequest(
        client_order_id="JDSS-TQQQ-ID-check",
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("100.00"),
        purpose="IDENTITY_CHECK",
    )


def _client() -> TossClient:
    return TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=MissingClientOrderIdSession(),
    )


def test_toss_client_does_not_fabricate_missing_client_order_id():
    receipt = _client().place_order(_request())

    assert receipt.broker_order_id == "broker-identity-1"
    assert receipt.client_order_id == ""
    assert "clientOrderId" not in receipt.raw


def test_missing_order_identity_is_unknown_not_rejected(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "identity.db", config)
    settings = RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "identity.db",
        log_path=tmp_path / "identity.log",
    )
    manager = OrderManager(repository, _client(), settings)

    with pytest.raises(BrokerReceiptValidationError, match="clientOrderId"):
        manager.submit(_request(), cycle_id=None)

    local = repository.get_order_by_client_id("JDSS-TQQQ-ID-check")
    assert local is not None
    assert local["status"] == "UNKNOWN"
    assert local["broker_order_id"] is None
