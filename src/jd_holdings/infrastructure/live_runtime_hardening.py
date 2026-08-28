from __future__ import annotations

import html
import time
from datetime import UTC, datetime

from jd_holdings.application.live_runtime_services import (
    LiveInitialOnboardingPortfolioService,
)

from .market_clock import is_toss_order_maintenance_window
from .operational_safety_telegram import OperationalSafetyTelegramBotApp
from .safe_mode_diagnostics import broker_diagnostic, operator_action
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

    def __init__(self, *args, **kwargs) -> None:
        self._toss_diagnostic_notice_at: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    def _notify_runtime_error(
        self,
        event_type: str,
        title: str,
        exc: Exception,
        *,
        cooldown_seconds: int = 600,
    ) -> None:
        super()._notify_runtime_error(
            event_type,
            title,
            exc,
            cooldown_seconds=cooldown_seconds,
        )
        diagnostic = broker_diagnostic(exc)
        if diagnostic is None:
            return
        fingerprint = ":".join(
            (
                event_type,
                str(diagnostic.http_status or ""),
                str(diagnostic.error_code or ""),
                str(diagnostic.request_id or ""),
            )
        )
        now = time.monotonic()
        last = self._toss_diagnostic_notice_at.get(fingerprint)
        if last is not None and now - last < cooldown_seconds:
            return
        self._toss_diagnostic_notice_at[fingerprint] = now
        http_status = diagnostic.http_status if diagnostic.http_status is not None else "없음"
        lines = [
            "🏦 <b>[토스증권 API 오류 상세]</b>",
            "",
            f"• 발생 작업 : <b>{html.escape(title)}</b>",
            f"• HTTP 상태 : <code>{http_status}</code>",
            f"• 토스 오류코드 : <code>{html.escape(diagnostic.error_code or '없음')}</code>",
            f"• 재시도 가능 오류 : <b>{'예' if diagnostic.retryable else '아니오'}</b>",
        ]
        if diagnostic.request_id:
            lines.append(
                f"• 요청 추적ID : <code>{html.escape(diagnostic.request_id)}</code>"
            )
        lines.extend(
            [
                f"• 사유 : <code>{html.escape(diagnostic.summary)}</code>",
                "",
                "🛡️ <b>운영자 조치</b>",
                "1. 토스 앱에서 보유수량·미체결·최근 체결을 확인하세요.",
                "2. 주문 결과가 불명확하면 임의 재주문하지 마세요.",
                "3. <code>/errors</code> → <code>/order</code> → <code>/account</code> 순으로 확인하세요.",
                "4. SAFE_MODE라면 원인과 계좌·원장 정합성을 확인한 뒤에만 "
                "<code>/resume</code> 하세요.",
            ]
        )
        try:
            self._send("\n".join(lines))
        except Exception as notify_exc:
            self.logger.warning("Toss API diagnostic Telegram send failed: %s", notify_exc)

    def _probe_open_order_lookup_failure(self, symbol: str) -> None:
        """Re-run only a failed OPEN-order read so Toss metadata reaches Telegram.

        Reconciliation intentionally returns safe issue codes instead of raising for a
        per-symbol OPEN-order lookup failure. That fail-closed contract must stay as-is,
        but it used to discard the TossApiError cause before the Telegram diagnostic
        layer could inspect HTTP/code/request-id metadata. Probe once per alert window;
        never place/cancel an order and never auto-resume SAFE_MODE.
        """
        try:
            self.trading_service.broker.list_orders(
                status="OPEN",
                symbol=symbol,
                limit=100,
            )
        except Exception as exc:
            self._notify_runtime_error(
                "RECONCILIATION_OPEN_ORDER_LOOKUP_ERROR",
                f"{symbol} 미체결 주문 조회 오류",
                exc,
            )
        else:
            self._send(
                f"ℹ️ <b>[{html.escape(symbol)} 토스 API 재확인 성공]</b>\n\n"
                "직전 미체결 주문 조회는 실패했지만 즉시 재확인에서는 정상 응답했습니다.\n"
                "일시적 API 오류일 수 있으나 SAFE_MODE는 자동 해제하지 않습니다.\n"
                "<code>/errors</code>·<code>/order</code>·<code>/account</code> 확인 후 "
                "정합성이 맞을 때만 <code>/resume</code> 하세요."
            )

    def _send_reconciliation_alert(
        self,
        symbol: str,
        issues: list[str],
        *,
        cooldown_seconds: int = 600,
    ) -> None:
        fingerprint = f"{symbol}:{'|'.join(sorted(issues))}"
        now = time.monotonic()
        last = self._reconciliation_notice_at.get(fingerprint)
        if last is not None and now - last < cooldown_seconds:
            return
        self._reconciliation_notice_at[fingerprint] = now
        issue_lines = "\n".join(
            f"• <code>{html.escape(issue)}</code>" for issue in issues
        )
        actions = []
        for issue in issues:
            action = operator_action(issue)
            if action not in actions:
                actions.append(action)
        action_lines = "\n".join(
            f"{index}. {html.escape(action)}"
            for index, action in enumerate(actions, start=1)
        )
        check_step = len(actions) + 1
        resume_step = len(actions) + 2
        self._send(
            f"🚨 <b>[{html.escape(symbol)} 안전정지(SAFE_MODE)]</b>\n\n"
            "<b>발생 원인</b>\n"
            f"{issue_lines}\n\n"
            "<b>현재 상태</b>\n"
            "• 신규 BUY : ⛔ 차단\n"
            "• 위험축소 SELL·주문감시 : 가능한 범위에서 계속\n"
            "• SAFE_MODE : 원인 확인 전 임의 해제 금지\n\n"
            "<b>지금 할 일</b>\n"
            f"{action_lines}\n"
            f"{check_step}. <code>/errors</code>, <code>/order</code>, "
            "<code>/account</code>를 확인하세요.\n"
            f"{resume_step}. 문제가 해결되고 Toss/DB가 일치한 뒤에만 "
            "<code>/resume</code> 2단계 검증을 진행하세요.\n\n"
            "같은 원인은 10분 동안 반복 알림하지 않습니다."
        )
        if any(issue.startswith("BROKER_OPEN_ORDER_LOOKUP_FAILED:") for issue in issues):
            self._probe_open_order_lookup_failure(symbol)

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