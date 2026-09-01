from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.database import SQLiteRepository
from jd_holdings.infrastructure.live_runtime_resilience import (
    ResilientLiveInitialOnboardingPortfolioService,
    ResilientReadTossClient,
)
from jd_holdings.infrastructure.toss_client import TossApiError, TossClient


def test_read_only_toss_noise_is_retried_and_recovers(monkeypatch):
    client = ResilientReadTossClient(client_id="key", client_secret="secret")
    calls = 0

    def fake_list_orders(self, *, status, symbol=None, limit=100):
        nonlocal calls
        calls += 1
        assert status == "OPEN"
        assert symbol == "TQQQ"
        assert limit == 100
        if calls < 3:
            raise TossApiError("temporary gateway error", status_code=503, retryable=True)
        return []

    monkeypatch.setattr(TossClient, "list_orders", fake_list_orders)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_resilience.time.sleep",
        lambda _seconds: None,
    )

    assert client.list_orders(status="OPEN", symbol="TQQQ") == []
    assert calls == 3


def test_read_only_invalid_token_after_refresh_race_is_retried(monkeypatch):
    client = ResilientReadTossClient(client_id="key", client_secret="secret")
    calls = 0

    def fake_get_holdings(self, symbol=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TossApiError(
                "유효하지 않은 토큰입니다.",
                status_code=401,
                code="invalid-token",
                retryable=False,
            )
        return []

    monkeypatch.setattr(TossClient, "get_holdings", fake_get_holdings)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_resilience.time.sleep",
        lambda _seconds: None,
    )

    assert client.get_holdings() == []
    assert calls == 2


def test_expired_token_read_is_retried_but_other_auth_error_fails_fast(monkeypatch):
    client = ResilientReadTossClient(client_id="key", client_secret="secret")
    calls = 0

    def fake_get_accounts(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TossApiError(
                "액세스 토큰이 만료되었습니다.",
                status_code=401,
                code="expired-token",
            )
        return []

    monkeypatch.setattr(TossClient, "get_accounts", fake_get_accounts)
    monkeypatch.setattr(
        "jd_holdings.infrastructure.live_runtime_resilience.time.sleep",
        lambda _seconds: None,
    )
    assert client.get_accounts() == []
    assert calls == 2

    def invalid_client(self):
        raise TossApiError(
            "클라이언트 인증 실패",
            status_code=401,
            code="invalid-client",
        )

    monkeypatch.setattr(TossClient, "get_accounts", invalid_client)
    with pytest.raises(TossApiError, match="클라이언트 인증 실패"):
        client.get_accounts()


def test_nonretryable_read_error_fails_fast(monkeypatch):
    client = ResilientReadTossClient(client_id="key", client_secret="secret")
    calls = 0

    def fake_get_price(self, symbol):
        nonlocal calls
        calls += 1
        raise TossApiError(f"{symbol} 현재가가 300초보다 오래되었습니다")

    monkeypatch.setattr(TossClient, "get_price", fake_get_price)

    with pytest.raises(TossApiError, match="300초"):
        client.get_price("QQQ")

    assert calls == 1


def test_order_writes_are_not_overridden_by_read_retry_layer():
    assert ResilientReadTossClient.place_order is TossClient.place_order
    assert ResilientReadTossClient.cancel_order is TossClient.cancel_order


class _StaleExecutionQuoteBroker:
    def __init__(self) -> None:
        self.display_prices = {
            "QQQ": Decimal("720.00"),
            "TQQQ": Decimal("70.00"),
            "SOXL": Decimal("115.00"),
        }
        self.strict_price_calls = 0

    def get_prices(self, symbols):
        return {symbol: self.display_prices[symbol] for symbol in symbols}

    def get_price(self, symbol):
        self.strict_price_calls += 1
        raise TossApiError(
            f"{symbol} 현재가가 300초보다 오래되어 주문 계산을 중단합니다"
        )


def test_live_snapshot_uses_latest_available_price_without_weakening_order_quote(
    tmp_path,
    config,
):
    repository = SQLiteRepository(tmp_path / "display.db", config)
    broker = _StaleExecutionQuoteBroker()
    service = ResilientLiveInitialOnboardingPortfolioService(
        config,
        repository,
        broker,
        None,
        None,
        None,
        trading_mode="live",
    )

    snapshot = service.snapshot()

    assert snapshot["quote_basis"] == "latest_available_read_only"
    assert {row["symbol"] for row in snapshot["rows"]} == {"QQQ", "TQQQ", "SOXL"}
    assert broker.strict_price_calls == 0

    with pytest.raises(TossApiError, match="주문 계산을 중단"):
        broker.get_price("QQQ")
