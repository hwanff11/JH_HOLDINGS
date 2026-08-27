from __future__ import annotations

import html
import time
from datetime import UTC, datetime

from jd_holdings.application.live_runtime_services import (
    LiveInitialOnboardingPortfolioService,
)

from .market_clock import is_toss_order_maintenance_window
from .operational_safety_telegram import OperationalSafetyTelegramBotApp
from .telegram_bot import SEOUL_TZ, _daily_analysis_is_due, _format_idle_cash_event
from .telegram_bot_runtime import _format_daily_portfolio_brief


def _live_order_monitor_interval(config) -> int:
    """Use the faster safe cadence in live without changing strategy.yaml."""
    return min(
        config.scheduler.poll_interval_seconds,
        config.scheduler.order_monitor_interval_seconds,
    )


class HardenedLiveInitialOnboardingPortfolioService(
    LiveInitialOnboardingPortfolioService
):
    """Block every live allocation evaluation during Toss order maintenance."""

    def run_allocation(self, now: datetime | None = None):
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if is_toss_order_maintenance_window(current):
            return None
        return super().run_allocation(current)


class HardenedOperationalSafetyTelegramBotApp(OperationalSafetyTelegramBotApp):
    """Run order settlement before reconciliation and portfolio decisions in live mode."""

    def _run_order_safety_cycle(self) -> bool:
        monitor_clean = True
        try:
            for event in self.order_monitor.run_once():
                self._send(f"ℹ️ {html.escape(event)}")
        except Exception as exc:
            monitor_clean = False
            self._notify_runtime_error(
                "ORDER_MONITOR_ERROR",
                "주문 모니터 오류",
                exc,
            )

        if self.idle_cash_manager is not None:
            try:
                for event in self.idle_cash_manager.refresh_orders():
                    self._send(
                        _format_idle_cash_event(
                            event,
                            self.settings.trading_mode,
                        )
                    )
                for quote in self.trading_service.resume_cash_releases():
                    self._send(
                        f"💵 <b>[{quote.symbol} SGOV 현금화 완료]</b>\n"
                        "최신 주문조건을 다시 확인했습니다."
                    )
                    self._send_final_quote(quote)
            except Exception as exc:
                monitor_clean = False
                self._notify_runtime_error(
                    "IDLE_CASH_MONITOR_ERROR",
                    "SGOV 주문 점검 오류",
                    exc,
                )

        reconciliation_clean = False
        try:
            mismatches = self.reconciliation_service.run()
            reconciliation_clean = not mismatches
            if not mismatches:
                self._reconciliation_notice_at.clear()
            for symbol, issues in mismatches.items():
                self._send_reconciliation_alert(symbol, issues)
        except Exception as exc:
            self._notify_runtime_error(
                "RECONCILIATION_ERROR",
                "계좌 정합성 점검 오류",
                exc,
            )
        self._last_monitor = time.monotonic()
        return monitor_clean and reconciliation_clean

    def _scheduler_loop(self) -> None:
        """Fail closed: settle orders, reconcile, then allow allocation decisions."""
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            now_utc = datetime.now(UTC)
            now_kst = now_utc.astimezone(SEOUL_TZ)
            maintenance = is_toss_order_maintenance_window(now_utc)

            monitor_due = (
                time.monotonic() - self._last_monitor
                >= _live_order_monitor_interval(self.config)
            )
            safety_ready = False
            if monitor_due and not maintenance:
                safety_ready = self._run_order_safety_cycle()

            completed = None
            daily_due = _daily_analysis_is_due(
                now_kst,
                self.config.scheduler.daily_analysis_time_kst,
            )
            if daily_due:
                try:
                    completed = self.market_clock.latest_completed_session(
                        delay_minutes=self.config.scheduler.signal_delay_minutes
                    )
                except Exception as exc:
                    self._notify_runtime_error(
                        "MARKET_CLOCK_ERROR",
                        "거래일 계산 오류",
                        exc,
                    )

            if (
                self.portfolio_service is not None
                and completed is not None
                and not maintenance
                and safety_ready
            ):
                try:
                    portfolio_run = self.portfolio_service.run_month_end()
                    if portfolio_run is not None:
                        brief, remaining_events = _format_daily_portfolio_brief(
                            portfolio_run.trade_date,
                            portfolio_run.events,
                            has_buy_signals=bool(portfolio_run.signals),
                        )
                        if brief is not None:
                            self._send(brief)
                        for event in remaining_events:
                            if portfolio_run.signals and "매수 승인 대기" in event:
                                continue
                            self._send(f"📊 {html.escape(event)}")
                        self.notify_portfolio_buy_batch_ready(portfolio_run.signals)
                except Exception as exc:
                    self._notify_runtime_error(
                        "PORTFOLIO_SCHEDULER_ERROR",
                        "V3.2.2 배분 점검 오류",
                        exc,
                    )

            if completed is not None:
                try:
                    last_analysis = self.repository.get_system_value(
                        "last_analysis_trade_date"
                    )
                    if last_analysis != completed.isoformat():
                        results = self.analysis_service.analyze_all()
                        self.notify_new_signals(results)
                except Exception as exc:
                    self._notify_runtime_error(
                        "ANALYSIS_SCHEDULER_ERROR",
                        "일일 전략 분석 오류",
                        exc,
                    )

            cash_due = (
                not maintenance
                and self.idle_cash_manager is not None
                and time.monotonic() - self._last_idle_cash_sweep
                >= self.config.idle_cash.sweep_interval_seconds
            )
            if cash_due:
                try:
                    for event in self.idle_cash_manager.run_once():
                        self._send(
                            _format_idle_cash_event(
                                event,
                                self.settings.trading_mode,
                            )
                        )
                except Exception as exc:
                    self._notify_runtime_error(
                        "IDLE_CASH_SWEEP_ERROR",
                        "SGOV 유휴자금 운용 오류",
                        exc,
                    )
                self._last_idle_cash_sweep = time.monotonic()

            try:
                self.repository.expire_stale_signals()
            except Exception as exc:
                self._notify_runtime_error(
                    "SIGNAL_EXPIRY_ERROR",
                    "신호 만료 처리 오류",
                    exc,
                )
