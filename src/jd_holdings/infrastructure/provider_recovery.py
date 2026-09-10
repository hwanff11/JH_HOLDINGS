from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .toss_client import TossApiError

TRANSIENT_RECON_REASON_KEY = "reconciliation_transient_error"
TRANSIENT_RECON_AT_KEY = "reconciliation_transient_at"
AUTO_TRANSIENT_RECOVERY_PENDING_KEY = "jh_auto_transient_recovery_pending"
AUTO_TRANSIENT_CLEAN_STREAK_KEY = "jh_auto_transient_clean_streak"
TRANSIENT_RECOVERY_REQUIRED_CLEAN_CYCLES = 2
ANALYSIS_RETRY_DELAYS_SECONDS = (300, 600, 900, 1800, 3600)
RECOVERABLE_AUTH_CODES = {"invalid-token", "expired-token", "token-revoked"}


class TransientReconciliationError(RuntimeError):
    """A read-only broker outage that blocks BUY but is not state corruption."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def retryable_toss_read_error(exc: BaseException) -> bool:
    """Return True only for Toss read failures that are safe to retry later.

    Writes are deliberately outside this classifier. The caller uses it only around
    holdings/open-order GET preflights, after the normal bounded read retries have
    already been exhausted.
    """
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TossApiError):
            if current.retryable:
                return True
            if current.status_code == 401 and current.code in RECOVERABLE_AUTH_CODES:
                return True
            status = current.status_code
            return bool(
                status is not None
                and 200 <= status < 300
                and "응답" in str(current)
            )
        current = current.__cause__ or current.__context__
    return False


def arm_transient_recovery(repository) -> None:
    repository.set_system_value(AUTO_TRANSIENT_RECOVERY_PENDING_KEY, "1")
    repository.set_system_value(AUTO_TRANSIENT_CLEAN_STREAK_KEY, "0")


def advance_transient_recovery(repository) -> tuple[bool, int]:
    """Require two independent clean reconciliation cycles before AUTO can resume."""
    if repository.get_system_value(AUTO_TRANSIENT_RECOVERY_PENDING_KEY) != "1":
        return True, 0
    try:
        streak = int(repository.get_system_value(AUTO_TRANSIENT_CLEAN_STREAK_KEY) or "0")
    except ValueError:
        streak = 0
    streak += 1
    if streak < TRANSIENT_RECOVERY_REQUIRED_CLEAN_CYCLES:
        repository.set_system_value(AUTO_TRANSIENT_CLEAN_STREAK_KEY, str(streak))
        return False, streak
    repository.set_system_value(AUTO_TRANSIENT_RECOVERY_PENDING_KEY, "0")
    repository.set_system_value(AUTO_TRANSIENT_CLEAN_STREAK_KEY, "0")
    return True, streak


@dataclass
class DailyAnalysisRetryGate:
    """Bound daily provider retries without changing the configured 07:00 schedule."""

    trade_date: date | None = None
    failures: int = 0
    next_attempt_monotonic: float = 0.0

    def _ensure_trade_date(self, trade_date: date) -> None:
        if self.trade_date == trade_date:
            return
        self.trade_date = trade_date
        self.failures = 0
        self.next_attempt_monotonic = 0.0

    def should_attempt(self, trade_date: date, now_monotonic: float) -> bool:
        self._ensure_trade_date(trade_date)
        return now_monotonic >= self.next_attempt_monotonic

    def record_failure(self, trade_date: date, now_monotonic: float) -> tuple[int, int]:
        self._ensure_trade_date(trade_date)
        delay_index = min(self.failures, len(ANALYSIS_RETRY_DELAYS_SECONDS) - 1)
        delay = ANALYSIS_RETRY_DELAYS_SECONDS[delay_index]
        self.failures += 1
        self.next_attempt_monotonic = now_monotonic + delay
        return self.failures, delay

    def record_success(self, trade_date: date) -> int:
        self._ensure_trade_date(trade_date)
        recovered_failures = self.failures
        self.failures = 0
        self.next_attempt_monotonic = 0.0
        return recovered_failures
