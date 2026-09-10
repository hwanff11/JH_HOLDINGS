from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.automation.final_ops_hardening import FinalOpsProductionJHAutoService
from jd_holdings.automation.service import AUTO_OPERATOR_HALT_LATCH_KEY
from jd_holdings.core.indicators import MarketDataError

from .jh_auto_live_display import LiveJHAutoTelegramBotApp
from .toss_client import TossApiError

LOGGER = logging.getLogger(__name__)
SEOUL_TZ = ZoneInfo("Asia/Seoul")
RECOVERABLE_AUTH_CODES = {"invalid-token", "expired-token", "token-revoked"}
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

PROVIDER_TRANSIENT_QUARANTINE_KEY = "jh_auto_provider_transient_quarantine"
PROVIDER_CLEAN_STREAK_KEY = "jh_auto_provider_clean_streak"
PROVIDER_RECOVERY_NOTICE_KEY = "jh_auto_provider_recovery_notice_pending"
PROVIDER_RELEASE_HOLD_KEY = "jh_auto_provider_release_hold_one_cycle"
MARKET_DATA_DEGRADED_KEY = "jh_daily_market_data_degraded"
MARKET_DATA_RETRY_NEXT_KEY = "jh_daily_market_data_retry_next_at"
MARKET_DATA_FAILURE_COUNT_KEY = "jh_daily_market_data_failure_count"
MARKET_DATA_RECOVERY_NOTICE_KEY = "jh_daily_market_data_recovery_notice_pending"
MARKET_DATA_LAST_FAILURE_KEY = "jh_daily_market_data_last_failure_at"

MARKET_DATA_RETRY_INTERVAL_MINUTES = (5, 10, 15, 30, 60)
REQUIRED_PROVIDER_CLEAN_CYCLES = 2


class TransientBrokerReadError(RuntimeError):
    """A broker read is temporarily unavailable; state corruption is not proven."""


class MarketDataDeferredError(RuntimeError):
    """A scheduled market-data retry is intentionally waiting for its next window."""


def _normalize_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _walk_exception_chain(exc: BaseException):
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_transient_broker_read_error(exc: BaseException) -> bool:
    """Classify only failures that are safe to retry because the operation is read-only."""
    for current in _walk_exception_chain(exc):
        if isinstance(current, TossApiError):
            if current.retryable:
                return True
            if current.status_code in TRANSIENT_HTTP_STATUSES:
                return True
            if current.status_code == 401 and current.code in RECOVERABLE_AUTH_CODES:
                return True
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
    return False


def is_market_data_failure(exc: BaseException) -> bool:
    for current in _walk_exception_chain(exc):
        if isinstance(current, MarketDataError):
            return True
        text = str(current).lower()
        if "yfinance" in text and ("일봉" in text or "조회" in text):
            return True
        if "완결 일봉 거래일" in str(current):
            return True
    return False


