from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.core.indicators import MarketDataError, normalize_ohlcv

from .market_data import CACHE_START_GRACE_DAYS, YFinanceDataSource
from .toss_client import TossApiError

LOGGER = logging.getLogger(__name__)
TOSS_DAILY_FALLBACK_CANDLES = 400
TOSS_DAILY_FALLBACK_ATTEMPTS = 3
TOSS_DAILY_FALLBACK_RETRY_BASE_SECONDS = 0.5


class YahooTossDualDataSource(YFinanceDataSource):
    """Use Yahoo as primary daily OHLCV and Toss adjusted candles as LIVE fallback.

    The production strategy has a long virtual-history warmup beginning well before the
    recent Toss chart window.  A Toss fallback therefore patches recent adjusted daily
    candles onto the best local historical cache instead of pretending a short recent
    window is sufficient for the strategy.  If neither the Yahoo response nor the
    merged cache+Toss series reaches the requested completed session, the call still
    fails closed.

    Only refresh=True callers use the Toss fallback. Research/backtest cache semantics
    remain unchanged unless a caller explicitly opts into live refresh behavior.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        toss_client: Any | None = None,
    ) -> None:
        super().__init__(cache_dir)
        self.toss_client = toss_client

    @staticmethod
    def _covers_requested_end(frame: pd.DataFrame, end: str | date | None) -> bool:
        if end is None:
            return not frame.empty
        if frame.empty:
            return False
        return frame.index[-1].date() >= pd.Timestamp(end).date()

    @staticmethod
    def _covers_requested_start(frame: pd.DataFrame, start: str | date) -> bool:
        if frame.empty:
            return False
        requested_start = pd.Timestamp(start)
        return frame.index[0] <= requested_start + pd.Timedelta(days=CACHE_START_GRACE_DAYS)

    def _get_toss_candles(self, symbol: str) -> list[dict[str, Any]]:
        if self.toss_client is None:
            raise MarketDataError("토스 일봉 fallback client가 구성되지 않았습니다")

        last_error: Exception | None = None
        for attempt in range(1, TOSS_DAILY_FALLBACK_ATTEMPTS + 1):
            try:
                candles = self.toss_client.get_candles(
                    symbol,
                    interval="1d",
                    count=TOSS_DAILY_FALLBACK_CANDLES,
                    adjusted=True,
                )
                if candles:
                    return list(candles)
                last_error = MarketDataError(f"토스 일봉 빈 응답: {symbol.upper()}")
            except TossApiError as exc:
                last_error = exc
                if not exc.retryable or attempt >= TOSS_DAILY_FALLBACK_ATTEMPTS:
                    raise
            except Exception as exc:
                last_error = exc
                if attempt >= TOSS_DAILY_FALLBACK_ATTEMPTS:
                    raise

            LOGGER.warning(
                "%s 토스 일봉 fallback 실패 (%d/%d), 재시도합니다: %s",
                symbol.upper(),
                attempt,
                TOSS_DAILY_FALLBACK_ATTEMPTS,
                last_error,
            )
            time.sleep(TOSS_DAILY_FALLBACK_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

        raise MarketDataError(f"토스 일봉 조회 실패: {symbol.upper()}") from last_error

    @staticmethod
    def _normalize_toss_candles(candles: list[dict[str, Any]]) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        index: list[pd.Timestamp] = []
        for candle in candles:
            raw_timestamp = candle.get("timestamp")
            if not raw_timestamp:
                continue
            try:
                session_date = pd.Timestamp(str(raw_timestamp)).date()
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "Open": candle.get("openPrice"),
                    "High": candle.get("highPrice"),
                    "Low": candle.get("lowPrice"),
                    "Close": candle.get("closePrice"),
                    "Volume": candle.get("volume"),
                }
            )
            index.append(pd.Timestamp(session_date))

        if not records:
            raise MarketDataError("토스 일봉 응답에 사용 가능한 OHLCV가 없습니다")
        frame = pd.DataFrame(records, index=pd.DatetimeIndex(index))
        return normalize_ohlcv(frame)

    def _toss_daily_fallback(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None,
        *,
        yahoo_frame: pd.DataFrame | None,
        yahoo_error: Exception,
    ) -> pd.DataFrame:
        candles = self._get_toss_candles(symbol)
        toss_frame = self._normalize_toss_candles(candles)

        base = yahoo_frame
        if base is None or base.empty:
            base = self._best_cache(symbol, start, end, require_full_range=False)

        if base is not None and not base.empty:
            merged = pd.concat([base, toss_frame]).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
        else:
            merged = toss_frame

        selected = self._slice_frame(merged, start, end)
        if not self._covers_requested_start(selected, start):
            raise MarketDataError(
                f"토스 fallback의 {symbol.upper()} 장기 이력이 전략 warmup 시작점을 덮지 못합니다"
            ) from yahoo_error
        if not self._covers_requested_end(selected, end):
            latest = selected.index[-1].date().isoformat() if not selected.empty else "none"
            expected = pd.Timestamp(end).date().isoformat() if end is not None else "latest"
            raise MarketDataError(
                f"Yahoo/Toss 모두 {symbol.upper()} 최신 완결 일봉을 제공하지 못했습니다 "
                f"(latest={latest}, expected={expected})"
            ) from yahoo_error

        cache_path = self._cache_path(symbol, start, end)
        if cache_path is not None:
            selected.to_csv(cache_path)
        LOGGER.warning(
            "%s Yahoo 일봉을 Toss adjusted candles로 자동 대체했습니다 (최신일 %s)",
            symbol.upper(),
            selected.index[-1].date(),
        )
        return selected

    def daily(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None = None,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        yahoo_frame: pd.DataFrame | None = None
        try:
            yahoo_frame = super().daily(symbol, start, end, refresh=refresh)
            if not refresh or self._covers_requested_end(yahoo_frame, end):
                return yahoo_frame
            latest = yahoo_frame.index[-1].date().isoformat()
            expected = pd.Timestamp(end).date().isoformat() if end is not None else "latest"
            yahoo_error: Exception = MarketDataError(
                f"Yahoo 최신 일봉 누락: {symbol.upper()} latest={latest} expected={expected}"
            )
        except MarketDataError as exc:
            yahoo_error = exc

        if not refresh or self.toss_client is None:
            raise yahoo_error

        try:
            return self._toss_daily_fallback(
                symbol,
                start,
                end,
                yahoo_frame=yahoo_frame,
                yahoo_error=yahoo_error,
            )
        except Exception as toss_error:
            if isinstance(toss_error, MarketDataError):
                raise
            raise MarketDataError(
                f"Yahoo/Toss 일봉 이중화 실패: {symbol.upper()}"
            ) from toss_error
