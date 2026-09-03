from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import requests

from jd_holdings.core.models import OrderRequest
from jd_holdings.infrastructure.toss_client import TossApiError, TossClient


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return FakeResponse({"access_token": "test-token", "expires_in": 86400})

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if url.endswith("/api/v1/orders") and method == "POST":
            return FakeResponse(
                {
                    "result": {
                        "orderId": "broker-1",
                        "clientOrderId": kwargs["json"]["clientOrderId"],
                    }
                }
            )
        if url.endswith("/api/v1/orders") and method == "GET":
            return FakeResponse({"result": {"orders": []}})
        if url.endswith("/api/v1/accounts"):
            return FakeResponse(
                {
                    "result": [
                        {
                            "accountNo": "PRIVATE",
                            "accountSeq": 1,
                            "accountType": "BROKERAGE",
                        }
                    ]
                }
            )
        if url.endswith("/api/v1/holdings"):
            return FakeResponse({"result": {"items": []}})
        if url.endswith("/api/v1/buying-power"):
            return FakeResponse({"result": {"cashBuyingPower": "50000"}})
        if url.endswith("/api/v1/prices"):
            return FakeResponse(
                {
                    "result": [
                        {
                            "symbol": "TQQQ",
                            "lastPrice": "100.25",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    ]
                }
            )
        if url.endswith("/api/v1/orderbook"):
            return FakeResponse(
                {
                    "result": {
                        "asks": [{"price": "100.02", "volume": "100"}],
                        "bids": [{"price": "100.01", "volume": "200"}],
                    }
                }
            )
        raise AssertionError((method, url, kwargs))


def _limit_order(*, price: Decimal = Decimal("100.50")) -> OrderRequest:
    return OrderRequest(
        client_order_id="JDSS-TQQQ-E1-abc123",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=8,
        price=price,
        purpose="ENTRY_1",
        signal_id=1,
    )


def test_current_official_order_schema_and_idempotency_key():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )
    receipt = client.place_order(_limit_order())
    assert receipt.broker_order_id == "broker-1"
    order_call = session.requests[-1][2]
    assert order_call["json"]["clientOrderId"] == "JDSS-TQQQ-E1-abc123"
    assert order_call["json"]["quantity"] == "8"
    assert order_call["json"]["price"] == "100.50"
    assert client.get_price("TQQQ") == Decimal("100.25")
    assert client.get_orderbook("SGOV")["asks"][0]["price"] == "100.02"


def test_official_account_discovery_is_source_for_account_scoped_reads():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    accounts = client.get_accounts()
    assert accounts[0]["accountSeq"] == 1
    assert client.get_holdings() == []
    assert client.list_orders(status="OPEN") == []
    assert client.get_buying_power("USD") == Decimal("50000")

    account_scoped = [
        request
        for method, url, request in session.requests
        if method == "GET"
        and any(
            url.endswith(path)
            for path in (
                "/api/v1/holdings",
                "/api/v1/orders",
                "/api/v1/buying-power",
            )
        )
    ]
    assert account_scoped
    assert all(item["headers"]["X-Tossinvest-Account"] == "1" for item in account_scoped)
    accounts_call = next(
        request
        for method, url, request in session.requests
        if url.endswith("/api/v1/accounts")
    )
    assert "X-Tossinvest-Account" not in accounts_call["headers"]


def test_market_order_omits_price_for_toss_api():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )
    client.place_order(
        OrderRequest(
            client_order_id="JDSS-SGOV-SELL-market",
            symbol="SGOV",
            side="SELL",
            order_type="MARKET",
            quantity=2,
            price=None,
            purpose="SGOV_ENTRY_RELEASE",
        )
    )
    payload = session.requests[-1][2]["json"]
    assert payload["orderType"] == "MARKET"
    assert "price" not in payload


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", "../TQQQ", "종목 코드"),
        ("side", "HOLD", "주문 방향"),
        ("order_type", "STOP", "주문 유형"),
        ("quantity", 0, "주문 수량"),
        ("price", Decimal("NaN"), "지정가 주문"),
    ],
)
def test_order_boundary_rejects_invalid_values(field, value, message):
    values = {
        "client_order_id": "JDSS-TQQQ-E1-boundary",
        "symbol": "TQQQ",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 1,
        "price": Decimal("100"),
        "purpose": "ENTRY_1",
    }
    values[field] = value
    client = TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=FakeSession(),
    )

    with pytest.raises(ValueError, match=message):
        client.place_order(OrderRequest(**values))


