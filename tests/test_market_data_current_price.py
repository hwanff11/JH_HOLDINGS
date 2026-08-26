from __future__ import annotations

import pandas as pd
import pytest

from jd_holdings.core.indicators import MarketDataError
from jd_holdings.infrastructure import market_data
from jd_holdings.infrastructure.market_data import YFinanceDataSource


class _FakeTicker:
    def __init__(self, symbol: str, frames: list[pd.DataFrame]) -> None:
        self.symbol = symbol
        self._frames = frames

    def history(self, **_kwargs) -> pd.DataFrame:
        if self._frames:
            return self._frames.pop(0)
        return pd.DataFrame()


def _price_frame(price: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [price]},
        index=[pd.Timestamp("2026-08-26T06:48:00-04:00")],
    )


def test_current_price_uses_download_fallback_after_empty_history(monkeypatch) -> None:
    ticker_frames = [pd.DataFrame()]
    download_frames = [_price_frame(575.25)]
    calls = {"ticker": 0, "download": 0}

    def fake_ticker(symbol: str):
        calls["ticker"] += 1
        return _FakeTicker(symbol, ticker_frames)

    def fake_download(*_args, **_kwargs):
        calls["download"] += 1
        return download_frames.pop(0)

    monkeypatch.setattr(market_data.yf, "Ticker", fake_ticker)
    monkeypatch.setattr(market_data.yf, "download", fake_download)

    timestamp, price = YFinanceDataSource().current_price("qqq")

    assert timestamp == pd.Timestamp("2026-08-26T06:48:00-04:00")
    assert price == 575.25
    assert calls == {"ticker": 1, "download": 1}


def test_current_price_retries_after_both_paths_are_temporarily_empty(monkeypatch) -> None:
    ticker_frames = [pd.DataFrame(), _price_frame(576.5)]
    download_frames = [pd.DataFrame()]
    sleeps: list[float] = []
    calls = {"ticker": 0, "download": 0}

    def fake_ticker(symbol: str):
        calls["ticker"] += 1
        return _FakeTicker(symbol, ticker_frames)

    def fake_download(*_args, **_kwargs):
        calls["download"] += 1
        return download_frames.pop(0) if download_frames else pd.DataFrame()

    monkeypatch.setattr(market_data.yf, "Ticker", fake_ticker)
    monkeypatch.setattr(market_data.yf, "download", fake_download)
    monkeypatch.setattr(market_data.time, "sleep", sleeps.append)

    _timestamp, price = YFinanceDataSource().current_price("QQQ")

    assert price == 576.5
    assert calls == {"ticker": 2, "download": 1}
    assert sleeps == [market_data.YFINANCE_CURRENT_PRICE_RETRY_BASE_SECONDS]


def test_current_price_fails_closed_after_bounded_retries(monkeypatch) -> None:
    calls = {"ticker": 0, "download": 0}
    sleeps: list[float] = []

    def fake_ticker(symbol: str):
        calls["ticker"] += 1
        return _FakeTicker(symbol, [pd.DataFrame()])

    def fake_download(*_args, **_kwargs):
        calls["download"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(market_data.yf, "Ticker", fake_ticker)
    monkeypatch.setattr(market_data.yf, "download", fake_download)
    monkeypatch.setattr(market_data.time, "sleep", sleeps.append)

    with pytest.raises(MarketDataError, match="yfinance 현재가 조회 실패: QQQ"):
        YFinanceDataSource().current_price("QQQ")

    assert calls == {
        "ticker": market_data.YFINANCE_CURRENT_PRICE_ATTEMPTS,
        "download": market_data.YFINANCE_CURRENT_PRICE_ATTEMPTS,
    }
    assert sleeps == [
        market_data.YFINANCE_CURRENT_PRICE_RETRY_BASE_SECONDS,
        market_data.YFINANCE_CURRENT_PRICE_RETRY_BASE_SECONDS * 2,
    ]