class _HoldingsSnapshotBroker:
    """Delegate every broker operation except one already-proven holdings snapshot."""

    def __init__(self, broker: Any, holdings: list[dict[str, Any]]) -> None:
        self._broker = broker
        self._holdings = [dict(item) for item in holdings]

    def get_holdings(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol is None:
            return [dict(item) for item in self._holdings]
        normalized = symbol.upper()
        return [
            dict(item)
            for item in self._holdings
            if str(item.get("symbol") or item.get("stockCode") or "").upper()
            == normalized
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._broker, name)


class TransientAwareReconciliationService(ReconciliationService):
    """Keep true mismatches sticky while routing temporary holdings outages to quarantine.

    The broker holdings read is performed once before the canonical reconciliation.
    A transient read failure therefore never reaches the canonical code path that
    deliberately latches portfolio SAFE_MODE. If the read succeeds, the exact snapshot
    is passed into an otherwise unchanged ReconciliationService run, so quantity/order
    mismatch and UNKNOWN handling remain fail-closed and sticky.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._transient_handler: Callable[[str, BaseException], None] | None = None

    def set_transient_handler(
        self, handler: Callable[[str, BaseException], None] | None
    ) -> None:
        self._transient_handler = handler

    def _handle_transient(self, reason: str, exc: BaseException) -> None:
        self.repository.log_event(
            "WARNING",
            "BROKER_READ_DEGRADED",
            "브로커 읽기 API가 일시적으로 불안정하여 신규 BUY를 임시 차단합니다",
            context={"reason": reason, "exception": type(exc).__name__},
        )
        handler = self._transient_handler
        if handler is None:
            self.repository.set_system_value("operator_buy_halt", "1")
            self.repository.set_system_value(
                "operator_buy_halt_at", datetime.now(UTC).isoformat()
            )
            return
        try:
            handler(reason, exc)
        except Exception:
            # If the higher-level quarantine state cannot be persisted, keep the
            # lowest-level BUY boundary closed rather than allowing a read outage to
            # weaken execution safety.
            LOGGER.exception("transient provider quarantine callback failed")
            self.repository.set_system_value("operator_buy_halt", "1")
            self.repository.set_system_value(
                "operator_buy_halt_at", datetime.now(UTC).isoformat()
            )

    def run(self) -> dict[str, list[str]]:
        try:
            holdings = self.broker.get_holdings()
        except Exception as exc:
            if not is_transient_broker_read_error(exc):
                # Preserve the historical sticky SAFE_MODE behavior for credential
                # misconfiguration, malformed responses and other non-transient faults.
                return super().run()
            self._handle_transient("BROKER_HOLDINGS_READ_DEGRADED", exc)
            raise TransientBrokerReadError(
                "브로커 보유수량 일시 조회 장애로 신규 BUY를 임시 차단합니다"
            ) from exc

        snapshot_broker = _HoldingsSnapshotBroker(self.broker, holdings)
        canonical = ReconciliationService(
            self.config,
            self.repository,
            snapshot_broker,
        )
        return canonical.run()


class MarketDataRetryCoordinator:
    """Persist provider backoff so restarts do not cause a Yahoo retry storm."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self._lock = threading.RLock()

    def _failure_count(self) -> int:
        raw = self.repository.get_system_value(MARKET_DATA_FAILURE_COUNT_KEY) or "0"
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    def next_retry_at(self) -> datetime | None:
        raw = self.repository.get_system_value(MARKET_DATA_RETRY_NEXT_KEY)
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return _normalize_utc(value)

    def is_degraded(self) -> bool:
        return self.repository.get_system_value(MARKET_DATA_DEGRADED_KEY) == "1"

    def before_attempt(self, now: datetime | None = None) -> None:
        current = _normalize_utc(now)
        next_retry = self.next_retry_at()
        if self.is_degraded() and next_retry is not None and current < next_retry:
            next_kst = next_retry.astimezone(SEOUL_TZ).strftime("%H:%M KST")
            raise MarketDataDeferredError(
                f"일일 시세 자동 재시도 대기 중입니다. 다음 시도: {next_kst}"
            )

    def record_failure(self, exc: BaseException, now: datetime | None = None) -> None:
        current = _normalize_utc(now)
        with self._lock:
            count = self._failure_count() + 1
            interval = MARKET_DATA_RETRY_INTERVAL_MINUTES[
                min(count - 1, len(MARKET_DATA_RETRY_INTERVAL_MINUTES) - 1)
            ]
            next_retry = current + timedelta(minutes=interval)
            self.repository.set_system_value(MARKET_DATA_DEGRADED_KEY, "1")
            self.repository.set_system_value(MARKET_DATA_FAILURE_COUNT_KEY, str(count))
            self.repository.set_system_value(
                MARKET_DATA_RETRY_NEXT_KEY, next_retry.isoformat()
            )
            self.repository.set_system_value(
                MARKET_DATA_LAST_FAILURE_KEY, current.isoformat()
            )
            self.repository.set_system_value(MARKET_DATA_RECOVERY_NOTICE_KEY, "0")
            self.repository.log_event(
                "WARNING",
                "MARKET_DATA_RETRY_SCHEDULED",
                "일일 시세 조회 실패로 자동 재시도 시간을 예약했습니다",
                context={
                    "failure_count": count,
                    "retry_interval_minutes": interval,
                    "next_retry_at": next_retry.isoformat(),
                    "exception": type(exc).__name__,
                },
            )

    def record_success(self, now: datetime | None = None) -> bool:
        del now
        with self._lock:
            recovered = self.is_degraded()
            self.repository.set_system_value(MARKET_DATA_DEGRADED_KEY, "0")
            self.repository.set_system_value(MARKET_DATA_FAILURE_COUNT_KEY, "0")
            self.repository.set_system_value(MARKET_DATA_RETRY_NEXT_KEY, "")
            self.repository.set_system_value(MARKET_DATA_LAST_FAILURE_KEY, "")
            if recovered:
                self.repository.set_system_value(MARKET_DATA_RECOVERY_NOTICE_KEY, "1")
                self.repository.log_event(
                    "INFO",
                    "MARKET_DATA_RECOVERED",
                    "일일 시세 공급자가 자동 재시도로 정상화되었습니다",
                )
            return recovered

    def failure_count(self) -> int:
        return self._failure_count()

    def consume_recovery_notice(self) -> bool:
        if self.repository.get_system_value(MARKET_DATA_RECOVERY_NOTICE_KEY) != "1":
            return False
        self.repository.set_system_value(MARKET_DATA_RECOVERY_NOTICE_KEY, "0")
        return True


class ScheduledRetryAnalysisService(AnalysisService):
    """Use a persisted 5/10/15/30/60-minute provider retry cadence in LIVE."""

    def __init__(self, *args, retry_coordinator: MarketDataRetryCoordinator, **kwargs) -> None:
        self.retry_coordinator = retry_coordinator
        super().__init__(*args, **kwargs)

    def analyze_all(
        self,
        now: datetime | None = None,
        *,
        create_signals: bool | None = None,
    ):
        current = _normalize_utc(now)
        self.retry_coordinator.before_attempt(current)
        try:
            results = super().analyze_all(
                now=current,
                create_signals=create_signals,
            )
        except Exception as exc:
            if is_market_data_failure(exc):
                self.retry_coordinator.record_failure(exc, current)
            raise
        self.retry_coordinator.record_success(current)
        return results


class ScheduledRetryPortfolioService:
    """Apply the same market-data backoff to LIVE portfolio calculations."""

    def __init__(self, delegate: Any, retry_coordinator: MarketDataRetryCoordinator) -> None:
        self._delegate = delegate
        self.retry_coordinator = retry_coordinator

    def run_month_end(self, *args, **kwargs):
        current = datetime.now(UTC)
        self.retry_coordinator.before_attempt(current)
        try:
            result = self._delegate.run_month_end(*args, **kwargs)
        except Exception as exc:
            if is_market_data_failure(exc):
                self.retry_coordinator.record_failure(exc, current)
            raise
        if result is not None:
            self.retry_coordinator.record_success(current)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ProviderRecoveryJHAutoService(FinalOpsProductionJHAutoService):
    """Require two independent clean safety cycles before transient auto-recovery."""

    @classmethod
    def bootstrap_repository(cls, repository: Any) -> None:
        super().bootstrap_repository(repository)
        defaults = {
            PROVIDER_TRANSIENT_QUARANTINE_KEY: "0",
            PROVIDER_CLEAN_STREAK_KEY: "0",
            PROVIDER_RECOVERY_NOTICE_KEY: "0",
            PROVIDER_RELEASE_HOLD_KEY: "0",
            MARKET_DATA_DEGRADED_KEY: "0",
            MARKET_DATA_FAILURE_COUNT_KEY: "0",
            MARKET_DATA_RETRY_NEXT_KEY: "",
            MARKET_DATA_RECOVERY_NOTICE_KEY: "0",
            MARKET_DATA_LAST_FAILURE_KEY: "",
        }
        for key, value in defaults.items():
            if repository.get_system_value(key) is None:
                repository.set_system_value(key, value)

    def quarantine_transient(self, reason: str, exc: BaseException | None = None) -> None:
        del exc
        self.repository.set_system_value(PROVIDER_TRANSIENT_QUARANTINE_KEY, "1")
        self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
        self.repository.set_system_value(PROVIDER_RECOVERY_NOTICE_KEY, "0")
        self.repository.set_system_value(PROVIDER_RELEASE_HOLD_KEY, "0")
        super().quarantine(reason)

    def note_safety_failure(self) -> None:
        if self.repository.get_system_value(PROVIDER_TRANSIENT_QUARANTINE_KEY) == "1":
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")

    def try_release_quarantine(self, *, safety_ready: bool) -> bool:
        if self.repository.get_system_value(PROVIDER_TRANSIENT_QUARANTINE_KEY) != "1":
            return super().try_release_quarantine(safety_ready=safety_ready)

        if not safety_ready:
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
            return False
        if self.repository.get_system_value(MARKET_DATA_DEGRADED_KEY) == "1":
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
            return False

        settings = self.settings()
        if (
            not settings.launch_authorized
            or settings.operator_halt_latched
            or self._portfolio_safe_mode()
            or self.repository.open_orders()
        ):
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
            return False
        if self.repository.get_system_value(AUTO_OPERATOR_HALT_LATCH_KEY) == "1":
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
            return False

        raw = self.repository.get_system_value(PROVIDER_CLEAN_STREAK_KEY) or "0"
        try:
            streak = max(0, int(raw)) + 1
        except ValueError:
            streak = 1
        self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, str(streak))
        if streak < REQUIRED_PROVIDER_CLEAN_CYCLES:
            return False

        released = super().try_release_quarantine(safety_ready=True)
        if released:
            self.repository.set_system_value(PROVIDER_TRANSIENT_QUARANTINE_KEY, "0")
            self.repository.set_system_value(PROVIDER_CLEAN_STREAK_KEY, "0")
            self.repository.set_system_value(PROVIDER_RECOVERY_NOTICE_KEY, "1")
            # Do not allow a BUY in the exact cycle that removes provider quarantine.
            # The following safety cycle must re-enter through the normal final gates.
            self.repository.set_system_value(PROVIDER_RELEASE_HOLD_KEY, "1")
        return released

    def execute_one(self, trading_service: Any, *, now: datetime | None = None):
        if self.repository.get_system_value(PROVIDER_RELEASE_HOLD_KEY) == "1":
            self.repository.set_system_value(PROVIDER_RELEASE_HOLD_KEY, "0")
            self.repository.log_event(
                "INFO",
                "PROVIDER_RECOVERY_BUY_DEFERRED_ONE_CYCLE",
                "임시격리 해제 직후 한 안전주기 동안 신규 BUY를 추가 보류했습니다",
            )
            return None
        return super().execute_one(trading_service, now=now)

    def consume_provider_recovery_notice(self) -> bool:
        if self.repository.get_system_value(PROVIDER_RECOVERY_NOTICE_KEY) != "1":
            return False
        self.repository.set_system_value(PROVIDER_RECOVERY_NOTICE_KEY, "0")
        return True


