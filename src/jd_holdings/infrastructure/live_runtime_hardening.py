from __future__ import annotations

import html
import time
from datetime import UTC, datetime

from jd_holdings.application.live_runtime_services import (
    LiveInitialOnboardingPortfolioService,
)
from jd_holdings.core.indicators import MarketDataError

from .market_clock import is_toss_order_maintenance_window
from .operational_safety_telegram import OperationalSafetyTelegramBotApp
from .provider_recovery import (
    DAILY_PROVIDER_RECOVERY_PENDING_KEY,
    DailyAnalysisRetryGate,
    TransientReconciliationError,
    advance_transient_recovery,
    arm_transient_recovery,
)
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
    """Run settlement/reconciliation first and self-heal read-only provider outages."""

    def __init__(self, *args, **kwargs) -> None:
        self._toss_diagnostic_notice_at: dict[str, float] = {}
        self._transient_reconciliation_notice_at: dict[str, float] = {}
        self._daily_analysis_retry = DailyAnalysisRetryGate()
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

        Structural reconciliation failures stay sticky SAFE_MODE. Transient Toss read
        failures are intercepted by the live reconciliation boundary before reaching
        this path, so a successful probe here never auto-resumes SAFE_MODE.
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
                "구조적 SAFE_MODE는 자동 해제하지 않습니다.\n"
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

    def _quarantine_transient_reconciliation(
        self,
        exc: TransientReconciliationError,
    ) -> None:
        arm_transient_recovery(self.repository)
        auto_service = getattr(self, "auto_service", None)
        if auto_service is None:
            return
        settings = auto_service.settings()
        if (
            settings.launch_authorized
            and not settings.operator_halt_latched
            and not settings.quarantine
        ):
            auto_service.quarantine(f"TRANSIENT_PROVIDER:{exc.reason}")

    def _notify_transient_reconciliation(
        self,
        exc: TransientReconciliationError,
        *,
        cooldown_seconds: int = 1800,
    ) -> None:
        fingerprint = exc.reason
        now = time.monotonic()
        last = self._transient_reconciliation_notice_at.get(fingerprint)
        if last is not None and now - last < cooldown_seconds:
            return
        self._transient_reconciliation_notice_at[fingerprint] = now
        self._send(
            "🟡 <b>[토스 읽기 일시 장애 · 자동복구 중]</b>\n\n"
            f"• 원인 : <code>{html.escape(exc.reason)}</code>\n"
            "• 신규 BUY : <b>임시 차단</b>\n"
            "• 주문감시·위험축소 : 가능한 범위에서 계속\n"
            "• 자동조치 : 토스 읽기를 계속 재확인하고, 정상화 후 계좌·원장 "
            "정합성을 2회 연속 확인합니다.\n\n"
            "실제 수량/주문 불일치가 발견되지 않는 한 <code>/resume</code>을 "
            "직접 누를 필요가 없습니다."
        )

    def _daily_analysis_blocks_auto(self) -> bool:
        """Never execute yesterday's pending BUY before today's due analysis succeeds."""
        now_kst = datetime.now(UTC).astimezone(SEOUL_TZ)
        if not _daily_analysis_is_due(
            now_kst,
            self.config.scheduler.daily_analysis_time_kst,
        ):
            return False
        try:
            completed = self.market_clock.latest_completed_session(
                delay_minutes=self.config.scheduler.signal_delay_minutes
            )
        except Exception:
            return True
        return self.repository.get_system_value("last_analysis_trade_date") != (
            completed.isoformat()
        )

    def _record_daily_job_failure(
        self,
        completed,
        title: str,
        exc: Exception,
        *,
        provider_failure: bool,
    ) -> None:
        failures, delay = self._daily_analysis_retry.record_failure(
            completed,
            time.monotonic(),
        )
        self.repository.set_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY, "1")
        auto_service = getattr(self, "auto_service", None)
        if auto_service is not None:
            settings = auto_service.settings()
            if (
                settings.launch_authorized
                and not settings.operator_halt_latched
                and not settings.quarantine
            ):
                auto_service.quarantine(
                    f"DAILY_PROVIDER_RECOVERY:{type(exc).__name__}"
                )
        self.logger.warning(
            "%s 실패; %d초 후 자동 재시도합니다 (%d회 실패): %s",
            title,
            delay,
            failures,
            exc,
        )
        if provider_failure and failures == 1:
            self._send(
                "🟡 <b>[일일 시세 조회 지연 · 자동재시도]</b>\n\n"
                f"• 작업 : <b>{html.escape(title)}</b>\n"
                f"• 거래일 : <code>{completed.isoformat()}</code>\n"
                f"• 다음 재시도 : <b>{delay // 60}분 후</b>\n"
                "• 신규 BUY : <b>시세 정상화까지 임시 차단</b>\n\n"
                "5→10→15→30→60분 간격으로 자동 재시도하며 같은 장애를 "
                "반복 알림하지 않습니다. 정상화되면 복구 완료를 한 번 알려드립니다."
            )
        elif not provider_failure:
            self._notify_runtime_error(
                "DAILY_JOB_ERROR",
                title,
                exc,
                cooldown_seconds=max(600, delay),
            )

    def _record_daily_job_success(self, completed) -> None:
        was_pending = (
            self.repository.get_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY) == "1"
        )
        recovered_failures = self._daily_analysis_retry.record_success(completed)
        if not was_pending and recovered_failures == 0:
            return
        self.repository.set_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY, "0")
        self._send(
            "✅ <b>[일일 시세 자동복구 완료]</b>\n\n"
            f"• 거래일 : <code>{completed.isoformat()}</code>\n"
            "• 최신 일봉 조회와 일일 분석을 정상 완료했습니다.\n"
            "• 다음 신규 BUY는 다음 독립 안전주기에서 계좌·원장·주문 상태를 "
            "다시 확인한 뒤에만 재개됩니다."
        )

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
        except TransientReconciliationError as exc:
            self._quarantine_transient_reconciliation(exc)
            self._notify_transient_reconciliation(exc)
        except Exception as exc:
            self._notify_runtime_error(
                "RECONCILIATION_ERROR",
                "계좌 정합성 점검 오류",
                exc,
            )
        else:
            if not mismatches:
                self._reconciliation_notice_at.clear()
                recovery_ready, clean_streak = advance_transient_recovery(
                    self.repository
                )
                if recovery_ready:
                    reconciliation_clean = True
                    if clean_streak:
                        self._transient_reconciliation_notice_at.clear()
                        self._send(
                            "✅ <b>[토스 읽기 자동복구 검증 완료]</b>\n\n"
                            "계좌·원장·미체결 주문을 독립 안전주기에서 2회 연속 "
                            "정상 확인했습니다. 다른 안전조건이 정상이라면 임시격리를 "
                            "자동 해제할 수 있습니다."
                        )
                else:
                    self.logger.info(
                        "토스 읽기 복구 후 정합성 연속 검증 %d/2 완료",
                        clean_streak,
                    )
            for symbol, issues in mismatches.items():
                self._send_reconciliation_alert(symbol, issues)

        if (
            reconciliation_clean
            and self.repository.get_system_value(DAILY_PROVIDER_RECOVERY_PENDING_KEY)
            == "1"
        ):
            reconciliation_clean = False
        if reconciliation_clean and self._daily_analysis_blocks_auto():
            reconciliation_clean = False

        self._last_monitor = time.monotonic()
        return monitor_clean and reconciliation_clean

    def _scheduler_loop(self) -> None:
        """Fail closed: settle orders, reconcile, then allow allocation decisions."""
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            heartbeat = getattr(self, "_record_scheduler_heartbeat", None)
            if heartbeat is not None:
                heartbeat()
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

            provider_attempted = False
            provider_failed = False
            provider_retry_due = bool(
                completed is not None
                and self._daily_analysis_retry.should_attempt(
                    completed,
                    time.monotonic(),
                )
            )

            if (
                self.portfolio_service is not None
                and completed is not None
                and not maintenance
                and safety_ready
                and provider_retry_due
            ):
                provider_attempted = True
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
                except MarketDataError as exc:
                    provider_failed = True
                    self._record_daily_job_failure(
                        completed,
                        "V3.2.2 배분 점검",
                        exc,
                        provider_failure=True,
                    )
                except Exception as exc:
                    provider_failed = True
                    self._record_daily_job_failure(
                        completed,
                        "V3.2.2 배분 점검 오류",
                        exc,
                        provider_failure=False,
                    )

            if completed is not None and provider_retry_due and not provider_failed:
                try:
                    last_analysis = self.repository.get_system_value(
                        "last_analysis_trade_date"
                    )
                    if last_analysis != completed.isoformat():
                        provider_attempted = True
                        results = self.analysis_service.analyze_all()
                        self.notify_new_signals(results)
                except MarketDataError as exc:
                    provider_failed = True
                    self._record_daily_job_failure(
                        completed,
                        "일일 전략 분석",
                        exc,
                        provider_failure=True,
                    )
                except Exception as exc:
                    provider_failed = True
                    self._record_daily_job_failure(
                        completed,
                        "일일 전략 분석 오류",
                        exc,
                        provider_failure=False,
                    )

            if (
                completed is not None
                and provider_attempted
                and not provider_failed
                and provider_retry_due
            ):
                self._record_daily_job_success(completed)

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
