from __future__ import annotations

import pandas as pd
import pytest

from jd_holdings.core.indicators import MarketDataError
from jd_holdings.infrastructure import market_data


class _EmptyTicker:
    def __init__(self, _symbol: str) -> None:
        pass

    def history(self, **_kwargs):
        return pd.DataFrame()


def test_yfinance_internal_cache_uses_jdss_cache_dir(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_data.yf,
        "set_tz_cache_location",
        lambda value: calls.append(value),
    )

    data_source = market_data.YFinanceDataSource(tmp_path / "cache")

    expected = tmp_path / "cache" / "yfinance"
    assert data_source.cache_dir == tmp_path / "cache"
    assert expected.is_dir()
    assert calls == [str(expected)]


def test_yfinance_internal_cache_is_not_reconfigured_without_cache_dir(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_data.yf,
        "set_tz_cache_location",
        lambda value: calls.append(value),
    )

    data_source = market_data.YFinanceDataSource()

    assert data_source.cache_dir is None
    assert calls == []


def _cached_prices(path):
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1000, 1100, 1200],
        },
        index=pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"]),
    )
    frame.to_csv(path)


def test_daily_uses_covering_cache_with_a_different_request_filename(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cached_prices(cache / "QQQ_2026-08-01_2026-08-05_adjusted.csv")
    monkeypatch.setattr(
        market_data.yf,
        "download",
        lambda *args, **kwargs: pytest.fail("covering cache should avoid network"),
    )

    frame = market_data.YFinanceDataSource(cache).daily(
        "QQQ", "2026-08-03", "2026-08-04"
    )

    assert list(frame.index.date) == [
        pd.Timestamp("2026-08-03").date(),
        pd.Timestamp("2026-08-04").date(),
    ]


def test_daily_falls_back_to_explicitly_stale_cache_only_without_refresh(
    tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cached_prices(cache / "QQQ_2026-08-01_2026-08-05_adjusted.csv")
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(market_data.yf, "Ticker", _EmptyTicker)
    source = market_data.YFinanceDataSource(cache)

    stale = source.daily("QQQ", "2026-08-03", "2026-08-10")

    assert stale.index[-1] == pd.Timestamp("2026-08-05")
    with pytest.raises(MarketDataError):
        source.daily("QQQ", "2026-08-03", "2026-08-10", refresh=True)


def test_daily_skips_corrupt_exact_cache_and_uses_valid_covering_cache(
    tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "QQQ_2026-08-03_2026-08-04_adjusted.csv").write_text(
        "not,ohlcv\n1,2\n", encoding="utf-8"
    )
    _cached_prices(cache / "QQQ_2026-08-01_2026-08-05_adjusted.csv")
    monkeypatch.setattr(
        market_data.yf,
        "download",
        lambda *args, **kwargs: pytest.fail("valid covering cache should be used"),
    )

    frame = market_data.YFinanceDataSource(cache).daily(
        "QQQ", "2026-08-03", "2026-08-04"
    )

    assert list(frame.index.date) == [
        pd.Timestamp("2026-08-03").date(),
        pd.Timestamp("2026-08-04").date(),
    ]


def test_daily_never_uses_cache_that_starts_after_requested_history(
    tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cached_prices(cache / "QQQ_2026-08-01_2026-08-05_adjusted.csv")
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: pd.DataFrame())

    with pytest.raises(MarketDataError):
        market_data.YFinanceDataSource(cache).daily(
            "QQQ", "2011-01-01", "2026-08-10"
        )
