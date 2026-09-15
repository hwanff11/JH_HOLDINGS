from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from jd_holdings.core.indicators import MarketDataError
from jd_holdings.infrastructure.dual_market_data import (
    TOSS_DAILY_FALLBACK_CANDLES,
    YahooTossDualDataSource,
)
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def _frame(*dates: str) -> pd.DataFrame:
    index = pd.to_datetime(list(dates))
    count = len(index)
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(count)],
            "high": [101.0 + i for i in range(count)],
            "low": [99.0 + i for i in range(count)],
            "close": [100.5 + i for i in range(count)],
            "volume": [1000 + i for i in range(count)],
        },
        index=index,
    )


def _candle(day: str, close: str = "110.5") -> dict[str, str]:
    return {
        "timestamp": f"{day}T22:30:00+09:00",
        "openPrice": "110.0",
        "highPrice": "112.0",
        "lowPrice": "109.0",
        "closePrice": close,
        "volume": "1234567",
        "currency": "USD",
    }


class _TossCandles:
    def __init__(self, candles: list[dict[str, str]]) -> None:
        self.candles = candles
        self.calls: list[dict[str, object]] = []

    def get_candles(self, symbol: str, **kwargs):
        self.calls.append({"symbol": symbol, **kwargs})
        return list(self.candles)


def test_live_refresh_uses_toss_adjusted_candles_when_yahoo_fails(monkeypatch, tmp_path):
    toss = _TossCandles([_candle("2026-09-14")])
    source = YahooTossDualDataSource(tmp_path, toss)
    start = date(2010, 1, 1)
    end = date(2026, 9, 14)

    cache_path = source._cache_path("SOXL", start, end)
    assert cache_path is not None
    _frame("2010-01-04", "2026-09-11").to_csv(cache_path)

    def fail_yahoo(*_args, **_kwargs):
        raise MarketDataError("Yahoo unavailable")

    monkeypatch.setattr(YFinanceDataSource, "daily", fail_yahoo)

    result = source.daily("SOXL", start, end, refresh=True)

    assert result.index[-1].date() == end
    assert float(result.iloc[-1]["close"]) == pytest.approx(110.5)
    assert toss.calls == [
        {
            "symbol": "SOXL",
            "interval": "1d",
            "count": TOSS_DAILY_FALLBACK_CANDLES,
            "adjusted": True,
        }
    ]


def test_live_refresh_replaces_stale_yahoo_tail_with_toss(monkeypatch, tmp_path):
    toss = _TossCandles([_candle("2026-09-14", close="222.25")])
    source = YahooTossDualDataSource(tmp_path, toss)
    start = date(2026, 9, 10)
    end = date(2026, 9, 14)
    stale = _frame("2026-09-10", "2026-09-11")

    monkeypatch.setattr(YFinanceDataSource, "daily", lambda *_args, **_kwargs: stale)

    result = source.daily("QQQ", start, end, refresh=True)

    assert result.index[-1].date() == end
    assert float(result.loc[pd.Timestamp(end), "close"]) == pytest.approx(222.25)
    assert toss.calls[0]["adjusted"] is True


def test_non_refresh_path_does_not_call_toss(monkeypatch, tmp_path):
    toss = _TossCandles([_candle("2026-09-14")])
    source = YahooTossDualDataSource(tmp_path, toss)

    monkeypatch.setattr(
        YFinanceDataSource,
        "daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MarketDataError("Yahoo unavailable")),
    )

    with pytest.raises(MarketDataError, match="Yahoo unavailable"):
        source.daily("QQQ", date(2026, 9, 10), date(2026, 9, 14), refresh=False)
    assert toss.calls == []


def test_dual_source_still_fails_closed_when_toss_is_stale(monkeypatch, tmp_path):
    toss = _TossCandles([_candle("2026-09-11")])
    source = YahooTossDualDataSource(tmp_path, toss)
    start = date(2026, 9, 10)
    end = date(2026, 9, 14)

    monkeypatch.setattr(
        YFinanceDataSource,
        "daily",
        lambda *_args, **_kwargs: _frame("2026-09-10", "2026-09-11"),
    )

    with pytest.raises(MarketDataError, match="Yahoo/Toss 모두"):
        source.daily("QQQ", start, end, refresh=True)
