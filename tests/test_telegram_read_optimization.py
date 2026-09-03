from __future__ import annotations

import threading
from decimal import Decimal
from types import SimpleNamespace

from jd_holdings.infrastructure import final_ops_runtime
from jd_holdings.infrastructure.final_ops_runtime import (
    TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS,
    FinalOpsLiveInitialOnboardingPortfolioService,
)


class _CountingBroker:
    def __init__(self) -> None:
        self.bulk_calls = 0
        self.single_calls = 0
        self.prices = {
            "QQQ": Decimal("500"),
            "TQQQ": Decimal("100"),
            "SOXL": Decimal("50"),
        }

    def get_prices(self, symbols):
        self.bulk_calls += 1
        return {symbol: self.prices[symbol] for symbol in symbols}

    def get_price(self, symbol):
        self.single_calls += 1
        return self.prices[symbol]


class _Repository:
    def core_positions(self):
        return [
            {
                "symbol": "QQQ",
                "underlying": "QQQ",
                "trend_active": True,
                "target_weight": "0.50",
                "qty": 2,
                "cost_basis": "900",
                "signal_trade_date": "2026-09-02",
            },
            {
                "symbol": "TQQQ",
                "underlying": "QQQ",
                "trend_active": True,
                "target_weight": "0.25",
                "qty": 1,
                "cost_basis": "90",
                "signal_trade_date": "2026-09-02",
            },
            {
                "symbol": "SOXL",
                "underlying": "SOXX",
                "trend_active": True,
                "target_weight": "0.25",
                "qty": 1,
                "cost_basis": "45",
                "signal_trade_date": "2026-09-02",
            },
        ]


def _service() -> tuple[FinalOpsLiveInitialOnboardingPortfolioService, _CountingBroker]:
    service = object.__new__(FinalOpsLiveInitialOnboardingPortfolioService)
    service.config = SimpleNamespace(
        enabled_symbols=("TQQQ", "SOXL"),
        idle_cash=SimpleNamespace(enabled=False),
    )
    service.repository = _Repository()
    broker = _CountingBroker()
    service.broker = broker
    service.policy = SimpleNamespace(initial_capital=Decimal("50000"))
    service._display_snapshot_lock = threading.Lock()
    service._display_snapshot_cache = None
    service._display_snapshot_cached_at = 0.0
    return service, broker


def test_telegram_snapshot_uses_one_bulk_quote_and_reuses_short_cache(monkeypatch):
    service, broker = _service()

    def fake_marked(_config, _repository, display_broker):
        assert display_broker.get_price("QQQ") == Decimal("500")
        assert display_broker.get_price("TQQQ") == Decimal("100")
        assert display_broker.get_price("SOXL") == Decimal("50")
        return Decimal("2150")

    monkeypatch.setattr(final_ops_runtime, "marked_managed_equity", fake_marked)
    monkeypatch.setattr(
        final_ops_runtime,
        "raw_managed_cash_balance",
        lambda _config, _repository: Decimal("1000"),
    )
    monkeypatch.setattr(
        final_ops_runtime,
        "current_v322_capital_state",
        lambda _config, _repository: (Decimal("2200"), Decimal("2100")),
    )

    first = service.snapshot()
    first["rows"][0]["price"] = Decimal("999")
    second = service.snapshot()

    assert broker.bulk_calls == 1
    assert broker.single_calls == 0
    assert second["rows"][0]["price"] == Decimal("500")
    assert second["quote_basis"] == "single_bulk_latest_available_read_only"
    assert second["risk_budget"] == Decimal("2100")

    service._display_snapshot_cached_at -= TELEGRAM_DISPLAY_SNAPSHOT_TTL_SECONDS + 1
    third = service.snapshot()
    assert broker.bulk_calls == 2
    assert third["rows"][0]["price"] == Decimal("500")


def test_telegram_snapshot_cache_invalidation_forces_fresh_bulk_read(monkeypatch):
    service, broker = _service()
    monkeypatch.setattr(
        final_ops_runtime,
        "marked_managed_equity",
        lambda _config, _repository, _broker: Decimal("2150"),
    )
    monkeypatch.setattr(
        final_ops_runtime,
        "raw_managed_cash_balance",
        lambda _config, _repository: Decimal("1000"),
    )
    monkeypatch.setattr(
        final_ops_runtime,
        "current_v322_capital_state",
        lambda _config, _repository: (Decimal("2200"), Decimal("2100")),
    )

    service.snapshot()
    service.snapshot()
    assert broker.bulk_calls == 1

    service._invalidate_display_snapshot()
    service.snapshot()
    assert broker.bulk_calls == 2