@pytest.mark.parametrize("price", [Decimal("100.001"), Decimal("0.12345")])
def test_us_limit_price_rejects_precision_beyond_official_schema(price):
    client = TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=FakeSession(),
    )

    with pytest.raises(ValueError, match="지정가 자릿수"):
        client.place_order(_limit_order(price=price))


def test_us_limit_price_accepts_four_decimals_below_one_dollar():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    receipt = client.place_order(_limit_order(price=Decimal("0.1234")))

    assert receipt.broker_order_id == "broker-1"
    assert session.requests[-1][2]["json"]["price"] == "0.1234"


def test_success_response_rejects_non_json_payload():
    class InvalidJsonResponse(FakeResponse):
        def json(self):
            raise ValueError("invalid json")

    class InvalidJsonSession(FakeSession):
        def request(self, method, url, **kwargs):
            return InvalidJsonResponse(None)

    client = TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=InvalidJsonSession(),
    )

    with pytest.raises(TossApiError, match="JSON 형식"):
        client.get_price("TQQQ")


def test_api_error_uses_response_header_request_id_fallback():
    response = FakeResponse(
        {"error": {"code": "account-not-found", "message": "not found"}},
        status_code=404,
        headers={"X-Request-Id": "request-123"},
    )

    error = TossClient._api_error(response)

    assert error.status_code == 404
    assert error.code == "account-not-found"
    assert error.request_id == "request-123"


def test_auth_retries_retryable_token_failure_without_order_side_effect(monkeypatch):
    sleeps = []

    class RetrySession(FakeSession):
        def __init__(self):
            super().__init__()
            self.auth_calls = 0

        def post(self, url, **kwargs):
            self.requests.append(("POST", url, kwargs))
            self.auth_calls += 1
            if self.auth_calls == 1:
                return FakeResponse(
                    {"error": {"code": "rate-limited", "message": "retry"}},
                    status_code=429,
                    headers={"Retry-After": "0"},
                )
            return FakeResponse({"access_token": "retry-token", "expires_in": 86400})

    monkeypatch.setattr(
        "jd_holdings.infrastructure.toss_client.time.sleep", sleeps.append
    )
    session = RetrySession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    assert client.authenticate() == "retry-token"
    assert session.auth_calls == 2
    assert sleeps == [0.25]
    assert not any(
        method == "POST" and url.endswith("/api/v1/orders")
        for method, url, _ in session.requests
    )


def test_auth_retries_network_failure_but_stays_bounded(monkeypatch):
    sleeps = []

    class NetworkRetrySession(FakeSession):
        def __init__(self):
            super().__init__()
            self.auth_calls = 0

        def post(self, url, **kwargs):
            self.auth_calls += 1
            if self.auth_calls < 3:
                raise requests.ConnectionError("temporary")
            return FakeResponse({"access_token": "network-recovered"})

    monkeypatch.setattr(
        "jd_holdings.infrastructure.toss_client.time.sleep", sleeps.append
    )
    session = NetworkRetrySession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    assert client.authenticate() == "network-recovered"
    assert session.auth_calls == 3
    assert sleeps == [1.0, 2.0]


def test_auth_does_not_retry_nonretryable_credential_failure(monkeypatch):
    sleeps = []

    class RejectSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.auth_calls = 0

        def post(self, url, **kwargs):
            self.auth_calls += 1
            return FakeResponse(
                {"error": {"code": "invalid-client", "message": "denied"}},
                status_code=401,
            )

    monkeypatch.setattr(
        "jd_holdings.infrastructure.toss_client.time.sleep", sleeps.append
    )
    session = RejectSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    with pytest.raises(TossApiError) as raised:
        client.authenticate()

    assert raised.value.status_code == 401
    assert session.auth_calls == 1
    assert sleeps == []


