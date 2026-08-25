from __future__ import annotations

import html
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings.application.managed_account import available_managed_cash
from jd_holdings.application.portfolio_service import TARGET_QTY_GENERATION_KEY
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


@dataclass(frozen=True)
class _BatchBuyItem:
    execution_approval_id: int
    execution_token: str
    symbol: str
    quantity: int
    limit_price: Decimal
    estimated_fee: Decimal

    @property
    def estimated_total(self) -> Decimal:
        return Decimal(self.quantity) * self.limit_price + self.estimated_fee


@dataclass(frozen=True)
class _BatchBuyPlan:
    batch_id: str
    created_monotonic: float
    trade_date: str
    items: tuple[_BatchBuyItem, ...]


def _v322_bot_commands() -> list[telebot.types.BotCommand]:
    return [
        telebot.types.BotCommand("dashboard", "통합 대시보드"),
        telebot.types.BotCommand("today", "오늘 주문 한번에 검토"),
        telebot.types.BotCommand("portfolio", "목표비중·위험한도 현황"),
        telebot.types.BotCommand("onboarding", "최초진입 50→75→100 매수"),
        telebot.types.BotCommand("account", "토스 계좌 잔고"),
        telebot.types.BotCommand("status", "종목별 목표·보유 상세"),
        telebot.types.BotCommand("score", "TQQQ/SOXL 추가매수 판단"),
        telebot.types.BotCommand("history", "최근 추가매수 점수"),
        telebot.types.BotCommand("signal", "매수 주문 승인 대기"),
        telebot.types.BotCommand("backtest", "최초진입 포함 백테스트"),
        telebot.types.BotCommand("guide", "V3.2.2 전략·최초진입 설명"),
        telebot.types.BotCommand("order", "미체결 주문 현황"),
        telebot.types.BotCommand("errors", "최근 시스템 기록"),
        telebot.types.BotCommand("ping", "봇 상태 확인"),
        telebot.types.BotCommand("help", "메뉴 안내"),
    ]


