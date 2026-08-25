from __future__ import annotations

import html
import logging
import time
from datetime import datetime

from .telegram_bot import (
    SEOUL_TZ,
    _daily_analysis_is_due,
    _format_idle_cash_event,
    _is_toss_order_maintenance_window,
)
from .telegram_bot_v322 import V322TelegramBotApp

LOGGER = logging.getLogger(__name__)


def _operator_text(text: str) -> str:
    """Make execution and event messages actionable without changing strategy math."""
    replacements = (
        ("SCHEDULER_ERROR", "자동 점검 오류 · 다음 주기에 재시도"),
        ("RECONCILIATION_FAILED", "계좌·원장 불일치 · 신규매수 차단"),
        ("SGOV_RECONCILIATION_FAILED", "SGOV 원장 불일치 · 신규매수 차단"),
        ("CORE_SELL_INCOMPLETE", "코어 위험축소 미완료 · SAFE_MODE"),
        ("CORE_ORDER_SUBMISSION_UNKNOWN", "코어 주문 결과 불명 · SAFE_MODE"),
        ("ORDER_SUBMISSION_UNKNOWN", "구버전 direct 주문 결과 불명 · SAFE_MODE"),
        (
            "• 부분체결·TP 주문 복구·재시작 정합성 검증은 계속 동작합니다.",
            "• 코어 부분체결·TP 주문 복구·재시작 정합성 검증은 계속 동작합니다.\n"
            "• 코어 위험축소가 불완전하면 SAFE_MODE에서 신규매수를 차단합니다.",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)

    if "처리 상태</b> : <code>UNKNOWN</code>" in text:
        text = text.replace(
            "✅ <b>[모의주문 전송 완료]</b> ✨",
            "🚨 <b>[모의주문 결과 확인 필요]</b>",
        )
        text += (
            "\n\n🛡️ 주문 결과를 성공으로 추정하지 않습니다. "
            "<code>/errors</code>와 SAFE_MODE를 확인하세요."
        )
    elif any(
        f"처리 상태</b> : <code>{status}</code>" in text
        for status in ("REJECTED", "CANCELED", "REPLACED")
    ):
        text = text.replace(
            "✅ <b>[모의주문 전송 완료]</b> ✨",
            "⚠️ <b>[모의주문 미완료]</b>",
        )
        text += (
            "\n\n💡 코어 매수라면 유효시간 안에서 "
            "<code>/signal</code>로 재승인 여부를 확인하세요."
        )
    elif "처리 상태</b> : <code>PARTIAL_FILLED</code>" in text:
        text = text.replace(
            "✅ <b>[모의주문 전송 완료]</b> ✨",
            "⏳ <b>[모의주문 부분체결]</b>",
        )
        text += "\n\n💡 남은 수량은 주문 모니터가 계속 확인합니다."
    elif any(
        f"처리 상태</b> : <code>{status}</code>" in text
        for status in ("PENDING", "SUBMITTED", "CREATED")
    ):
        text = text.replace(
            "✅ <b>[모의주문 전송 완료]</b> ✨",
            "⏳ <b>[모의주문 접수]</b>",
        )
        text += (
            "\n\n💡 아직 체결 완료가 아닙니다. "
            "<code>/order</code>에서 진행 상태를 확인하세요."
        )
    return text


class RuntimeTelegramBotApp(V322TelegramBotApp):
    """V3.2.2 Telegram app with isolated schedulers and operator alerts."""

    def __init__(self, *args, **kwargs) -> None:
        self._runtime_error_notice_at: dict[str, float] = {}
        self._reconciliation_notice_at: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(_operator_text(text), markup=markup, chat_id=chat_id)

    def notify_startup_reconciliation(self, mismatches: dict[str, list[str]]) -> None:
        if not mismatches:
            return
        lines = [
            "🚨 <b>[JDSS 시작 정합성 점검 실패]</b>",
            "",
            "봇은 시작됐지만 아래 항목은 SAFE_MODE입니다. 신규매수는 차단됩니다.",
        ]
        for symbol, issues in mismatches.items():
            lines.append(f"• <b>{html.escape(symbol)}</b>: {html.escape(', '.join(issues))}")
        lines.extend(
            [
                "",
                "💡 <code>/errors</code>, <code>/order</code>, "
                "<code>/status</code>에서 원인을 확인하세요.",
            ]
        )
        try:
            self._send("\n".join(lines))
        except Exception:
            LOGGER.exception("시작 정합성 경고 Telegram 전송 실패")

    def _notify_runtime_error(
        self,
        event_type: str,
        title: str,
        exc: Exception,
        *,
        cooldown_seconds: int = 600,
    ) -> None:
        LOGGER.error(
            "%s: %s",
            title,
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self.repository.log_event(
            "ERROR",
            event_type,
            str(exc),
            context={"exception": type(exc).__name__},
        )
        now = time.monotonic()
        last = self._runtime_error_notice_at.get(event_type)
        if last is not None and now - last < cooldown_seconds:
            return
        self._runtime_error_notice_at[event_type] = now
        try:
            self._send(
                f"⚠️ <b>[JDSS {html.escape(title)}]</b>\n\n"
                f"• 오류: <code>{html.escape(str(exc))}</code>\n"
                "• 다른 안전 점검은 가능한 범위에서 계속 수행합니다.\n"
                "• 같은 오류 알림은 10분 동안 반복 전송하지 않습니다.\n\n"
                "💡 <code>/errors</code>에서 기록을 확인할 수 있습니다."
            )
        except Exception:
            LOGGER.exception("운영 오류 Telegram 알림 전송 실패")

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
        self._send(
            f"🚨 <b>[{html.escape(symbol)} SAFE_MODE 경고]</b>\n"
            + "\n".join(html.escape(issue) for issue in issues)
            + "\n\n💡 같은 원인은 10분 동안 반복 알림하지 않습니다. "
            "<code>/errors</code>에서 기록을 확인하세요."
        )

    def _scheduler_loop(self) -> None:
        """Run independent safety jobs so one failure cannot starve the others."""
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            completed = None
            daily_due = _daily_analysis_is_due(
                datetime.now(SEOUL_TZ),
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

            if self.portfolio_service is not None and completed is not None:
                try:
                    portfolio_run = self.portfolio_service.run_month_end()
                    if portfolio_run is not None:
                        for event in portfolio_run.events:
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

            monitor_due = (
                time.monotonic() - self._last_monitor
                >= self.config.scheduler.order_monitor_interval_seconds
            )
            if _is_toss_order_maintenance_window(datetime.now(SEOUL_TZ)):
                monitor_due = False

            if monitor_due:
                try:
                    for event in self.order_monitor.run_once():
                        self._send(f"ℹ️ {html.escape(event)}")
                except Exception as exc:
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
                        self._notify_runtime_error(
                            "IDLE_CASH_MONITOR_ERROR",
                            "SGOV 주문 점검 오류",
                            exc,
                        )

                try:
                    mismatches = self.reconciliation_service.run()
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

            cash_due = (
                self.idle_cash_manager is not None
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