def test_read_401_refreshes_token_and_replays_only_read_request():
    class ReadRefreshSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.auth_calls = 0
            self.price_calls = 0

        def post(self, url, **kwargs):
            self.auth_calls += 1
            return FakeResponse({"access_token": f"token-{self.auth_calls}"})

        def request(self, method, url, **kwargs):
            if url.endswith("/api/v1/prices"):
                self.price_calls += 1
                if self.price_calls == 1:
                    return FakeResponse(
                        {"error": {"code": "expired-token", "message": "expired"}},
                        status_code=401,
                    )
                return FakeResponse(
                    {
                        "result": [
                            {
                                "symbol": "TQQQ",
                                "lastPrice": "100.25",
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                        ]
                    }
                )
            return super().request(method, url, **kwargs)

    session = ReadRefreshSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    assert client.get_price("TQQQ") == Decimal("100.25")
    assert session.auth_calls == 2
    assert session.price_calls == 2


@pytest.mark.parametrize(
    ("timestamp", "message"),
    [
        (None, "시각이 없습니다"),
        ("not-a-date", "시각 형식"),
        ((datetime.now(UTC) - timedelta(minutes=6)).isoformat(), "오래되어"),
        # The timestamp is built at test collection time. Keep it well outside the
        # production 120-second future-skew tolerance even when the full suite takes
        # over a minute before this parametrized case runs.
        ((datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "미래"),
    ],
)
def test_current_price_rejects_missing_stale_or_future_timestamp(timestamp, message):
    class PriceSession(FakeSession):
        def request(self, method, url, **kwargs):
            if url.endswith("/api/v1/prices"):
                item = {"symbol": "TQQQ", "lastPrice": "100.25"}
                if timestamp is not None:
                    item["timestamp"] = timestamp
                return FakeResponse({"result": [item]})
            return super().request(method, url, **kwargs)

    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=PriceSession()
    )
    with pytest.raises(TossApiError, match=message):
        client.get_price("TQQQ")


def test_write_401_refreshes_token_but_never_replays_order_automatically():
    class WriteRefreshSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.auth_calls = 0
            self.order_calls = 0

        def post(self, url, **kwargs):
            self.auth_calls += 1
            return FakeResponse({"access_token": f"token-{self.auth_calls}"})

        def request(self, method, url, **kwargs):
            if url.endswith("/api/v1/orders") and method == "POST":
                self.order_calls += 1
                if self.order_calls == 1:
                    return FakeResponse(
                        {"error": {"code": "expired-token", "message": "expired"}},
                        status_code=401,
                    )
                return FakeResponse(
                    {
                        "result": {
                            "orderId": "broker-after-explicit-retry",
                            "clientOrderId": kwargs["json"]["clientOrderId"],
                        }
                    }
                )
            return super().request(method, url, **kwargs)

    session = WriteRefreshSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )

    with pytest.raises(TossApiError) as raised:
        client.place_order(_limit_order())

    assert raised.value.status_code == 401
    assert session.auth_calls == 2
    assert session.order_calls == 1

    # A later explicit call may use the refreshed token; the first call itself was
    # never replayed across the side-effecting broker boundary.
    receipt = client.place_order(_limit_order())
    assert receipt.broker_order_id == "broker-after-explicit-retry"
    assert session.auth_calls == 2
    assert session.order_calls == 2


def test_cancel_requires_distinct_operation_order_id():
    class CancelSession(FakeSession):
        def request(self, method, url, **kwargs):
            if url.endswith("/api/v1/orders/original/cancel") and method == "POST":
                return FakeResponse({"result": {"orderId": "cancel-operation"}})
            return super().request(method, url, **kwargs)

    client = TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=CancelSession(),
    )

    assert client.cancel_order("original") == "cancel-operation"


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {}},
        {"result": {"orderId": "original"}},
    ],
)
def test_cancel_malformed_success_is_treated_as_uncertain(payload):
    class MalformedCancelSession(FakeSession):
        def request(self, method, url, **kwargs):
            if url.endswith("/api/v1/orders/original/cancel") and method == "POST":
                return FakeResponse(payload)
            return super().request(method, url, **kwargs)

    client = TossClient(
        client_id="client",
        client_secret="secret",
        account_seq="1",
        session=MalformedCancelSession(),
    )

    with pytest.raises(TossApiError) as raised:
        client.cancel_order("original")

    assert raised.value.retryable