def _v322_runtime_text(text: str) -> str:
    replacements = (
        ("JDSS V3.0 MONTHLY_H05", "JDSS V3.2.2 RS6M-HWM75"),
        ("JDSS V3 MONTHLY_H05", "JDSS V3.2.2 RS6M-HWM75"),
        ("JDSS V3.1.1 TWIN-H40-S3", "JDSS V3.2.2 RS6M-HWM75"),
        ("V3.1.1 TWIN-H40-S3 포트폴리오", "V3.2.2 QQQ/TQQQ/SOXL 포트폴리오"),
        ("JDSS H40-S3 부스터", "JDSS 5% 추가 레버리지"),
        ("목표 / 현재 배분", "목표 / 현재 보유비중"),
        ("목표 / 현재 비중", "목표 / 현재 보유비중"),
        ("V3.2.2 allocation 위험증가 BUY", "V3.2.2 목표비중 맞추기 · 매수 필요"),
        ("V3.2.2 allocation BUY 승인 대기", "V3.2.2 목표비중 매수 승인 대기"),
        ("V3.2.2 allocation", "V3.2.2 목표비중 조정"),
        ("종목별 allocation 원장", "종목별 목표·보유 상세"),
        ("allocation 체결", "목표비중 조정 체결"),
        ("최근 allocation 체결", "최근 목표비중 조정 체결"),
        ("5% 가상 오버레이 입력", "TQQQ/SOXL 추가매수 판단"),
        ("5% 가상 오버레이 분석", "TQQQ/SOXL 추가매수 판단"),
        ("5% 가상 오버레이", "5% 추가 레버리지"),
        ("가상 오버레이 점수", "추가매수 판단 점수"),
        ("가상 오버레이", "추가 레버리지"),
        ("오버레이 신호", "추가 레버리지 판단"),
        ("위험증가 BUY 승인 대기", "매수 주문 승인 대기"),
        ("SGOV는 V3.1.1 계약에서 사용하지 않습니다.", "SGOV는 V3.2.2 계약에서도 사용하지 않습니다."),
        ("SGOV 추정수익", "SGOV 운용수익(OFF)"),
        ("SGOV수익", "SGOV수익(OFF)"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"^.*<code>/sgov</code>.*\n?", "", text, flags=re.MULTILINE)
    return text


def _format_open_orders_shortcut(values: list[dict]) -> str:
    if not values:
        return "✅ <b>[미체결 주문]</b>\n\n현재 대기 중인 주문이 없습니다."
    lines = ["📋 <b>[미체결 주문]</b>", ""]
    for item in values:
        symbol = html.escape(str(item["symbol"]))
        side = "매수" if str(item["side"]).upper() == "BUY" else "매도"
        quantity = telegram_bot_module._quantity(item["qty"])
        price = (
            telegram_bot_module._money(item["price"])
            if item.get("price")
            else "시장가"
        )
        status = html.escape(str(item["status"]))
        lines.append(
            f"• <b>{symbol}</b> {side} <code>{quantity}주</code> · "
            f"<code>{price}</code> · <code>{status}</code>"
        )
    return "\n".join(lines)


def _format_batch_barrier(orders: list[dict]) -> str | None:
    if not orders:
        return None
    sell_orders = [item for item in orders if str(item["side"]).upper() == "SELL"]
    if sell_orders:
        lines = [
            "⏳ <b>[오늘 주문 · 매도 완료 대기]</b>",
            "",
            "매도 또는 현금화 주문이 아직 끝나지 않아 새 매수는 자동으로 막혀 있습니다.",
        ]
        for item in sell_orders:
            lines.append(
                f"• <b>{html.escape(str(item['symbol']))}</b> "
                f"{telegram_bot_module._quantity(item['filled_qty'])}/"
                f"{telegram_bot_module._quantity(item['qty'])}주 · "
                f"<code>{html.escape(str(item['status']))}</code>"
            )
        lines.extend(
            [
                "",
                "매도가 끝나고 브로커/원장 정합성이 확인되면 "
                "다음 점검에서 매수 후보가 자동으로 열립니다.",
            ]
        )
        return "\n".join(lines)

    return (
        "⏳ <b>[오늘 주문 · 기존 매수 진행 중]</b>\n\n"
        "이미 제출된 매수 주문이 있어 중복 주문을 막았습니다.\n"
        "<code>미체결 주문 보기</code>에서 먼저 상태를 확인하세요."
    )


class V322TelegramBotApp(TelegramBotApp):
    """Production entry point for the JDSS V3.2.2 Telegram application."""

    def __init__(self, *args, **kwargs) -> None:
        self._buy_batch_lock = threading.RLock()
        self._active_buy_batch: _BatchBuyPlan | None = None
        super().__init__(*args, **kwargs)

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(_v322_runtime_text(text), markup=markup, chat_id=chat_id)

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.row(
            InlineKeyboardButton(
                "🧾 오늘 주문 한번에 검토",
                callback_data="ops|today",
            )
        )
        for symbol in ALLOCATION_SYMBOLS:
            buttons = [
                InlineKeyboardButton(
                    f"📊 {symbol} 보유/목표",
                    callback_data=f"status|{symbol}",
                )
            ]
            if symbol in self.config.enabled_symbols:
                buttons.append(
                    InlineKeyboardButton(
                        f"🎯 {symbol} 추가매수 판단",
                        callback_data=f"score|{symbol}",
                    )
                )
            markup.row(*buttons)
        markup.row(
            InlineKeyboardButton(
                "🛒 개별 매수 보기",
                callback_data="ops|signals",
            ),
            InlineKeyboardButton(
                "📋 미체결 주문 보기",
                callback_data="ops|orders",
            ),
        )
        return markup

    def _portfolio_safe_mode(self) -> bool:
        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            return True
        for symbol in self.config.enabled_symbols:
            state = self.repository.get_position(symbol).state
            value = state.value if hasattr(state, "value") else str(state)
            if value == "SAFE_MODE":
                return True
        return False

    def _log_batch_event(
        self,
        severity: str,
        event_type: str,
        message: str,
        *,
        context: dict | None = None,
    ) -> None:
        try:
            self.repository.log_event(
                severity,
                event_type,
                message,
                context=context or {},
            )
        except Exception:
            telegram_bot_module.LOGGER.exception("batch 주문 감사로그 기록 실패")

    def _cancel_batch_approvals(self, plan: _BatchBuyPlan | None) -> None:
        if plan is None:
            return
        for item in plan.items:
            try:
                self.trading_service.cancel_approval(item.execution_approval_id)
            except Exception:
                telegram_bot_module.LOGGER.debug(
                    "batch approval cancel skipped",
                    exc_info=True,
                )

    def _active_batch_message(self) -> str | None:
        plan = self._active_buy_batch
        if plan is None:
            return None
        ttl = self.config.global_.execution_token_ttl_seconds
        if time.monotonic() - plan.created_monotonic <= ttl:
            return (
                "⏳ <b>[오늘 주문 · 이미 검토 중]</b>\n\n"
                "이미 최신 일괄 매수 검토가 열려 있습니다. "
                "기존 메시지의 <b>순차 실행</b> 또는 <b>취소</b> 버튼을 사용해 주세요.\n"
                "새 검토가 필요하면 기존 묶음을 취소한 뒤 다시 눌러 주세요."
            )
        self._active_buy_batch = None
        self._cancel_batch_approvals(plan)
        self._log_batch_event(
            "INFO",
            "BATCH_BUY_EXPIRED",
            "일괄 매수 최종승인이 만료되어 기존 승인을 정리했습니다",
            context={"batch_id": plan.batch_id, "trade_date": plan.trade_date},
        )
        return None

    def _freshness_barrier(self, current: datetime) -> str | None:
        try:
            completed = self.market_clock.latest_completed_session(
                current,
                delay_minutes=self.config.scheduler.signal_delay_minutes,
            )
        except Exception as exc:
            self._log_batch_event(
                "ERROR",
                "BATCH_BUY_MARKET_CLOCK_FAILED",
                "오늘 주문 검토 중 최신 완결 거래일을 확인하지 못했습니다",
                context={"exception": type(exc).__name__},
            )
            return (
                "⚠️ <b>[오늘 주문 · 거래일 확인 실패]</b>\n\n"
                "최신 완결 거래일을 확인하지 못해 주문 없음으로 판단하지 않습니다.\n"
                "<code>/errors</code>를 확인하고 다음 자동 점검 뒤 다시 시도해 주세요."
            )

        marker = self.repository.get_system_value("last_v322_allocation_trade_date")
        if marker != completed.isoformat():
            return (
                "⚠️ <b>[오늘 주문 · 전략 계산 확인 필요]</b>\n\n"
                f"최신 완결봉은 <code>{completed.isoformat()}</code>인데 "
                f"전략 계산 완료 표시는 <code>{html.escape(marker or '없음')}</code>입니다.\n"
                "전략 계산 실패·지연을 주문 없음으로 오인하지 않도록 새 주문을 막았습니다."
            )

        cores = list(self.repository.core_positions())
        generations = {
            str(core["signal_trade_date"])
            for core in cores
            if core.get("signal_trade_date")
        }
        if len(generations) != 1:
            return (
                "⚠️ <b>[오늘 주문 · 목표 상태 확인 필요]</b>\n\n"
                "QQQ/TQQQ/SOXL의 목표 기준일이 하나로 일치하지 않아 새 매수를 막았습니다.\n"
                "<code>/status</code>와 <code>/errors</code>를 확인해 주세요."
            )

        generation = next(iter(generations))
        target_generation = self.repository.get_system_value(TARGET_QTY_GENERATION_KEY)
        if target_generation == generation:
            return None

        try:
            generation_date = datetime.fromisoformat(generation).date()
            session_started = self.market_clock.next_session_has_started(
                generation_date,
                current,
            )
        except Exception as exc:
            self._log_batch_event(
                "ERROR",
                "BATCH_BUY_TARGET_GENERATION_CHECK_FAILED",
                "다음 거래일 시작 여부를 확인하지 못했습니다",
                context={"generation": generation, "exception": type(exc).__name__},
            )
            return (
                "⚠️ <b>[오늘 주문 · 주문시점 확인 실패]</b>\n\n"
                "고정 목표수량을 만들 수 있는 거래시점인지 확인하지 못해 새 매수를 막았습니다."
            )

        if not session_started:
            return (
                "🕒 <b>[오늘 주문 · 다음 미국 거래일 대기]</b>\n\n"
                f"전략 계산은 <code>{marker}</code> 기준으로 완료됐습니다.\n"
                "다만 목표비중이 새로 바뀌어 실제 고정 주문수량은 "
                "<b>다음 미국 거래일이 시작된 뒤</b> 확정됩니다.\n"
                "현재는 주문 없음이 아니라 <b>주문 준비 대기</b> 상태입니다."
            )

        return (
            "⏳ <b>[오늘 주문 · 목표수량 생성 대기]</b>\n\n"
            "다음 미국 거래일은 시작됐지만 새 목표수량 생성이 아직 완료되지 않았습니다.\n"
            "자동 점검이 목표수량을 확정할 때까지 새 매수를 막았습니다."
        )

    def _no_signal_message(self) -> str:
        cores = list(self.repository.core_positions())
        needs_sell = [
            str(core["symbol"])
            for core in cores
            if int(core.get("target_qty") or 0) < int(core.get("qty") or 0)
        ]
        needs_buy = [
            str(core["symbol"])
            for core in cores
            if int(core.get("target_qty") or 0) > int(core.get("qty") or 0)
        ]
        if needs_sell:
            return (
                "⏳ <b>[오늘 주문 · 위험축소 준비 중]</b>\n\n"
                f"목표보다 많이 보유한 종목이 있습니다: "
                f"<code>{', '.join(needs_sell)}</code>\n"
                "자동 SELL이 아직 생성되지 않은 상태이므로 주문 없음으로 판단하지 않습니다."
            )
        if needs_buy:
            return (
                "⏳ <b>[오늘 주문 · 매수신호 생성 대기]</b>\n\n"
                f"목표보다 부족한 종목이 있습니다: "
                f"<code>{', '.join(needs_buy)}</code>\n"
                "매수 승인 신호가 아직 생성되지 않아 다음 자동 점검을 기다립니다."
            )
        return (
            "✅ <b>[오늘 주문]</b>\n\n"
            "최신 전략 계산·고정 목표수량·정합성을 모두 확인했습니다.\n"
            "현재 목표수량과 실제 보유수량이 일치해 추가 주문이 없습니다."
        )

    def _reconciliation_barrier(self, *, phase: str) -> str | None:
        try:
            mismatches = self.reconciliation_service.run()
        except Exception as exc:
            self._log_batch_event(
                "ERROR",
                "BATCH_BUY_RECONCILIATION_ERROR",
                f"{phase} 정합성 확인 중 오류가 발생했습니다",
                context={"phase": phase, "exception": type(exc).__name__},
            )
            return (
                "🚨 <b>[오늘 주문 · 정합성 확인 실패]</b>\n\n"
                "실제 계좌와 JDSS 원장을 즉시 대조하지 못해 새 매수를 차단했습니다.\n"
                "<code>/errors</code>를 확인해 주세요."
            )
        if mismatches or self._portfolio_safe_mode():
            symbols = ", ".join(sorted(mismatches)) if mismatches else "SAFE_MODE"
            return (
                "🚨 <b>[오늘 주문 · 계좌/원장 불일치]</b>\n\n"
                f"즉시 정합성 점검에서 <code>{html.escape(symbols)}</code> 문제가 확인되어 "
                "새 매수를 차단했습니다.\n"
                "<code>/errors</code>에서 원인을 먼저 확인해 주세요."
            )
        return None

    def _managed_buy_capacity(self) -> Decimal:
        return available_managed_cash(
            self.config,
            self.repository,
            self.trading_service.broker,
        )

    def _batch_requirement(self, items: tuple[_BatchBuyItem, ...]) -> Decimal:
        total = sum((item.estimated_total for item in items), Decimal("0"))
        idle_cash = getattr(self.config, "idle_cash", None)
        if idle_cash is not None and getattr(idle_cash, "enabled", False):
            total += Decimal(str(idle_cash.cash_buffer))
        return total

    def _render_batch_review(
        self,
        plan: _BatchBuyPlan,
        *,
        cash: Decimal,
        capacity: Decimal,
    ) -> tuple[str, InlineKeyboardMarkup]:
        total = sum((item.estimated_total for item in plan.items), Decimal("0"))
        lines = [
            "🧾 <b>[오늘 주문 한번에 검토]</b>",
            "",
            f"• 전략 기준일 : <code>{html.escape(plan.trade_date)}</code>",
            "• 선행 매도 : ✅ 미체결 SELL 없음",
            "• 계좌/원장 정합성 : ✅ 즉시 확인 완료",
            "• SAFE_MODE : ✅ 정상",
            "",
            "📈 <b>매수 예정</b>",
        ]
        for item in plan.items:
            lines.append(
                f"• <b>{html.escape(item.symbol)}</b> "
                f"<code>{telegram_bot_module._quantity(item.quantity)}주</code> · "
                f"지정가 <code>{telegram_bot_module._money(item.limit_price)}</code> 이하 · "
                f"약 <code>{telegram_bot_module._money(item.estimated_total)}</code>"
            )
        lines.extend(
            [
                "",
                f"💵 총 예상 매수 : <code>{telegram_bot_module._money(total)}</code>",
                f"🛡️ 현재 HWM75 매수가능한도 : <code>{telegram_bot_module._money(capacity)}</code>",
                f"💵 현재 JDSS 현금 : <code>{telegram_bot_module._money(cash)}</code>",
                "",
                "⚠️ <b>각 종목은 개별 주문으로 순서대로 제출됩니다.</b> "
                "앞 주문 뒤 가격·수량·상태가 바뀌면 일부 주문만 제출되고 나머지는 자동 중단될 수 있습니다.",
                "",
                f"⏰ <i>{self.config.global_.execution_token_ttl_seconds}초 안에 실행해야 하며, "
                "최종 버튼을 누르면 계좌 정합성과 각 종목 가격·수량·현금·세션을 다시 검증합니다.</i>",
            ]
        )

        action = "모의매수" if self.settings.trading_mode == "dry_run" else "실매수"
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                f"✅ 오늘 {action} {len(plan.items)}건 순차 실행",
                callback_data=f"batch|exec|{plan.batch_id}",
            )
        )
        markup.add(
            InlineKeyboardButton(
                "❌ 오늘 매수 취소",
                callback_data=f"batch|cancel|{plan.batch_id}",
            )
        )
        return "\n".join(lines), markup

    def _prepare_today_buy_batch(self) -> tuple[str, InlineKeyboardMarkup | None]:
        with self._buy_batch_lock:
            active_message = self._active_batch_message()
            if active_message is not None:
                return active_message, None

            current = datetime.now(UTC)
            freshness = self._freshness_barrier(current)
            if freshness is not None:
                return freshness, None

            barrier = _format_batch_barrier(self.repository.open_orders())
            if barrier is not None:
                return barrier, None

            reconciliation = self._reconciliation_barrier(phase="일괄 검토 전")
            if reconciliation is not None:
                return reconciliation, None

            rank = {symbol: index for index, symbol in enumerate(ALLOCATION_SYMBOLS)}
            signals = sorted(
                self.trading_service.active_signals(),
                key=lambda item: rank.get(str(item["symbol"]), len(rank)),
            )
            if not signals:
                return self._no_signal_message(), None

            items: list[_BatchBuyItem] = []
            try:
                for signal in signals:
                    review_id, review_token = self.trading_service.create_review_approval(
                        int(signal["signal_id"])
                    )
                    quote = self.trading_service.consume_review(review_id, review_token)
                    if quote.execution_approval_id is None or quote.execution_token is None:
                        raise RuntimeError("최종 매수 승인 토큰을 만들지 못했습니다")
                    items.append(
                        _BatchBuyItem(
                            execution_approval_id=int(quote.execution_approval_id),
                            execution_token=str(quote.execution_token),
                            symbol=str(quote.symbol),
                            quantity=int(quote.quantity),
                            limit_price=Decimal(str(quote.limit_price)),
                            estimated_fee=Decimal(str(quote.estimated_fee)),
                        )
                    )
            except Exception as exc:
                temporary = _BatchBuyPlan(
                    batch_id="failed",
                    created_monotonic=time.monotonic(),
                    trade_date=self.repository.get_system_value(
                        "last_v322_allocation_trade_date"
                    )
                    or "-",
                    items=tuple(items),
                )
                self._cancel_batch_approvals(temporary)
                self._log_batch_event(
                    "ERROR",
                    "BATCH_BUY_REVIEW_FAILED",
                    "일괄 매수 검토 생성 중 오류가 발생해 생성된 최종승인을 정리했습니다",
                    context={
                        "created_execution_approvals": len(items),
                        "exception": type(exc).__name__,
                    },
                )
                raise

            tuple_items = tuple(items)
            requirement = self._batch_requirement(tuple_items)
            capacity = self._managed_buy_capacity()
            cash = (
                Decimal(str(self.portfolio_service.snapshot().get("cash", 0)))
                if self.portfolio_service is not None
                else Decimal("0")
            )
            if requirement > capacity:
                temporary = _BatchBuyPlan(
                    batch_id="insufficient-capacity",
                    created_monotonic=time.monotonic(),
                    trade_date=self.repository.get_system_value(
                        "last_v322_allocation_trade_date"
                    )
                    or "-",
                    items=tuple_items,
                )
                self._cancel_batch_approvals(temporary)
                self._log_batch_event(
                    "WARNING",
                    "BATCH_BUY_CAPACITY_BLOCKED",
                    "일괄 매수 합계가 HWM75 관리 매수가능한도를 초과해 중단했습니다",
                    context={
                        "required": str(requirement),
                        "available": str(capacity),
                    },
                )
                return (
                    "⚠️ <b>[오늘 주문 · 전체 매수한도 부족]</b>\n\n"
                    f"전체 필요금액 <code>{telegram_bot_module._money(requirement)}</code>이 "
                    f"HWM75·현금·브로커 주문가능금액을 함께 반영한 실제 매수가능한도 "
                    f"<code>{telegram_bot_module._money(capacity)}</code>을 초과합니다.\n"
                    "일부 종목을 임의로 줄여 주문하지 않고 전체 검토를 중단했습니다.",
                    None,
                )

            plan = _BatchBuyPlan(
                batch_id=secrets.token_hex(4),
                created_monotonic=time.monotonic(),
                trade_date=self.repository.get_system_value(
                    "last_v322_allocation_trade_date"
                )
                or "-",
                items=tuple_items,
            )
            self._active_buy_batch = plan
            self._log_batch_event(
                "INFO",
                "BATCH_BUY_REVIEW_CREATED",
                "오늘 일괄 매수 검토를 생성했습니다",
                context={
                    "batch_id": plan.batch_id,
                    "trade_date": plan.trade_date,
                    "symbols": [item.symbol for item in plan.items],
                    "orders": len(plan.items),
                    "estimated_total": str(
                        sum((item.estimated_total for item in plan.items), Decimal("0"))
                    ),
                    "managed_capacity": str(capacity),
                },
            )
            return self._render_batch_review(plan, cash=cash, capacity=capacity)

    def _execute_today_buy_batch(self, batch_id: str) -> str:
        with self._buy_batch_lock:
            plan = self._active_buy_batch
            if plan is None or plan.batch_id != batch_id:
                return (
                    "⚠️ <b>[오늘 주문]</b>\n\n"
                    "이미 사용·취소·만료되었거나 서버 재시작으로 폐기된 버튼입니다. "
                    "대시보드에서 최신 주문을 다시 검토해 주세요."
                )
            self._active_buy_batch = None

            ttl = self.config.global_.execution_token_ttl_seconds
            if time.monotonic() - plan.created_monotonic > ttl:
                self._cancel_batch_approvals(plan)
                self._log_batch_event(
                    "INFO",
                    "BATCH_BUY_EXPIRED",
                    "일괄 매수 최종실행 시간이 만료되었습니다",
                    context={"batch_id": plan.batch_id, "trade_date": plan.trade_date},
                )
                return (
                    "⏰ <b>[오늘 주문 만료]</b>\n\n"
                    "최종 실행 시간이 지나 모든 매수 승인을 취소했습니다. "
                    "최신 가격으로 다시 검토해 주세요."
                )

            current = datetime.now(UTC)
            freshness = self._freshness_barrier(current)
            if freshness is not None:
                self._cancel_batch_approvals(plan)
                return freshness

            barrier = _format_batch_barrier(self.repository.open_orders())
            if barrier is not None:
                self._cancel_batch_approvals(plan)
                return barrier

            reconciliation = self._reconciliation_barrier(phase="최종 실행 직전")
            if reconciliation is not None:
                self._cancel_batch_approvals(plan)
                self._log_batch_event(
                    "WARNING",
                    "BATCH_BUY_RECONCILIATION_BLOCKED",
                    "최종 실행 직전 정합성 점검으로 일괄 매수를 차단했습니다",
                    context={"batch_id": plan.batch_id},
                )
                return reconciliation

            requirement = self._batch_requirement(plan.items)
            capacity = self._managed_buy_capacity()
            if requirement > capacity:
                self._cancel_batch_approvals(plan)
                self._log_batch_event(
                    "WARNING",
                    "BATCH_BUY_PREFLIGHT_CAPACITY_BLOCKED",
                    "최종 실행 직전 일괄 매수 합계가 매수가능한도를 초과했습니다",
                    context={
                        "batch_id": plan.batch_id,
                        "required": str(requirement),
                        "available": str(capacity),
                    },
                )
                return (
                    "⚠️ <b>[오늘 주문 · 실행 직전 한도 변경]</b>\n\n"
                    f"검토 뒤 매수가능한도가 <code>{telegram_bot_module._money(capacity)}</code>으로 "
                    "변해 전체 일괄 주문을 취소했습니다. 최신 조건으로 다시 검토해 주세요."
                )

            self._log_batch_event(
                "INFO",
                "BATCH_BUY_EXECUTION_STARTED",
                "일괄 매수 순차 제출을 시작했습니다",
                context={
                    "batch_id": plan.batch_id,
                    "trade_date": plan.trade_date,
                    "orders": len(plan.items),
                },
            )

            results: list[tuple[_BatchBuyItem, object]] = []
            failure: Exception | None = None
            failure_index: int | None = None
            for index, item in enumerate(plan.items):
                try:
                    receipt = self.trading_service.execute(
                        item.execution_approval_id,
                        item.execution_token,
                    )
                    results.append((item, receipt))
                    if receipt.status in {
                        "UNKNOWN",
                        "CANCELED",
                        "REJECTED",
                        "REPLACED",
                    }:
                        failure = RuntimeError(
                            f"{item.symbol} 주문 상태가 {receipt.status}여서 남은 매수를 중단했습니다"
                        )
                        failure_index = index
                        break
                except Exception as exc:
                    failure = exc
                    failure_index = index
                    break

            if failure_index is not None:
                remaining = _BatchBuyPlan(
                    batch_id=plan.batch_id,
                    created_monotonic=plan.created_monotonic,
                    trade_date=plan.trade_date,
                    items=plan.items[failure_index + 1 :],
                )
                self._cancel_batch_approvals(remaining)

            status = "SUCCESS" if failure is None else "PARTIAL_OR_FAILED"
            self._log_batch_event(
                "INFO" if failure is None else "WARNING",
                "BATCH_BUY_EXECUTION_FINISHED",
                f"일괄 매수 순차 제출이 {status} 상태로 종료됐습니다",
                context={
                    "batch_id": plan.batch_id,
                    "status": status,
                    "submitted": [
                        {
                            "symbol": item.symbol,
                            "status": str(receipt.status),
                            "filled_quantity": int(receipt.filled_quantity),
                            "quantity": int(receipt.quantity),
                            "broker_order_id": str(receipt.broker_order_id),
                        }
                        for item, receipt in results
                    ],
                    "failure": str(failure) if failure is not None else None,
                },
            )

            mode_text = (
                "모의매수" if self.settings.trading_mode == "dry_run" else "실매수"
            )
            title = (
                f"✅ <b>[오늘 {mode_text} 순차 제출 결과]</b>"
                if failure is None
                else f"⚠️ <b>[오늘 {mode_text} 부분 실행 결과]</b>"
            )
            lines = [title, ""]
            for item, receipt in results:
                lines.append(
                    f"• <b>{html.escape(item.symbol)}</b> "
                    f"<code>{telegram_bot_module._quantity(receipt.filled_quantity)} / "
                    f"{telegram_bot_module._quantity(receipt.quantity)}주</code> · "
                    f"<code>{html.escape(receipt.status)}</code> · "
                    f"주문 <code>{html.escape(receipt.broker_order_id)}</code>"
                )
            if not results:
                lines.append("• 제출 완료된 매수 주문 없음")

            if failure is None:
                lines.extend(
                    [
                        "",
                        "✅ 검토한 매수 주문을 모두 순서대로 제출했습니다.",
                        "미체결 주문은 <code>미체결 주문 보기</code>에서 계속 확인할 수 있습니다.",
                    ]
                )
            else:
                lines.extend(
                    [
                        "",
                        "⚠️ <b>중간에 조건 또는 주문상태가 바뀌어 남은 매수는 자동 중단했습니다.</b>",
                        f"<code>{html.escape(str(failure))}</code>",
                        "앞에서 이미 제출된 주문은 되돌리지 않습니다. "
                        "미체결 상태와 <code>/errors</code>의 batch 기록을 확인한 뒤 "
                        "다시 ‘오늘 주문 한번에 검토’를 눌러 주세요.",
                    ]
                )
            return "\n".join(lines)

    def _cancel_today_buy_batch(self, batch_id: str) -> str:
        with self._buy_batch_lock:
            plan = self._active_buy_batch
            if plan is None or plan.batch_id != batch_id:
                return (
                    "이미 사용·취소·만료되었거나 서버 재시작으로 폐기된 버튼입니다."
                )
            self._active_buy_batch = None
            self._cancel_batch_approvals(plan)
            self._log_batch_event(
                "INFO",
                "BATCH_BUY_CANCELED",
                "운영자가 일괄 매수 승인을 취소했습니다",
                context={"batch_id": plan.batch_id, "trade_date": plan.trade_date},
            )
            return "오늘 매수 승인을 모두 취소했습니다."

    def _send_today_order_review(self) -> None:
        text, markup = self._prepare_today_buy_batch()
        self._send(text, markup=markup)

    def notify_portfolio_buy_batch_ready(self, signal_ids: tuple[int, ...]) -> None:
        if not signal_ids:
            return
        symbols: list[str] = []
        for signal_id in signal_ids:
            try:
                signal = self.repository.get_signal(signal_id)
            except Exception:
                telegram_bot_module.LOGGER.warning(
                    "batch ready notification signal lookup failed: %s",
                    signal_id,
                    exc_info=True,
                )
                continue
            symbol = str(signal.get("symbol") or "")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            return
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "🧾 오늘 주문 한번에 검토",
                callback_data="ops|today",
            )
        )
        self._send(
            "🧾 <b>[오늘 매수 검토 가능]</b>\n\n"
            f"• 대상 : <code>{', '.join(html.escape(symbol) for symbol in symbols)}</code>\n"
            "• 위험축소 매도·정합성·HWM75 한도는 검토 버튼을 누를 때 다시 확인합니다.\n"
            "• 종목별 승인 카드를 반복해서 누를 필요 없이 아래 버튼에서 한 번에 검토할 수 있습니다.",
            markup=markup,
        )

    def _register_handlers(self) -> None:
        super()._register_handlers()
        bot = self.bot

        @bot.message_handler(commands=["today", "trade"])
        def today(message):
            if not self._authorized_message(message):
                return
            try:
                self._send_today_order_review()
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("오늘 주문 검토 실패")
                self._log_batch_event(
                    "ERROR",
                    "BATCH_BUY_REVIEW_UNHANDLED_ERROR",
                    "오늘 주문 검토 처리 중 예외가 발생했습니다",
                    context={"exception": type(exc).__name__},
                )
                self._send(
                    "❌ 오늘 주문을 검토하지 못했습니다.\n"
                    f"<code>{html.escape(str(exc))}</code>"
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("ops|"))
        def operator_shortcut_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(
                    call.id,
                    "권한이 없습니다.",
                    show_alert=True,
                )
                return
            try:
                _, action = call.data.split("|", 1)
                if action == "today":
                    self._send_today_order_review()
                    answer = "오늘 주문 상태를 확인했습니다."
                elif action == "signals":
                    signals = self.trading_service.active_signals()
                    if not signals:
                        self._send(
                            "✅ <b>현재 개별 매수 주문 승인이 없습니다.</b>\n"
                            "목표비중과 실제 보유비중은 위 종목 버튼에서 확인할 수 있습니다."
                        )
                    else:
                        for signal in signals:
                            self._send_signal(signal)
                    answer = f"개별 매수 승인 {len(signals)}건을 확인했습니다."
                elif action == "orders":
                    orders = self.repository.open_orders()
                    self._send(_format_open_orders_shortcut(orders))
                    answer = f"미체결 주문 {len(orders)}건을 확인했습니다."
                else:
                    raise ValueError("지원하지 않는 바로가기입니다.")
                bot.answer_callback_query(call.id, answer)
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("운영 바로가기 조회 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("batch|"))
        def batch_order_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(
                    call.id,
                    "권한이 없습니다.",
                    show_alert=True,
                )
                return
            try:
                _, action, batch_id = call.data.split("|", 2)
                if action == "exec":
                    self._send(self._execute_today_buy_batch(batch_id))
                    answer = "오늘 매수 순차 제출을 처리했습니다."
                elif action == "cancel":
                    answer = self._cancel_today_buy_batch(batch_id)
                    self._send(
                        f"❌ <b>[오늘 주문 취소]</b>\n\n{html.escape(answer)}"
                    )
                else:
                    raise ValueError("지원하지 않는 일괄 주문 동작입니다.")
                bot.answer_callback_query(call.id, answer)
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("오늘 일괄 주문 처리 실패")
                self._log_batch_event(
                    "ERROR",
                    "BATCH_BUY_CALLBACK_ERROR",
                    "일괄 주문 callback 처리 중 예외가 발생했습니다",
                    context={"exception": type(exc).__name__},
                )
                bot.answer_callback_query(call.id, str(exc), show_alert=True)
            finally:
                self._clear_callback_markup(call)

    def run(self) -> None:
        self.bot.set_my_commands(_v322_bot_commands())
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info("JDSS V3.2.2 Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
