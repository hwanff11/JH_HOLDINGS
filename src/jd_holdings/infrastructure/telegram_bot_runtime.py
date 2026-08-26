from __future__ import annotations

import html
import logging
import re
import time
from datetime import UTC, datetime

from .market_clock import session_is_allowed
from .telegram_bot import (
    SEOUL_TZ,
    _daily_analysis_is_due,
    _format_idle_cash_event,
    _is_toss_order_maintenance_window,
    _operator_error_summary,
)
from .telegram_bot_v322 import V322TelegramBotApp

LOGGER = logging.getLogger(__name__)

_ALLOCATION_EVENT_RE = re.compile(
    r"^V3\.2\.2 배분 레버리지 (?P<leverage>[0-9.]+)x · "
    r"RS6M (?P<rs>ON|OFF) · JDSS TQQQ (?P<tqqq>ON|OFF) / "
    r"SOXL (?P<soxl>ON|OFF) · 목표 QQQ (?P<qqq>[0-9.]+)% / "
    r"TQQQ (?P<tqqq_weight>[0-9.]+)% / SOXL (?P<soxl_weight>[0-9.]+)%$"
)
_CAPITAL_EVENT_RE = re.compile(
    r"^HWM USD (?P<hwm>[0-9,.]+) · 위험예산 USD (?P<risk>[0-9,.]+) · "
    r"완결종가 평가액 USD (?P<equity>[0-9,.]+)$"
)


def _leverage_label(value: float) -> str:
    if value <= 0.5:
        return "🔵 방어적 운용"
    if value <= 1.0:
        return "🟡 기본 운용"
    if value <= 1.25:
        return "🟢 상승추세 운용"
    return "🟢 공격적 운용"


def _plain_strategy_summary(
    leverage: float,
    *,
    rs_active: bool,
    tqqq_active: bool,
    soxl_active: bool,
) -> str:
    if leverage <= 0.5:
        return "시장 변동성이 높아 위험노출을 줄이고 방어적으로 운용하는 상태입니다."
    if leverage <= 1.0:
        return "시장 방향성이 강하지 않아 기본 노출을 유지하며 과도한 레버리지를 피하는 상태입니다."
    if rs_active:
        base = (
            "기술주 상승추세가 우호적이고 반도체도 상대적으로 강해 QQQ를 중심으로 "
            "TQQQ·SOXL 비중을 함께 가져가는 상태입니다."
        )
    else:
        base = (
            "기술주 상승추세는 우호적이지만 반도체 상대강도 우위는 없어 "
            "QQQ·TQQQ 중심으로 운용하는 상태입니다."
        )
    if tqqq_active and soxl_active:
        return base + " TQQQ와 SOXL 모두 추가 레버리지 조건도 충족했습니다."
    if tqqq_active:
        return base + " TQQQ 추가 레버리지 조건만 충족했습니다."
    if soxl_active:
        return base + " SOXL 추가 레버리지 조건만 충족했습니다."
    return base