class ProviderRecoveryLiveJHAutoTelegramBotApp(LiveJHAutoTelegramBotApp):
    """Operator messaging for transient provider degradation and automatic recovery."""

    def __init__(self, *args, **kwargs) -> None:
        self._provider_notice_at: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    def _notice_due(self, key: str, cooldown_seconds: int = 1800) -> bool:
        now = time.monotonic()
        previous = self._provider_notice_at.get(key)
        if previous is not None and now - previous < cooldown_seconds:
            return False
        self._provider_notice_at[key] = now
        return True

    def _retry_coordinator(self) -> MarketDataRetryCoordinator | None:
        coordinator = getattr(self.analysis_service, "retry_coordinator", None)
        return coordinator if isinstance(coordinator, MarketDataRetryCoordinator) else None

    def _notify_runtime_error(
        self,
        event_type: str,
        title: str,
        exc: Exception,
        *,
        cooldown_seconds: int = 600,
    ) -> None:
        if isinstance(exc, MarketDataDeferredError):
            LOGGER.debug("market-data retry deferred: %s", exc)
            return

        if isinstance(exc, TransientBrokerReadError):
            if self._notice_due("broker-read-degraded", cooldown_seconds):
                self._send(
                    "🟡 <b>[토스 계좌조회 일시 장애 · 자동복구 중]</b>\n\n"
                    "• 신규 BUY : <b>임시 차단</b>\n"
                    "• 주문감시·위험축소 : 가능한 범위에서 계속\n"
                    "• 이번 조회 장애 자체로 영구 SAFE_MODE를 만들지 않습니다.\n"
                    "• 복구조건 : 계좌·원장 안전주기 <b>2회 연속 PASS</b>\n\n"
                    "시스템이 자동 재확인합니다. 실제 수량불일치·UNKNOWN 주문이 확인되면 "
                    "그때는 별도 SAFE_MODE로 전환합니다."
                )
            return

        if event_type in {"ANALYSIS_SCHEDULER_ERROR", "PORTFOLIO_SCHEDULER_ERROR"} and is_market_data_failure(exc):
            quarantine = getattr(self.auto_service, "quarantine_transient", None)
            if callable(quarantine):
                quarantine("MARKET_DATA_DEGRADED", exc)
            coordinator = self._retry_coordinator()
            if coordinator is not None and self._notice_due("market-data-degraded", 1800):
                next_retry = coordinator.next_retry_at()
                next_text = (
                    next_retry.astimezone(SEOUL_TZ).strftime("%H:%M KST")
                    if next_retry is not None
                    else "자동 계산 중"
                )
                self._send(
                    "🟡 <b>[일일 시세 일시 장애 · 자동 재시도 예약]</b>\n\n"
                    f"• 다음 재시도 : <code>{next_text}</code>\n"
                    "• 재시도 간격 : 5분 → 10분 → 15분 → 30분 → 이후 최대 60분\n"
                    "• 신규 BUY : <b>시세 정상화까지 임시 차단</b>\n"
                    "• 기존 주문감시·위험축소 : 계속\n\n"
                    "같은 장애를 30초마다 반복 호출하거나 Telegram으로 도배하지 않습니다."
                )
            return

        super()._notify_runtime_error(
            event_type,
            title,
            exc,
            cooldown_seconds=cooldown_seconds,
        )

    def notify_new_signals(self, results) -> None:
        super().notify_new_signals(results)
        coordinator = self._retry_coordinator()
        if coordinator is not None and coordinator.consume_recovery_notice():
            self._send(
                "✅ <b>[일일 시세 자동복구 완료]</b>\n\n"
                "Yahoo 일봉 조회가 다시 정상화되었습니다.\n"
                "신규 BUY는 즉시 재개하지 않고 계좌·원장 안전주기 2회 연속 확인 후 "
                "임시격리를 자동 해제합니다."
            )

    def _run_order_safety_cycle(self) -> bool:
        clean = super()._run_order_safety_cycle()
        if not clean:
            note_failure = getattr(self.auto_service, "note_safety_failure", None)
            if callable(note_failure):
                note_failure()
            return False

        consume_notice = getattr(
            self.auto_service,
            "consume_provider_recovery_notice",
            None,
        )
        if callable(consume_notice) and consume_notice():
            self._send(
                "✅ <b>[JH AUTO 임시격리 자동해제]</b>\n\n"
                "브로커·원장·주문 안전조건을 2회 연속 확인했습니다.\n"
                "해제와 같은 주기에는 BUY를 보내지 않으며, 다음 독립 안전주기부터 "
                "정상 최종검증을 다시 통과한 주문만 실행할 수 있습니다."
            )
        return True
