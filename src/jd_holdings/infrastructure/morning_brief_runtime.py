from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal

from jd_holdings.application.managed_account import (
    AUTO_EFFECTIVE_PRINCIPAL_KEY,
    AUTO_ENABLED_KEY,
    HIGH_WATER_KEY,
)
from jd_holdings.core.indicators import MarketDataError

from .jh_auto_live_display import LiveJHAutoTelegramBotApp
from .operational_safety_telegram import (
    DAILY_BRIEF_CUTOFF_NOTICE_DATE_KEY,
    _as_seoul,
    _daily_brief_window_bounds,
)
from .provider_recovery import DAILY_PROVIDER_RECOVERY_PENDING_KEY, DailyAnalysisRetryGate
from .telegram_bot_runtime import _format_daily_portfolio_brief

LOGGER = logging.getLogger(__name__)
DAILY_BRIEF_SENT_KST_DATE_KEY = "daily_brief_sent_kst_date"
DAILY_BRIEF_SENT_TRADE_DATE_KEY = "daily_brief_sent_trade_date"


class MorningBriefLiveJHAutoTelegramBotApp(LiveJHAutoTelegramBotApp):
    """Deliver the 07:00 brief from a read-only completed-session strategy preview.

    The production allocator intentionally mutates HWM/targets and may cancel or create
    orders, so it remains restricted to the US regular session.  The morning report
    must not loosen that safety boundary.  This companion loop computes only the
    completed-session target weights and presentation metrics, waits until the normal
    daily analysis has succeeded, and records only delivery metadata.
    """

    def __init__(self, *args, **kwargs) -> None:
        self._morning_brief_retry = DailyAnalysisRetryGate()
        super().__init__(*args, **kwargs)

    def _preview_capital_event(
        self,
        *,
        marked_equity: Decimal,
    ) -> str | None:
        policy = self.portfolio_service.policy
        if self.repository.get_system_value(AUTO_ENABLED_KEY) == "1":
            raw_initial = self.repository.get_system_value(AUTO_EFFECTIVE_PRINCIPAL_KEY)
            initial = Decimal(str(raw_initial or "0"))
        else:
            initial = policy.initial_capital
        if not initial.is_finite() or initial <= 0:
            return None

        raw_previous = self.repository.get_system_value(HIGH_WATER_KEY)
        previous = Decimal(str(raw_previous)) if raw_previous not in (None, "") else initial
        high_water = max(previous, marked_equity, initial)
        gain = max(Decimal("0"), high_water - initial)
        risk_budget = max(
            Decimal("0"),
            min(
                initial + policy.hwm_reinvestment_fraction * gain,
                marked_equity,
            ),
        )
        return (
            f"HWM USD {high_water:,.2f} · 위험예산 USD {risk_budget:,.2f} · "
            f"완결종가 평가액 USD {marked_equity:,.2f}"
        )

    def _build_read_only_morning_preview(self, completed: date) -> tuple[str, tuple[str, ...]]:
        service = self.portfolio_service
        raw, target = service._calculate_target(completed)
        completed_ts = next(
            (timestamp for timestamp in target.index if timestamp.date() == completed),
            None,
        )
        if completed_ts is None:
            raise ValueError(
                f"V3.2.2 target에 완결 거래일이 없습니다: {completed.isoformat()}"
            )

        target_row = target.loc[completed_ts]
        marked_equity = service._completed_marked_equity(raw, completed_ts)
        new_weights = {
            symbol: Decimal(str(float(target_row[symbol])))
            for symbol in ("QQQ", "TQQQ", "SOXL")
        }
        events = [
            (
                "V3.2.2 배분 "
                f"레버리지 {float(target_row['leverage']):.2f}x · "
                f"RS6M {'ON' if bool(target_row['semiconductor_active']) else 'OFF'} · "
                f"JDSS TQQQ {'ON' if bool(target_row['jdss_tqqq_active']) else 'OFF'} / "
                f"SOXL {'ON' if bool(target_row['jdss_soxl_active']) else 'OFF'} · "
                f"목표 QQQ {new_weights['QQQ'] * 100:.2f}% / "
                f"TQQQ {new_weights['TQQQ'] * 100:.2f}% / "
                f"SOXL {new_weights['SOXL'] * 100:.2f}%"
            )
        ]
        capital_event = self._preview_capital_event(marked_equity=marked_equity)
        if capital_event is not None:
            events.append(capital_event)
        return completed.isoformat(), tuple(events)

    @staticmethod
    def _mark_brief_as_preview(text: str) -> str:
        text = text.replace(
            "기준일 : <code>",
            "※ 07:00 읽기전용 전략 계산 · 주문/원장 변경 없음\n"
            "기준일 : <code>",
            1,
        )
        text = text.replace(
            "현재 목표비중과 운용상태를 유지합니다.\n"
            "<b>지금 승인할 신규 매수 주문은 없습니다.</b>",
            "직전 완결봉 기준 <b>전략 목표비중을 계산했습니다.</b>\n"
            "실제 주문 필요 여부는 미국 정규장 안전주기에서 계좌·원장을 "
            "다시 확인한 뒤 확정합니다.",
            1,
        )
        return text

    def _send_cutoff_notice(self, now: datetime, *, completed: date | None) -> bool:
        today = _as_seoul(now).date().isoformat()
        if self.repository.get_system_value(DAILY_BRIEF_CUTOFF_NOTICE_DATE_KEY) == today:
            return False
        if self.repository.get_system_value(DAILY_BRIEF_SENT_KST_DATE_KEY) == today:
            return False

        pending = (
            self.repository.get_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY) == "1"
        )
        last_analysis = self.repository.get_system_value("last_analysis_trade_date")
        expected = completed.isoformat() if completed is not None else None
        if pending:
            reason = "최신 일봉/전략 데이터 복구가 아직 완료되지 않았습니다."
        elif expected is not None and last_analysis != expected:
            reason = "오늘 일일 전략 분석이 아직 완결 거래일까지 끝나지 않았습니다."
        else:
            reason = "전략 분석은 확인됐지만 정규 아침 브리핑 전달이 완료되지 않았습니다."

        self._send(
            "🟡 <b>[오늘 아침 브리핑 미확정]</b>\n\n"
            "09:00 KST까지 정규 아침 브리핑이 전달되지 않았습니다.\n"
            f"• 상태 : {reason}\n"
            "• 전체 브리핑 : 09:00 이후 지연전송하지 않음\n"
            "• 거래 안전장치 : 기존 BUY 차단·SAFE_MODE·/halt 상태를 그대로 존중\n\n"
            "백그라운드 점검은 계속하며 실제 주문·체결·오류·복구 알림은 정상 전송합니다."
        )
        self.repository.set_system_value(DAILY_BRIEF_CUTOFF_NOTICE_DATE_KEY, today)
        return True

    def _run_morning_brief_cycle(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        now_kst = _as_seoul(current)
        scheduled = self._daily_brief_scheduled_time()
        start, cutoff = _daily_brief_window_bounds(now_kst, scheduled)
        today = now_kst.date().isoformat()

        if now_kst < start:
            return False
        if self.repository.get_system_value(DAILY_BRIEF_SENT_KST_DATE_KEY) == today:
            return False

        completed: date | None = None
        try:
            completed = self.market_clock.latest_completed_session(
                current,
                delay_minutes=self.config.scheduler.signal_delay_minutes,
            )
        except Exception as exc:
            LOGGER.warning("아침 브리핑 거래일 계산 대기: %s", type(exc).__name__)

        if now_kst >= cutoff:
            return self._send_cutoff_notice(current, completed=completed)
        if completed is None:
            return False

        # The canonical daily analysis is the readiness barrier.  This avoids racing
        # its yfinance refresh/cache writes and guarantees the preview is never used as
        # a substitute for the strategy's normal fail-closed analysis path.
        if self.repository.get_system_value("last_analysis_trade_date") != completed.isoformat():
            return False

        monotonic_now = time.monotonic()
        if not self._morning_brief_retry.should_attempt(completed, monotonic_now):
            return False

        try:
            trade_date, events = self._build_read_only_morning_preview(completed)
            brief, _remaining = _format_daily_portfolio_brief(
                trade_date,
                events,
                has_buy_signals=False,
            )
            if brief is None:
                raise RuntimeError("아침 브리핑 포맷에 필요한 배분 이벤트가 없습니다")
            self._send(self._mark_brief_as_preview(brief))
        except MarketDataError as exc:
            failures, delay = self._morning_brief_retry.record_failure(
                completed,
                monotonic_now,
            )
            LOGGER.warning(
                "아침 브리핑 읽기전용 계산 지연; %d초 후 재시도 (%d회): %s",
                delay,
                failures,
                exc,
            )
            if failures == 1:
                self._send(
                    "🟡 <b>[아침 브리핑 생성 지연 · 자동재시도]</b>\n\n"
                    f"• 기준 거래일 : <code>{completed.isoformat()}</code>\n"
                    f"• 다음 재시도 : <b>{delay // 60}분 후</b>\n"
                    "• 실제 거래 : 기존 안전주기와 별개로 계속 보호됨\n\n"
                    "브리핑용 읽기전용 계산만 재시도하며 주문이나 원장은 변경하지 않습니다."
                )
            return False
        except Exception as exc:
            failures, delay = self._morning_brief_retry.record_failure(
                completed,
                monotonic_now,
            )
            LOGGER.error(
                "아침 브리핑 생성/전송 오류; %d초 후 재시도 (%d회): %s",
                delay,
                failures,
                type(exc).__name__,
            )
            self.repository.set_system_value(
                "jh_auto_notification_failed_at",
                datetime.now(UTC).isoformat(),
            )
            self.repository.set_system_value(
                "jh_auto_notification_error",
                type(exc).__name__,
            )
            return False

        self._morning_brief_retry.record_success(completed)
        self.repository.set_system_value(DAILY_BRIEF_SENT_KST_DATE_KEY, today)
        self.repository.set_system_value(DAILY_BRIEF_SENT_TRADE_DATE_KEY, completed.isoformat())
        self.repository.set_system_value("jh_auto_notification_failed_at", "")
        self.repository.set_system_value("jh_auto_notification_error", "")
        LOGGER.info("정규 아침 브리핑 전송 완료: %s", completed.isoformat())
        return True

    def _morning_brief_loop(self) -> None:
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            try:
                self._run_morning_brief_cycle()
            except Exception as exc:  # pragma: no cover - process boundary
                LOGGER.error("아침 브리핑 독립 루프 예외: %s", type(exc).__name__)

    def run(self) -> None:
        threading.Thread(target=self._morning_brief_loop, daemon=True).start()
        super().run()