def _format_daily_portfolio_brief(
    trade_date: str,
    events: tuple[str, ...],
    *,
    has_buy_signals: bool,
) -> tuple[str | None, tuple[str, ...]]:
    """Convert internal V3.2.2 allocation events into one operator-friendly brief."""
    allocation_match = None
    capital_match = None
    remaining: list[str] = []

    for event in events:
        if allocation_match is None:
            allocation_match = _ALLOCATION_EVENT_RE.fullmatch(event)
            if allocation_match is not None:
                continue
        if capital_match is None:
            capital_match = _CAPITAL_EVENT_RE.fullmatch(event)
            if capital_match is not None:
                continue
        remaining.append(event)

    if allocation_match is None:
        return None, events

    leverage = float(allocation_match.group("leverage"))
    rs_active = allocation_match.group("rs") == "ON"
    tqqq_active = allocation_match.group("tqqq") == "ON"
    soxl_active = allocation_match.group("soxl") == "ON"
    risk_reduction = any("위험축소" in event for event in remaining)

    lines = [
        "🌅 <b>[JDSS 아침 운용 브리핑]</b>",
        f"기준일 : <code>{html.escape(trade_date)}</code>",
        "",
        "✅ <b>오늘의 결론</b>",
    ]
    if risk_reduction:
        lines.extend(
            [
                "현재 <b>위험축소 매도가 진행 중</b>입니다.",
                "매도와 계좌·원장 정합성 확인이 끝날 때까지 신규 매수는 자동 차단됩니다.",
            ]
        )
    elif has_buy_signals:
        lines.extend(
            [
                "목표비중 조정을 위한 <b>신규 매수 승인이 필요합니다.</b>",
                "아래에 이어지는 <b>‘오늘 매수 검토 가능’</b>에서 최종 확인해 주세요.",
            ]
        )
    else:
        lines.extend(
            [
                "현재 목표비중과 운용상태를 유지합니다.",
                "<b>지금 승인할 신규 매수 주문은 없습니다.</b>",
            ]
        )

    summary = _plain_strategy_summary(
        leverage,
        rs_active=rs_active,
        tqqq_active=tqqq_active,
        soxl_active=soxl_active,
    )
    lines.extend(
        [
            "",
            "🎯 <b>현재 목표 비중</b>",
            f"• QQQ   <code>{float(allocation_match.group('qqq')):.1f}%</code>",
            f"• TQQQ <code>{float(allocation_match.group('tqqq_weight')):.1f}%</code>",
            f"• SOXL <code>{float(allocation_match.group('soxl_weight')):.1f}%</code>",
            "",
            "📈 <b>전략 판단</b>",
            f"• 시장상태 : <b>{_leverage_label(leverage)}</b>",
            f"• 목표 노출 : <code>{leverage:.2f}x</code>",
            f"• 반도체 상대강도 : {'✅ 강세' if rs_active else '➖ 우위 아님'}",
            "• 추가 레버리지 : "
            f"TQQQ {'✅' if tqqq_active else '➖'} / "
            f"SOXL {'✅' if soxl_active else '➖'}",
            "",
            "쉽게 말하면,",
            f"<b>{summary}</b>",
        ]
    )

    if capital_match is not None:
        hwm = capital_match.group("hwm")
        risk = capital_match.group("risk")
        equity = capital_match.group("equity")
        lines.extend(
            [
                "",
                "💵 <b>자금관리</b>",
                f"• 현재 평가액 : <code>${equity}</code>",
                f"• 최고 평가액 : <code>${hwm}</code>",
                f"• 투자규모 계산 기준 : <code>${risk}</code>",
                "",
                "HWM75 적용 중",
                "→ 향후 수익이 발생하면 <b>수익의 75%만</b> 다음 투자규모 확대에 반영합니다.",
            ]
        )

    lines.extend(
        [
            "",
            "━━━━━━━━━━━━━━",
            "🧾 실제 주문 필요 여부 → <code>/today</code>",
            "📊 상세 보유·목표 → <code>/portfolio</code>",
        ]
    )
    return "\n".join(lines), tuple(remaining)


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

    def _prepare_today_buy_batch(self):
        """Fail closed with a readable state before creating any approvals."""
        now_kst = datetime.now(SEOUL_TZ)
        if _is_toss_order_maintenance_window(now_kst):
            return (
                "🛠️ <b>[오늘 주문 · 토스 주문 점검시간]</b>\n\n"
                "한국시간 08:50~08:59에는 미국주식 주문 취소·리셋 구간이므로 "
                "새 매수 검토를 만들지 않습니다. 09:00 이후 다시 확인해 주세요.",
                None,
            )
        session = self.market_clock.classify_session(datetime.now(UTC))
        if not session_is_allowed(session, self.config):
            return (
                "🕒 <b>[오늘 주문 · 주문 가능시간 대기]</b>\n\n"
                f"현재 세션 <code>{html.escape(session)}</code>은 V3.2.2 주문 허용시간이 아닙니다.\n"
                "전략 목표는 유지되며 주문 가능한 세션에서 다시 검토해 주세요.",
                None,
            )
        return super()._prepare_today_buy_batch()

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
                f"• 오류: <code>{html.escape(_operator_error_summary(exc))}</code>\n"
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
