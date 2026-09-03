# ruff: noqa: E501
from __future__ import annotations

import html
import secrets
import threading
import time
from dataclasses import dataclass
from decimal import Decimal

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings.automation import AUTO_VERSION
from jd_holdings.automation.service import (
    AUTO_UNITS_KEY,
    JHAutoService,
)
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from . import telegram_bot as telegram_bot_module
from .live_runtime_hardening import HardenedOperationalSafetyTelegramBotApp

AUTO_CONFIRM_TTL_SECONDS = 300


@dataclass(frozen=True)
class _PendingAutoChange:
    kind: str
    value: str
    created_monotonic: float


def _auto_bot_commands() -> list[telebot.types.BotCommand]:
    return [
        telebot.types.BotCommand("dashboard", "자동운용 전체 상태·수익률"),
        telebot.types.BotCommand("today", "오늘 자동매매·종목 수익률"),
        telebot.types.BotCommand("auto", "기준자금·자동운용비율·시작"),
        telebot.types.BotCommand("account", "실제 토스 계좌 확인"),
        telebot.types.BotCommand("portfolio", "종목별 보유·수익률·목표"),
        telebot.types.BotCommand("backtest", "JDSS 전략 과거검증"),
        telebot.types.BotCommand("halt", "대표 긴급 신규매수 정지"),
        telebot.types.BotCommand("help", "전체 기능과 문제 대응"),
    ]


def _money(value: object) -> str:
    return telegram_bot_module._money(value)


def _signed_money(value: object) -> str:
    return telegram_bot_module._signed_money(value)


def _percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):+.2f}%"


def _unsigned_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"


def _quantity(value: object) -> str:
    return telegram_bot_module._quantity(value)


