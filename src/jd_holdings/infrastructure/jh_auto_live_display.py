from __future__ import annotations

import html
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from jd_holdings.automation import AUTO_VERSION
from jd_holdings.application.operational_safety import OPERATOR_BUY_HALT_KEY
from jd_holdings.infrastructure import telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.jh_auto_telegram import (
    JHAutoTelegramBotApp,
    _auto_bot_commands,
    _money,
)

SEOUL_TZ = ZoneInfo("Asia/Seoul")
NEW_YORK_TZ = ZoneInfo("America/New_York")
AUTO_SCHEDULER_LAST_CYCLE_KEY = "jh_auto_scheduler_last_cycle_at"
AUTO_SCHEDULER_LAST_SAFETY_OK_KEY = "jh_auto_scheduler_last_safety_ok_at"
AUTO_SCHEDULER_FAILURE_KEY = "jh_auto_scheduler_failure"
AUTO_MARKET_OPEN_NOTICE_KEY = "jh_auto_market_open_notice_session"


class LiveJHAutoTelegramBotApp(JHAutoTelegramBotApp):
    """Live-only operator display and runtime isolation for JH AUTO.

    LIVE capital is operator-configured, so legacy research-era values are never shown
    as live limits.  The class also owns the operator-facing confirmation freshness and
    Telegram/runtime isolation rules: a stale confirmation cannot authorize changed
    capital, and a Telegram delivery failure cannot terminate order monitoring after a
    broker order has already been submitted.
    """

    _RESEARCH_CAPITAL_NOTE = (
        "ℹ️ JDSS의 <code>$50,000</code>은 공식 연구·백테스트 기준값이며 "
        "실거래 한도가 아닙니다."
    )
    _MUTABLE_CAPITAL_NOTE = (
        "ℹ️ 운용 기준자금은 최초 시작 후에도 변경할 수 있습니다. "
        "증액은 추가분을 단계적으로 열고, 감액은 위험축소를 우선합니다."
    )

    def __init__(self, *args, **kwargs) -> None:
        self._auto_confirm_lock = threading.RLock()
        self._auto_pending_fingerprints: dict[str, tuple[str, ...]] = {}
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_watchdog_thread: threading.Thread | None = None
        self._scheduler_failure_noted = False
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------------
    # LIVE dashboard fast-path and operator-first status
    # ------------------------------------------------------------------
    def _register_handlers(self) -> None:
        """Register the LIVE AUTO dashboard before the inherited legacy dashboard."""
        bot = self.bot

        @bot.message_handler(commands=["dashboard", "d"])
        def live_auto_dashboard(message):
            if not self._authorized_message(message):
                return
            started = time.perf_counter()
            try:
                self._send(
                    self._format_auto_dashboard(),
                    markup=self._dashboard_markup(),
                )
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("JH AUTO dashboard fast-path 실패")
                self._send(
                    "❌ 대시보드를 가져오지 못했습니다.\n"
                    f"<code>{telegram_bot_module._operator_error_summary(exc)}</code>"
                )
                return
            elapsed = time.perf_counter() - started
            log = (
                telegram_bot_module.LOGGER.info
                if elapsed >= 0.75
                else telegram_bot_module.LOGGER.debug
            )
            log("JH AUTO dashboard 응답 준비 %.3fs (legacy analyze_all 생략)", elapsed)

        super()._register_handlers()

    def _operator_action(self) -> str:
        settings = self.auto_service.settings()
        if not settings.configured:
            return "⚙️ <code>/auto</code>에서 기준자금·자동운용비율 설정"
        if not settings.launch_authorized:
            return "▶️ 설정 확인 후 <code>/auto start</code> 최초 시작 검토"
        if settings.operator_halt_latched:
            return "🚨 대표 긴급정지 유지 중 · 의도한 경우에만 <code>/resume</code>"
        if settings.quarantine or self._portfolio_safe_mode():
            return "🛡️ 자동 안전점검 대기 · 지속 시 <code>/account</code> 확인"
        if self.repository.open_orders():
            return "⏳ 주문감시·체결·계좌대조 완료 대기"
        if self.market_clock.classify_session() == "regular":
            return "✅ 자동운용 중 · 별도 개입 불필요"
        return "🌙 미국 정규장 대기 · 별도 개입 불필요"

    def _scheduler_status_text(self) -> str:
        thread = getattr(self, "_scheduler_thread", None)
        if thread is None:
            return "준비 중"
        if thread.is_alive():
            last_ok = self.repository.get_system_value(AUTO_SCHEDULER_LAST_SAFETY_OK_KEY)
            return "정상" if last_ok else "기동 후 안전점검 대기"
        return "중단 · 신규매수 차단 필요"

    def _replace_prelaunch_performance(self, text: str) -> str:
        if self.auto_service.settings().launch_authorized:
            return text
        prefixes = (
            "• 자동운용자산 :",
            "• 누적 운용손익 :",
            "• 누적 운용수익률 :",
            "• 투자중 :",
        )
        lines: list[str] = []
        for line in text.splitlines():
            if line.startswith(prefixes):
                label = line.split(":", 1)[0].rstrip()
                line = f"{label} : <b>운용 시작 전</b>"
            lines.append(line)
        return "\n".join(lines)

    def _replace_hwm_lines(self, text: str) -> str:
        settings = self.auto_service.settings()
        if settings.launch_authorized:
            return text.replace("HWM75 위험한도", "HWM75 현재 위험예산")

        lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("• 최고 평가액 :"):
                line = "• 최고 평가액 : <b>시작 전</b>"
            elif line.startswith("• HWM75 위험한도 :"):
                line = "• HWM75 현재 위험예산 : <b>시작 전</b>"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _insert_before(text: str, marker: str, extra: str) -> str:
        if extra in text or marker not in text:
            return text
        return text.replace(marker, f"{extra}\n{marker}", 1)

    @staticmethod
    def _dedupe_dashboard_capital_lines(text: str) -> str:
        prefix = "• 자금축소 회수대기 :"
        seen = False
        lines: list[str] = []
        for line in text.splitlines():
            if line.startswith(prefix):
                if seen:
                    continue
                seen = True
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Confirmation freshness: displayed capital must equal confirmed capital
    # ------------------------------------------------------------------
    def _settings_fingerprint(self) -> tuple[str, ...]:
        settings = self.auto_service.settings()
        return (
            str(settings.base_capital),
            str(settings.ratio),
            str(settings.target_principal),
            str(settings.effective_principal),
            str(int(settings.launch_authorized)),
            str(settings.ramp_stage),
            str(settings.ramp_target_principal),
        )

    def _new_pending(self, kind: str, value: str) -> str:
        # A newer capital/start review invalidates every older button.  This makes the
        # operator's visible review screen the single current source of consent.
        if not hasattr(self, "_auto_pending_fingerprints"):
            self._auto_pending_fingerprints = {}
        self._auto_pending.clear()
        self._auto_pending_fingerprints.clear()
        token = super()._new_pending(kind, value)
        self._auto_pending_fingerprints[token] = self._settings_fingerprint()
        return token

    def _expire_pending(self) -> None:
        super()._expire_pending()
        fingerprints = getattr(self, "_auto_pending_fingerprints", None)
        if fingerprints is not None:
            self._auto_pending_fingerprints = {
                token: value
                for token, value in fingerprints.items()
                if token in self._auto_pending
            }

    def _review_change(self, kind: str, value: str):
        text, markup = super()._review_change(kind, value)
        settings = self.auto_service.settings()
        if kind == "capital":
            proposed_base = self.auto_service._normalize_money(value)
            proposed_target = proposed_base * (settings.ratio or Decimal("0"))
        elif kind == "ratio":
            proposed_ratio = self.auto_service._normalize_ratio_percent(value)
            proposed_target = (settings.base_capital or Decimal("0")) * proposed_ratio
        elif kind == "start":
            proposed_target = settings.target_principal
        else:
            return text, markup

        if not settings.launch_authorized:
            next_effective = proposed_target * Decimal("0.50")
            next_label = "확정 후 첫 단계 실제 허용원금"
        elif proposed_target < settings.effective_principal:
            next_effective = proposed_target
            next_label = "감액 후 허용원금"
        elif proposed_target > settings.target_principal and settings.ramp_stage == 0:
            next_effective = settings.effective_principal + (
                proposed_target - settings.effective_principal
            ) * Decimal("0.50")
            next_label = "추가자금 1단계 적용 후 허용원금"
        else:
            next_effective = settings.effective_principal
            next_label = "현재 열린 원금"
        extra = (
            f"\n\n💰 <b>확정할 자금 범위</b>\n"
            f"• 목표 자동원금 : <code>{_money(proposed_target)}</code>\n"
            f"• {next_label} : <code>{_money(next_effective)}</code>"
        )
        return text.replace("\n\n한 번 더 확인해 주세요.", f"{extra}\n\n한 번 더 확인해 주세요."), markup

    def _confirm_pending(self, token: str) -> str:
        lock = getattr(self, "_auto_confirm_lock", None)
        if lock is None:
            self._auto_confirm_lock = threading.RLock()
            lock = self._auto_confirm_lock
        with lock:
            self._expire_pending()
            expected = getattr(self, "_auto_pending_fingerprints", {}).get(token)
            if expected is None or token not in self._auto_pending:
                raise RuntimeError("확인 요청이 만료되었거나 이미 사용되었습니다")
            if expected != self._settings_fingerprint():
                self._auto_pending.pop(token, None)
                self._auto_pending_fingerprints.pop(token, None)
                raise RuntimeError(
                    "검토 후 자금설정 또는 운용상태가 변경되었습니다. 최신 /auto 화면에서 다시 확인해 주세요"
                )
            result = super()._confirm_pending(token)
            self._auto_pending.clear()
            self._auto_pending_fingerprints.clear()
            return result

    # ------------------------------------------------------------------
    # Human-readable order history and message normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _human_order_status(status: object) -> str:
        return {
            "CREATED": "주문 준비",
            "SUBMITTED": "접수·체결대기",
            "PENDING": "접수·체결대기",
            "PARTIAL_FILLED": "부분체결",
            "PARTIALLY_FILLED": "부분체결",
            "PENDING_CANCEL": "취소 확인 중",
            "PENDING_REPLACE": "정정 확인 중",
            "FILLED": "체결완료",
            "CANCELED": "취소완료",
            "REJECTED": "주문거절",
            "REPLACED": "정정완료",
            "UNKNOWN": "상태확인 필요·신규매수 차단",
            "ERROR": "실행오류·안전확인 필요",
            "REVIEWING": "주문 전 최종검증",
        }.get(str(status or "").upper(), str(status or "-"))

    @staticmethod
    def _next_order_action(status: object, remaining: int) -> str:
        value = str(status or "").upper()
        if value == "UNKNOWN":
            return "자동 재주문 금지 · 토스 실제상태 확인"
        if remaining > 0 and value in {
            "CREATED",
            "SUBMITTED",
            "PENDING",
            "PARTIAL_FILLED",
            "PARTIALLY_FILLED",
            "PENDING_CANCEL",
            "PENDING_REPLACE",
        }:
            return "주문감시 → 체결상태 확인 → 계좌·원장 재대조"
        if value == "FILLED":
            return "체결 원장반영 → 계좌·원장 재대조"
        if value in {"CANCELED", "REJECTED", "REPLACED", "ERROR"}:
            return "다음 안전주기에서 상태·목표를 새로 계산"
        return "다음 안전주기에서 재확인"

    def _format_recent_cycles(self) -> str:
        rows = self.auto_service.recent_cycles(5)
        lines = ["📋 <b>[최근 JH AUTO 자동주문]</b>", ""]
        if not rows:
            lines.append("아직 자동주문 기록이 없습니다.")
            return "\n".join(lines)
        for row in rows:
            requested = int(row.get("requested_qty") or 0)
            filled = int(row.get("filled_qty") or 0)
            remaining = max(0, requested - filled)
            avg_raw = row.get("average_fill_price")
            avg = Decimal(str(avg_raw)) if avg_raw not in (None, "") else None
            amount = avg * filled if avg is not None and filled > 0 else None
            started_raw = str(row.get("started_at") or "")
            try:
                started = datetime.fromisoformat(started_raw).astimezone(SEOUL_TZ)
                when = started.strftime("%m-%d %H:%M KST")
            except ValueError:
                when = "시각 확인 필요"
            status = row.get("status")
            lines.extend(
                [
                    f"<b>{html.escape(str(row.get('symbol') or '-'))}</b> · {when}",
                    f"└ 상태 <b>{html.escape(self._human_order_status(status))}</b> · 체결 <code>{filled}/{requested}주</code>",
                    f"└ 체결단가 <code>{_money(avg)}</code> · 체결금액 <code>{_money(amount)}</code> · 미체결 <code>{remaining}주</code>",
                    f"└ 다음 처리 : {html.escape(self._next_order_action(status, remaining))}",
                ]
            )
        return "\n".join(lines)

    def _normalize_inherited_auto_text(self, text: str) -> str:
        replacements = (
            (
                "목표비중 조정을 위한 <b>신규 매수 승인이 필요합니다.</b>",
                "목표비중 조정을 위한 <b>자동매수 후보가 있습니다.</b>",
            ),
            (
                "아래에 이어지는 <b>‘오늘 매수 검토 가능’</b>에서 최종 확인해 주세요.",
                "개별 BUY 승인은 필요하지 않으며 다음 독립 안전주기에서 다시 검증합니다.",
            ),
            (
                "<b>지금 승인할 신규 매수 주문은 없습니다.</b>",
                "<b>현재 신규 자동매수 후보는 없습니다.</b>",
            ),
            (
                "실계좌가 연결되어 있으며 신규 매수는 운영자 잠금과 2단계 승인으로 통제합니다.",
                "실계좌가 연결되어 있으며 신규 매수는 최초 시작승인·대표 긴급정지·JH AUTO 내부 안전검증으로 통제합니다.",
            ),
            (
                "🔥 실거래에서는 신규 매수 잠금 해제와 2단계 승인을 모두 통과해야 실제 토스 주문이 전송됩니다.",
                "🔥 실거래에서는 JH AUTO 시작승인과 내부 2단계 검증·최종 주문경계를 모두 통과해야 실제 토스 주문이 전송됩니다.",
            ),
            (
                "현재 신규 BUY는 안전조건을 통과한 상태이며 실제 주문은 기존 2단계 승인 후에만 제출됩니다.",
                "현재 신규 BUY는 안전조건을 통과한 상태이며 JH AUTO가 다음 독립 안전주기에서 내부 2단계 검증 후에만 제출할 수 있습니다.",
            ),
            (
                "이제 신규 BUY 승인 절차를 진행할 수 있습니다.",
                "이제 JH AUTO가 다음 독립 안전주기에서 신규 BUY 여부를 다시 판단할 수 있습니다.",
            ),
            (
                "⚠️ 실제 주문은 여전히 기존 JDSS 2단계 승인 후에만 제출됩니다.",
                "⚠️ 실제 주문은 별도 개별 승인 없이 JH AUTO 내부 2단계 검증과 최종 주문경계를 통과한 경우에만 제출됩니다.",
            ),
            (
                "각 BUY는 기존 2단계 주문 승인이 추가로 필요합니다.",
                "별도 개별 BUY 승인은 필요하지 않으며 JH AUTO 내부 2단계 검증과 최종 안전경계를 다시 통과해야 합니다.",
            ),
            (
                "• 위험증가 BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동입니다.",
                "• 위험증가 BUY는 JH AUTO 내부 2단계 검증, 위험축소 SELL은 자동입니다.",
            ),
        )
        for old, new in replacements:
            text = text.replace(old, new)

        for raw, human in (
            ("SUBMITTED", "접수·체결대기"),
            ("PENDING", "접수·체결대기"),
            ("PARTIAL_FILLED", "부분체결"),
            ("FILLED", "체결완료"),
            ("CANCELED", "취소완료"),
            ("REJECTED", "주문거절"),
            ("UNKNOWN", "상태확인 필요·신규매수 차단"),
        ):
            text = text.replace(f"• 상태 : <code>{raw}</code>", f"• 상태 : <b>{human}</b>")

        if "[JDSS 실거래 아침 브리핑]" in text:
            text = text.replace(
                "[JDSS 실거래 아침 브리핑]", "[JH AUTO 전일 마감 브리핑]"
            )
            settings = self.auto_service.settings()
            perf = self._display_performance()
            lines = []
            for line in text.splitlines():
                if line.startswith("• 현재 평가액 :"):
                    line = (
                        f"• 자동운용자산 : <code>{_money(perf['equity'])}</code>"
                        if settings.launch_authorized
                        else "• 자동운용자산 : <b>시작 전</b>"
                    )
                elif line.startswith("• 최고 평가액 :"):
                    line = (
                        f"• 최고 평가액 : <code>{_money(perf['high_water'])}</code>"
                        if settings.launch_authorized
                        else "• 최고 평가액 : <b>시작 전</b>"
                    )
                elif line.startswith("• 투자규모 계산 기준 :"):
                    line = (
                        f"• HWM75 현재 위험예산 : <code>{_money(perf['risk_budget'])}</code>"
                        if settings.launch_authorized
                        else "• HWM75 현재 위험예산 : <b>시작 전</b>"
                    )
                lines.append(line)
            text = "\n".join(lines)
            text = self._insert_before(
                text,
                "━━━━━━━━━━━━━━",
                f"{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
            )
        return text

    # ------------------------------------------------------------------
    # Daily close brief and market-open notice
    # ------------------------------------------------------------------
    def _daily_brief_display_window_open(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(SEOUL_TZ)
        scheduled = self.config.scheduler.daily_analysis_time_kst
        current_min = current.hour * 60 + current.minute
        scheduled_min = scheduled.hour * 60 + scheduled.minute
        return scheduled_min <= current_min < scheduled_min + 60

    def _market_open_notice_text(self, *, safety_ready: bool, now: datetime) -> str:
        settings = self.auto_service.settings()
        completed = self.market_clock.latest_completed_session(now, delay_minutes=5)
        cores = {
            str(row["symbol"]): row
            for row in self.repository.core_positions()
        }
        weight_lines = []
        for symbol in ("QQQ", "TQQQ", "SOXL"):
            row = cores.get(symbol)
            weight = Decimal(str(row["target_weight"] if row else "0")) * Decimal("100")
            weight_lines.append(f"• {symbol} <code>{weight:.1f}%</code>")
        candidates = sum(
            1
            for signal in self.trading_service.active_signals()
            if str(signal.get("action")) == "CORE_REBALANCE_BUY"
        )
        halt = self.repository.get_system_value(OPERATOR_BUY_HALT_KEY) != "0"
        order_count = len(self.repository.open_orders())
        if not settings.launch_authorized:
            buy_state = "최초 시작 미승인 · 실제 신규매수 차단"
        elif settings.operator_halt_latched or halt:
            buy_state = "신규매수 잠금 ON"
        elif settings.quarantine or self._portfolio_safe_mode():
            buy_state = "안전점검/격리 중 · 신규매수 차단"
        elif order_count:
            buy_state = f"기존 주문 {order_count}건 감시 우선"
        else:
            buy_state = "자동운용 가능 · 주문 직전 조건 재검증"
        return "\n".join(
            [
                "🔔 <b>[JH AUTO 장 시작 점검]</b>",
                f"기준 데이터 : <code>{completed.isoformat()} 종가</code>",
                "",
                f"• 자동운용 : {self._auto_state_label()}",
                f"• 계좌·원장 안전주기 : <b>{'정상' if safety_ready else '재확인 중'}</b>",
                f"• 신규매수 : <b>{buy_state}</b>",
                f"• 자동매수 후보 : <code>{candidates}건</code>",
                "",
                "🎯 <b>현재 목표비중</b>",
                *weight_lines,
                "",
                "ℹ️ 실제 주문은 이 알림과 별개이며 다음 독립 안전주기에서 최신 가격·잔고·미체결·안전상태를 다시 확인합니다.",
            ]
        )

    def _maybe_send_market_open_notice(self, *, safety_ready: bool) -> None:
        now = datetime.now(UTC)
        if self.market_clock.classify_session(now) != "regular":
            return
        settings = self.auto_service.settings()
        if not settings.configured:
            return
        session_key = now.astimezone(NEW_YORK_TZ).date().isoformat()
        if self.repository.get_system_value(AUTO_MARKET_OPEN_NOTICE_KEY) == session_key:
            return
        # Mark first so a transient Telegram error cannot spam every 30 seconds.  The
        # alert is informational; trading safety never depends on its delivery.
        self.repository.set_system_value(AUTO_MARKET_OPEN_NOTICE_KEY, session_key)
        self._send(self._market_open_notice_text(safety_ready=safety_ready, now=now))

    # ------------------------------------------------------------------
    # Scheduler/Telegram fault isolation and heartbeat
    # ------------------------------------------------------------------
    def _is_scheduler_thread(self) -> bool:
        return threading.current_thread() is getattr(self, "_scheduler_thread", None)

    def _record_scheduler_send_failure(self, exc: BaseException) -> None:
        telegram_bot_module.LOGGER.exception("스케줄러 Telegram 알림 전송 실패", exc_info=exc)
        try:
            self.repository.log_event(
                "WARNING",
                "JH_AUTO_SCHEDULER_TELEGRAM_SEND_FAILED",
                "Telegram 전송 실패를 주문감시와 분리했습니다. 자동 안전주기는 계속 실행합니다",
                context={"exception": type(exc).__name__},
            )
        except Exception:
            telegram_bot_module.LOGGER.exception("Telegram 실패 이벤트 원장 기록도 실패")

    def _run_order_safety_cycle(self) -> bool:
        now = datetime.now(UTC).isoformat()
        self.repository.set_system_value(AUTO_SCHEDULER_LAST_CYCLE_KEY, now)
        clean = super()._run_order_safety_cycle()
        if clean:
            self.repository.set_system_value(AUTO_SCHEDULER_LAST_SAFETY_OK_KEY, now)
            self.repository.set_system_value(AUTO_SCHEDULER_FAILURE_KEY, "")
        self._maybe_send_market_open_notice(safety_ready=clean)
        return clean

    def _scheduler_worker(self) -> None:
        try:
            self._scheduler_loop()
        except Exception as exc:
            telegram_bot_module.LOGGER.exception("JH AUTO scheduler 비정상 종료")
            try:
                self.repository.set_system_value(
                    AUTO_SCHEDULER_FAILURE_KEY,
                    f"{type(exc).__name__}:{datetime.now(UTC).isoformat()}",
                )
                self.auto_service.quarantine(
                    f"AUTO_SCHEDULER_TERMINATED:{type(exc).__name__}"
                )
                self.repository.log_event(
                    "SAFE_MODE",
                    "JH_AUTO_SCHEDULER_TERMINATED",
                    "자동운용 스케줄러가 중단되어 신규매수를 차단했습니다",
                    context={"exception": type(exc).__name__},
                )
            except Exception:
                telegram_bot_module.LOGGER.exception("scheduler 종료 후 안전격리 처리 실패")

    def _scheduler_watchdog_loop(self) -> None:
        while not self._stop.wait(30):
            thread = getattr(self, "_scheduler_thread", None)
            if thread is not None and thread.is_alive():
                continue
            if self._stop.is_set() or getattr(self, "_scheduler_failure_noted", False):
                return
            self._scheduler_failure_noted = True
            try:
                self.auto_service.quarantine("AUTO_SCHEDULER_NOT_ALIVE")
                self.repository.log_event(
                    "SAFE_MODE",
                    "JH_AUTO_SCHEDULER_WATCHDOG",
                    "자동운용 스케줄러 생존확인 실패로 신규매수를 차단했습니다",
                )
            except Exception:
                telegram_bot_module.LOGGER.exception("scheduler watchdog 안전격리 실패")
            return

    def run(self) -> None:
        self.bot.set_my_commands(_auto_bot_commands())
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_worker,
            name="jh-auto-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()
        self._scheduler_watchdog_thread = threading.Thread(
            target=self._scheduler_watchdog_loop,
            name="jh-auto-scheduler-watchdog",
            daemon=True,
        )
        self._scheduler_watchdog_thread.start()
        telegram_bot_module.LOGGER.info(
            "JH AUTO %s Telegram polling 시작 (JDSS strategy 3.2.2)",
            AUTO_VERSION,
        )
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

    # ------------------------------------------------------------------
    # Final display composition and safe send
    # ------------------------------------------------------------------
    def _format_auto_dashboard(self) -> str:
        text = self._dedupe_dashboard_capital_lines(super()._format_auto_dashboard())
        text = self._replace_prelaunch_performance(text)
        text = self._replace_hwm_lines(text)
        text = text.replace(
            f"• <b>현재 상태</b> : {self._auto_state_label()}",
            f"• <b>현재 상태</b> : {self._auto_state_label()}\n• <b>운영자 할 일</b> : {self._operator_action()}",
            1,
        )
        text = self._insert_before(
            text,
            "🛡️ <b>안전상태</b>",
            f"{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
        )
        scheduler_line = f"• 자동점검 스케줄러 : <b>{self._scheduler_status_text()}</b>"
        text = self._insert_before(text, "ℹ️ 자동운용 설정과", f"{scheduler_line}\n")
        return text

    def _format_portfolio_message(self) -> str:
        text = self._replace_hwm_lines(super()._format_portfolio_message())
        text = self._insert_before(
            text,
            "ℹ️ 종목 수익률은",
            f"{self._RESEARCH_CAPITAL_NOTE}\n",
        )
        return text

    def _format_auto_control(self):
        text, markup = super()._format_auto_control()
        settings = self.auto_service.settings()
        if settings.launch_authorized:
            hwm_line = "• HWM75 현재 위험예산은 현재 허용원금과 실제 운용성과를 기준으로 계산됩니다."
        else:
            hwm_line = "• HWM75 현재 위험예산 : <b>시작 후 현재 허용원금 기준으로 계산</b>"
        text = self._insert_before(
            text,
            "기준자금 또는 자동운용비율 변경은",
            f"{hwm_line}\n{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
        )
        return text, markup

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        if (
            "[JDSS 실거래 아침 브리핑]" in text
            or "[JH AUTO 아침 브리핑]" in text
        ) and not self._daily_brief_display_window_open():
            telegram_bot_module.LOGGER.info(
                "정시 발송창이 지난 전일 마감 브리핑은 소급 전송하지 않습니다"
            )
            return
        normalized = self._normalize_inherited_auto_text(text)
        try:
            super()._send(normalized, markup=markup, chat_id=chat_id)
        except Exception as exc:
            if self._is_scheduler_thread():
                self._record_scheduler_send_failure(exc)
                return
            raise