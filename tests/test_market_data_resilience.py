from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from jd_holdings.core.indicators import MarketDataError
from jd_holdings.infrastructure import market_data
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def _frame(last: str = "2026-08-24") -> pd.DataFrame:
    index = pd.to_datetime(["2026-08-21", last])
    return pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [11.0, 12.0],
            "Low": [9.0, 10.0],
            "Close": [10.5, 11.5],
            "Volume": [1000, 1100],
        },
        index=index,
    )


class _EmptyTicker:
    def __init__(self, _symbol: str) -> None:
        pass

    def history(self, **_kwargs):
        return pd.DataFrame()


def test_refresh_retries_then_succeeds(monkeypatch, tmp_path):
    source = YFinanceDataSource(tmp_path)
    calls = {"count": 0}

    def fake_download(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            return pd.DataFrame()
        return _frame()

    monkeypatch.setattr(market_data.yf, "download", fake_download)
    monkeypatch.setattr(market_data.yf, "Ticker", _EmptyTicker)
    monkeypatch.setattr(market_data.time, "sleep", lambda _: None)

    result = source.daily("SOXL", date(2026, 8, 21), date(2026, 8, 24), refresh=True)

    assert calls["count"] == 3
    assert result.index[-1].date() == date(2026, 8, 24)


def test_refresh_falls_back_to_ticker_history_after_bulk_download_outage(
    monkeypatch, tmp_path
):
    source = YFinanceDataSource(tmp_path)
    history_calls = {"count": 0}

    class RecoveringTicker:
        def __init__(self, symbol: str) -> None:
            assert symbol == "SOXL"

        def history(self, **kwargs):
            history_calls["count"] += 1
            assert kwargs["interval"] == "1d"
            assert kwargs["auto_adjust"] is True
            return _frame()

    monkeypatch.setattr(
        market_data.yf, "download", lambda *args, **kwargs: pd.DataFrame()
    )
    monkeypatch.setattr(market_data.yf, "Ticker", RecoveringTicker)
    monkeypatch.setattr(market_data.time, "sleep", lambda _: None)

    result = source.daily("SOXL", date(2026, 8, 21), date(2026, 8, 24), refresh=True)

    assert result.index[-1].date() == date(2026, 8, 24)
    assert history_calls["count"] == 1


def test_refresh_uses_cache_only_when_requested_session_is_covered(monkeypatch, tmp_path):
    source = YFinanceDataSource(tmp_path)
    cached = _frame()
    cache_path = source._cache_path("SOXL", date(2026, 8, 21), date(2026, 8, 24))
    assert cache_path is not None
    cached.to_csv(cache_path)

    monkeypatch.setattr(
        market_data.yf, "download", lambda *args, **kwargs: pd.DataFrame()
    )
    monkeypatch.setattr(market_data.yf, "Ticker", _EmptyTicker)
    monkeypatch.setattr(market_data.time, "sleep", lambda _: None)

    result = source.daily("SOXL", date(2026, 8, 21), date(2026, 8, 24), refresh=True)
    assert result.index[-1].date() == date(2026, 8, 24)

    with pytest.raises(MarketDataError, match="SOXL"):
        source.daily("SOXL", date(2026, 8, 21), date(2026, 8, 25), refresh=True)