class JHAutoTelegramBotApp(HardenedOperationalSafetyTelegramBotApp):
    """Operator console for JH AUTO 1.0.0.

    Telegram is intentionally no longer the normal BUY approval surface.  It controls
    delegated capital, launch authorization and emergency halt while the autonomous
    scheduler reuses the existing two-step TradingService approval path internally.
    """

    def __init__(self, *args, auto_service: JHAutoService, **kwargs) -> None:
        self.auto_service = auto_service
        self._auto_pending: dict[str, _PendingAutoChange] = {}
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------------
    # Read-only operator views
    # ------------------------------------------------------------------
    def _auto_state_label(self) -> str:
        settings = self.auto_service.settings()
        if settings.operator_halt_latched:
            return "🚨 대표 긴급정지"
        if self._portfolio_safe_mode():
            return "🚨 안전정지 · 신규매수 차단"
        if not settings.launch_authorized:
            return "🟡 최초 시작승인 대기" if settings.configured else "⚙️ 자금설정 대기"
        if settings.quarantine:
            return "🛡️ 자동점검·임시격리"
        if settings.state == "RUNNING":
            return "✅ 자동운전 정상"
        return html.escape(settings.state)

    def _display_snapshot(self) -> dict[str, object]:
        if self.portfolio_service is None:
            return {
                "equity": Decimal("0"),
                "cash": Decimal("0"),
                "invested_market_value": Decimal("0"),
                "high_water": Decimal("0"),
                "risk_budget": Decimal("0"),
                "rows": [],
            }
        return self.portfolio_service.snapshot()

    def _display_performance(self) -> dict[str, Decimal]:
        settings = self.auto_service.settings()
        snapshot = self._display_snapshot()
        equity = Decimal(str(snapshot.get("equity", 0)))
        cash = Decimal(str(snapshot.get("cash", 0)))
        invested = Decimal(str(snapshot.get("invested_market_value", equity - cash)))
        raw_units = self.repository.get_system_value(AUTO_UNITS_KEY) or "0"
        units = Decimal(str(raw_units))
        nav = equity / units if settings.launch_authorized and units > 0 else Decimal("1")
        return {
            "equity": equity,
            "cash": cash,
            "invested": invested,
            "profit": equity - settings.effective_principal if settings.launch_authorized else Decimal("0"),
            "return": nav - Decimal("1"),
            "nav": nav,
            "high_water": Decimal(str(snapshot.get("high_water", 0))),
            "risk_budget": Decimal(str(snapshot.get("risk_budget", 0))),
        }

    def _portfolio_rows(self) -> dict[str, dict]:
        snapshot = self._display_snapshot()
        return {
            str(row["symbol"]): row
            for row in snapshot.get("rows", [])
            if str(row.get("symbol")) in ALLOCATION_SYMBOLS
        }

    def _format_auto_dashboard(self) -> str:
        settings = self.auto_service.settings()
        perf = self._display_performance()
        ratio = f"{settings.ratio_percent:.2f}%" if settings.ratio_percent is not None else "미설정"
        base = _money(settings.base_capital) if settings.base_capital is not None else "미설정"
        ramp = (
            f"{settings.ramp_stage}/3"
            if settings.ramp_stage
            else ("완료" if settings.launch_authorized else "시작 전")
        )
        pending = len(self.repository.open_orders())
        return "\n".join(
            [
                "🌟 <b>[JH_HOLDINGS 자동운용 대시보드]</b>",
                "",
                f"• <b>현재 상태</b> : {self._auto_state_label()}",
                "• <b>투자전략</b> : <code>JDSS 3.2.2</code>",
                f"• <b>자동매매</b> : <code>JH AUTO {AUTO_VERSION}</code>",
                "",
                "💰 <b>자동운용 자금</b>",
                f"• 운용 기준자금 : <code>{base}</code>",
                f"• 자동운용비율 : <code>{ratio}</code>",
                f"• 목표 자동원금 : <code>{_money(settings.target_principal)}</code>",
                f"• 현재 허용원금 : <code>{_money(settings.effective_principal)}</code>",
                f"• 자금투입 단계 : <code>{ramp}</code>",
                "",
                "📈 <b>현재 성과</b>",
                f"• 자동운용자산 : <code>{_money(perf['equity'])}</code>",
                f"• 누적 운용손익 : <code>{_signed_money(perf['profit'])}</code>",
                f"• 누적 운용수익률 : <code>{_percent(perf['return'])}</code>",
                f"• 투자중 : <code>{_money(perf['invested'])}</code> · 현금 <code>{_money(perf['cash'])}</code>",
                f"• 최고 평가액 : <code>{_money(perf['high_water'])}</code>",
                f"• HWM75 위험한도 : <code>{_money(perf['risk_budget'])}</code>",
                "",
                "🛡️ <b>안전상태</b>",
                f"• 최초 시작승인 : <b>{'완료' if settings.launch_authorized else '미승인 · 실제 BUY 차단'}</b>",
                f"• 대표 긴급정지 : <b>{'ON' if settings.operator_halt_latched else 'OFF'}</b>",
                f"• 시스템 임시격리 : <b>{'ON' if settings.quarantine else 'OFF'}</b>",
                f"• 진행 중 주문 : <code>{pending}건</code>",
                "",
                "ℹ️ 자동운용 설정과 최초 시작은 <code>/auto</code>에서 관리합니다.",
            ]
        )

    def _format_portfolio_message(self) -> str:
        settings = self.auto_service.settings()
        perf = self._display_performance()
        rows = self._portfolio_rows()
        lines = [
            "📊 <b>[JH AUTO 포트폴리오]</b>",
            "",
            "📈 <b>전체 성과</b>",
            f"• 자동운용자산 : <code>{_money(perf['equity'])}</code>",
            f"• 누적 운용손익 : <code>{_signed_money(perf['profit'])}</code>",
            f"• 누적 운용수익률 : <code>{_percent(perf['return'])}</code>",
            f"• 현재 투자액 : <code>{_money(perf['invested'])}</code>",
            "",
            "📦 <b>종목별 보유·목표·보유수익률</b>",
        ]
        for symbol in ALLOCATION_SYMBOLS:
            row = rows.get(symbol)
            if row is None:
                lines.append(f"• <b>{symbol}</b> : 자료 없음")
                continue
            qty = int(row.get("core_quantity", 0))
            target_qty = int(self.repository.get_core_position(symbol)["target_qty"])
            market_value = Decimal(str(row.get("core_market_value", 0)))
            cost_basis = Decimal(str(row.get("cost_basis", 0)))
            unrealized = Decimal(str(row.get("unrealized_profit", market_value - cost_basis)))
            holding_return = unrealized / cost_basis if cost_basis > 0 else Decimal("0")
            avg = cost_basis / qty if qty > 0 else Decimal("0")
            current_price = Decimal(str(row.get("price", 0)))
            lines.extend(
                [
                    f"<b>{symbol}</b> · <code>{qty}주</code> / 목표 <code>{target_qty}주</code>",
                    f"└ 평단 <code>{_money(avg)}</code> · 현재가 <code>{_money(current_price)}</code>",
                    f"└ 평가액 <code>{_money(market_value)}</code> · 평가손익 <code>{_signed_money(unrealized)}</code>",
                    f"└ 보유수익률 <code>{_percent(holding_return)}</code> · 목표비중 <code>{Decimal(str(row.get('target_weight', 0))) * 100:.2f}%</code>",
                ]
            )
        lines.extend(
            [
                "",
                f"• 현재 허용원금 : <code>{_money(settings.effective_principal)}</code>",
                f"• HWM75 위험한도 : <code>{_money(perf['risk_budget'])}</code>",
                "ℹ️ 종목 수익률은 현재 보유분의 평가손익 기준이며, 전체 수익률은 자금 증감 영향을 제거한 자동운용 성과입니다.",
            ]
        )
        return "\n".join(lines)

    def _format_today_auto(self) -> str:
        settings = self.auto_service.settings()
        perf = self._display_performance()
        rows = self._portfolio_rows()
        session = self.market_clock.classify_session()
        lines = [
            "🧾 <b>[오늘 자동운용]</b>",
            "",
            f"• <b>상태</b> : {self._auto_state_label()}",
            f"• <b>현재 미국장</b> : <code>{html.escape(session)}</code>",
            f"• <b>전체 누적수익률</b> : <code>{_percent(perf['return'])}</code>",
            f"• <b>전체 누적손익</b> : <code>{_signed_money(perf['profit'])}</code>",
            "",
            "📦 <b>종목별 현재 상태</b>",
        ]
        for symbol in ALLOCATION_SYMBOLS:
            row = rows.get(symbol)
            core = self.repository.get_core_position(symbol)
            qty = int(core["qty"])
            target = int(core["target_qty"])
            cost_basis = Decimal(str(core["cost_basis"] or "0"))
            market_value = Decimal(str(row.get("core_market_value", 0))) if row else Decimal("0")
            unrealized = (
                Decimal(str(row.get("unrealized_profit", market_value - cost_basis))) if row else Decimal("0")
            )
            holding_return = unrealized / cost_basis if cost_basis > 0 else Decimal("0")
            diff = target - qty
            if diff > 0:
                action = f"+{diff}주 자동매수 필요"
            elif diff < 0:
                action = f"{-diff}주 위험축소 필요"
            else:
                action = "목표수량 충족"
            lines.append(
                f"• <b>{symbol}</b> <code>{qty}→{target}주</code> · "
                f"보유수익률 <code>{_percent(holding_return)}</code> · {action}"
            )
        lines.extend(["", "🛡️ <b>실제 주문</b>"])
        if not settings.launch_authorized:
            lines.append("• <b>최초 시작승인 전 · 실제 자동매수 차단</b>")
        elif settings.operator_halt_latched:
            lines.append("• <b>대표 긴급정지 중 · 신규 자동매수 차단</b>")
        elif settings.quarantine or self._portfolio_safe_mode():
            lines.append("• <b>안전점검/안전정지 중 · 신규 자동매수 차단</b>")
        elif session != "regular":
            lines.append("• 자동 BUY는 미국 정규장에서만 실행합니다.")
        else:
            lines.append("• 안전조건을 모두 통과하면 시스템이 종목별로 한 건씩 순차 실행합니다.")
        lines.append("• 대표님이 별도로 개별 BUY를 승인할 작업은 없습니다.")
        return "\n".join(lines)

    def _format_auto_control(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = self.auto_service.settings()
        base = _money(settings.base_capital) if settings.base_capital is not None else "미설정"
        ratio = f"{settings.ratio_percent:.2f}%" if settings.ratio_percent is not None else "미설정"
        lines = [
            "🤖 <b>[JH AUTO 1.0.0]</b>",
            "",
            f"• 현재 상태 : {self._auto_state_label()}",
            f"• 운용 기준자금 : <code>{base}</code>",
            f"• 자동운용비율 : <code>{ratio}</code>",
            f"• 목표 자동원금 : <code>{_money(settings.target_principal)}</code>",
            f"• 현재 허용원금 : <code>{_money(settings.effective_principal)}</code>",
            f"• 최초 시작승인 : <b>{'완료' if settings.launch_authorized else '미승인'}</b>",
            "",
            "기준자금 또는 자동운용비율 변경은 항상 한 번 더 확인한 뒤 적용됩니다.",
            "직접 입력: <code>/auto capital 50000</code> · <code>/auto ratio 20</code>",
        ]
        markup = InlineKeyboardMarkup(row_width=3)
        markup.row(
            InlineKeyboardButton("$30k", callback_data="auto_review|capital|30000"),
            InlineKeyboardButton("$50k", callback_data="auto_review|capital|50000"),
            InlineKeyboardButton("$100k", callback_data="auto_review|capital|100000"),
        )
        markup.row(
            InlineKeyboardButton("10%", callback_data="auto_review|ratio|10"),
            InlineKeyboardButton("20%", callback_data="auto_review|ratio|20"),
            InlineKeyboardButton("30%", callback_data="auto_review|ratio|30"),
        )
        markup.row(
            InlineKeyboardButton("50%", callback_data="auto_review|ratio|50"),
            InlineKeyboardButton("75%", callback_data="auto_review|ratio|75"),
            InlineKeyboardButton("100%", callback_data="auto_review|ratio|100"),
        )
        if not settings.launch_authorized:
            markup.row(
                InlineKeyboardButton(
                    "▶️ 최초 자동운용 시작 검토",
                    callback_data="auto_review|start|1",
                )
            )
        markup.row(
            InlineKeyboardButton("📋 최근 자동주문", callback_data="auto_recent|5"),
            InlineKeyboardButton("🔄 새로고침", callback_data="auto_status|1"),
        )
        return "\n".join(lines), markup

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.row(
            InlineKeyboardButton("🤖 자동운용 설정", callback_data="auto_status|1"),
            InlineKeyboardButton("🧾 오늘 자동운용", callback_data="ops|today"),
        )
        markup.row(
            InlineKeyboardButton("📊 포트폴리오", callback_data="ops|portfolio"),
            InlineKeyboardButton("💰 실제 계좌", callback_data="ops|account"),
        )
        markup.row(InlineKeyboardButton("📋 진행 중 주문", callback_data="ops|orders"))
        return markup

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        if "[JDSS V3.2.2 운영 대시보드]" in text:
            text = self._format_auto_dashboard()
        super()._send(text, markup=markup, chat_id=chat_id)

    def _prepare_today_buy_batch(self):
        # Read-only by design: /today must never create manual approvals in AUTO mode.
        return self._format_today_auto(), None

    def notify_portfolio_buy_batch_ready(self, signal_ids: tuple[int, ...]) -> None:
        # The autonomous scheduler consumes these signals in a later safety cycle.
        # Do not create review approvals or send manual BUY buttons here.
        if signal_ids:
            self.repository.log_event(
                "INFO",
                "JH_AUTO_SIGNAL_READY",
                "자동매수 후보가 생성되어 다음 안전주기 실행을 기다립니다",
                context={"signal_ids": list(signal_ids)},
            )

    # ------------------------------------------------------------------
    # Automatic scheduler hook
    # ------------------------------------------------------------------
    def _run_order_safety_cycle(self) -> bool:
        clean = super()._run_order_safety_cycle()
        if not clean:
            return False
        try:
            self.auto_service.try_release_quarantine(safety_ready=True)
            self.auto_service.advance_ramp_if_ready()
        except Exception as exc:
            self.auto_service.quarantine(f"AUTO_SAFETY_STATE:{type(exc).__name__}")
            self._notify_runtime_error(
                "JH_AUTO_SAFETY_STATE_ERROR",
                "JH AUTO 안전상태 갱신 오류",
                exc,
            )
            return False

        try:
            result = self.auto_service.execute_one(self.trading_service)
        except Exception as exc:
            self.auto_service.quarantine(f"AUTO_EXECUTION_BOUNDARY:{type(exc).__name__}")
            self._notify_runtime_error(
                "JH_AUTO_EXECUTION_BOUNDARY_ERROR",
                "JH AUTO 자동매수 경계 오류",
                exc,
            )
            return False
        if result is not None:
            self._send(
                "🤖 <b>[JH AUTO 자동주문]</b>\n\n"
                f"• 종목 : <b>{html.escape(result.symbol)}</b>\n"
                f"• 주문수량 : <code>{result.quantity}주</code>\n"
                f"• 현재 체결 : <code>{result.filled_quantity}/{result.quantity}주</code>\n"
                f"• 상태 : <code>{html.escape(result.status)}</code>\n\n"
                "다음 신규 BUY는 주문감시와 계좌·원장 대조를 다시 통과한 뒤에만 진행됩니다."
            )
            # Prevent portfolio decisions/new orders in the same scheduler pass.
            return False
        return True

    # ------------------------------------------------------------------
    # Two-step AUTO settings / launch confirmation
    # ------------------------------------------------------------------
    def _new_pending(self, kind: str, value: str) -> str:
        self._expire_pending()
        token = secrets.token_hex(6)
        self._auto_pending[token] = _PendingAutoChange(kind, value, time.monotonic())
        return token

    def _expire_pending(self) -> None:
        now = time.monotonic()
        self._auto_pending = {
            token: item
            for token, item in self._auto_pending.items()
            if now - item.created_monotonic <= AUTO_CONFIRM_TTL_SECONDS
        }

    def _review_change(self, kind: str, value: str) -> tuple[str, InlineKeyboardMarkup]:
        settings = self.auto_service.settings()
        if kind == "capital":
            proposed = self.auto_service._normalize_money(value)
            ratio = settings.ratio or Decimal("0")
            new_target = proposed * ratio
            title = "운용 기준자금 변경"
            detail = (
                f"현재 <code>{_money(settings.base_capital or 0)}</code> → "
                f"변경 <code>{_money(proposed)}</code>\n"
                f"현재 자동운용비율 기준 목표원금은 약 <code>{_money(new_target)}</code>입니다."
            )
        elif kind == "ratio":
            proposed_ratio = self.auto_service._normalize_ratio_percent(value)
            base = settings.base_capital or Decimal("0")
            new_target = base * proposed_ratio
            title = "자동운용비율 변경"
            detail = (
                f"현재 <code>{settings.ratio_percent or 0:.2f}%</code> → "
                f"변경 <code>{proposed_ratio * 100:.2f}%</code>\n"
                f"현재 기준자금 기준 목표원금은 약 <code>{_money(new_target)}</code>입니다."
            )
        elif kind == "start":
            if not settings.configured:
                raise RuntimeError("운용 기준자금과 자동운용비율을 먼저 설정해 주세요")
            if settings.launch_authorized:
                raise RuntimeError("JH AUTO 최초 시작승인이 이미 완료되어 있습니다")
            title = "JH AUTO 최초 자동운용 시작"
            detail = (
                f"운용 기준자금 <code>{_money(settings.base_capital or 0)}</code>\n"
                f"자동운용비율 <code>{settings.ratio_percent or 0:.2f}%</code>\n"
                f"목표 자동원금 <code>{_money(settings.target_principal)}</code>\n\n"
                "승인 후에는 개별 매수승인 없이 JH AUTO가 실제 Toss 주문을 실행할 수 있습니다.\n"
                "단, <b>이 확인 버튼 자체는 주문을 보내지 않습니다.</b> 다음 독립 안전주기에서 모든 조건을 다시 검사합니다."
            )
        else:
            raise ValueError("알 수 없는 JH AUTO 변경입니다")
        token = self._new_pending(kind, value)
        markup = InlineKeyboardMarkup(row_width=2)
        markup.row(
            InlineKeyboardButton("취소", callback_data=f"auto_cancel|{token}"),
            InlineKeyboardButton("✅ 변경/시작 확정", callback_data=f"auto_confirm|{token}"),
        )
        return f"⚠️ <b>[{title}]</b>\n\n{detail}\n\n한 번 더 확인해 주세요.", markup

    def _confirm_pending(self, token: str) -> str:
        self._expire_pending()
        pending = self._auto_pending.pop(token, None)
        if pending is None:
            raise RuntimeError("확인 요청이 만료되었거나 이미 사용되었습니다")
        if pending.kind in {"capital", "ratio"}:
            if self.repository.open_orders():
                raise RuntimeError("진행 중 주문이 있어 자동운용 설정을 바꿀 수 없습니다")
            if self.auto_service.settings().launch_authorized:
                mismatches = self.reconciliation_service.run()
                if mismatches or self._portfolio_safe_mode():
                    raise RuntimeError("계좌·원장 또는 안전상태가 정상일 때만 자금을 변경할 수 있습니다")
        if pending.kind == "capital":
            updated = self.auto_service.set_base_capital(pending.value)
            return (
                "✅ 운용 기준자금을 변경했습니다.\n"
                f"현재 기준자금 <code>{_money(updated.base_capital or 0)}</code> · "
                f"목표 자동원금 <code>{_money(updated.target_principal)}</code>"
            )
        if pending.kind == "ratio":
            updated = self.auto_service.set_ratio_percent(pending.value)
            return (
                "✅ 자동운용비율을 변경했습니다.\n"
                f"현재 비율 <code>{updated.ratio_percent or 0:.2f}%</code> · "
                f"목표 자동원금 <code>{_money(updated.target_principal)}</code>"
            )
        if pending.kind == "start":
            before_orders = len(self.repository.open_orders())
            updated = self.auto_service.authorize_launch()
            after_orders = len(self.repository.open_orders())
            if after_orders != before_orders:
                self.auto_service.quarantine("LAUNCH_CALLBACK_CREATED_ORDER")
                raise RuntimeError("최초 시작승인 처리 중 주문 상태가 바뀌어 안전격리했습니다")
            return (
                "✅ <b>[JH AUTO 최초 시작승인 완료]</b>\n\n"
                f"• 목표 자동원금 : <code>{_money(updated.target_principal)}</code>\n"
                f"• 현재 허용원금(1단계) : <code>{_money(updated.effective_principal)}</code>\n"
                "• 실제 BUY : <b>이 버튼에서는 0건</b>\n\n"
                "다음 독립 안전주기에서 계좌·원장·시장·가격·수량을 처음부터 다시 확인한 뒤에만 자동매수가 가능해집니다."
            )
        raise RuntimeError("지원하지 않는 확인 요청입니다")

    def _format_recent_cycles(self) -> str:
        rows = self.auto_service.recent_cycles(5)
        lines = ["📋 <b>[최근 JH AUTO 자동주문]</b>", ""]
        if not rows:
            lines.append("아직 자동주문 기록이 없습니다.")
            return "\n".join(lines)
        for row in rows:
            lines.append(
                f"• <b>{html.escape(str(row.get('symbol') or '-'))}</b> "
                f"<code>{row.get('filled_qty', 0)}/{row.get('requested_qty', 0)}주</code> · "
                f"<code>{html.escape(str(row.get('status') or '-'))}</code>"
            )
        return "\n".join(lines)

    def _register_handlers(self) -> None:
        super()._register_handlers()
        bot = self.bot

        @bot.message_handler(commands=["auto"])
        def auto_command(message):
            if not self._authorized_message(message):
                return
            parts = (message.text or "").split()
            try:
                if len(parts) == 1:
                    text, markup = self._format_auto_control()
                    self._send(text, markup=markup)
                    return
                if len(parts) == 3 and parts[1].lower() in {"capital", "ratio"}:
                    text, markup = self._review_change(parts[1].lower(), parts[2])
                    self._send(text, markup=markup)
                    return
                if len(parts) == 2 and parts[1].lower() == "start":
                    text, markup = self._review_change("start", "1")
                    self._send(text, markup=markup)
                    return
                raise ValueError("형식: /auto | /auto capital 50000 | /auto ratio 20 | /auto start")
            except Exception as exc:
                self._send(
                    "⛔ JH AUTO 요청을 처리하지 않았습니다.\n"
                    f"<code>{html.escape(telegram_bot_module._operator_error_summary(exc))}</code>"
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("auto_status|"))
        def auto_status_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            text, markup = self._format_auto_control()
            self._send(text, markup=markup)
            bot.answer_callback_query(call.id, "최신 상태를 확인했습니다.")

        @bot.callback_query_handler(func=lambda call: call.data.startswith("auto_recent|"))
        def auto_recent_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            self._send(self._format_recent_cycles())
            bot.answer_callback_query(call.id)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("auto_review|"))
        def auto_review_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, kind, value = call.data.split("|", 2)
                text, markup = self._review_change(kind, value)
                self._send(text, markup=markup)
                bot.answer_callback_query(call.id, "한 번 더 확인해 주세요.")
            except Exception as exc:
                bot.answer_callback_query(
                    call.id,
                    telegram_bot_module._operator_error_summary(exc),
                    show_alert=True,
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("auto_confirm|"))
        def auto_confirm_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, token = call.data.split("|", 1)
                result = self._confirm_pending(token)
                self._clear_callback_markup(call)
                self._send(result)
                bot.answer_callback_query(call.id, "적용했습니다.")
            except Exception as exc:
                bot.answer_callback_query(
                    call.id,
                    telegram_bot_module._operator_error_summary(exc),
                    show_alert=True,
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("auto_cancel|"))
        def auto_cancel_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            _, token = call.data.split("|", 1)
            self._auto_pending.pop(token, None)
            self._clear_callback_markup(call)
            bot.answer_callback_query(call.id, "취소했습니다.")

    def run(self) -> None:
        self.bot.set_my_commands(_auto_bot_commands())
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info(
            "JH AUTO %s Telegram polling 시작 (JDSS strategy 3.2.2)",
            AUTO_VERSION,
        )
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
