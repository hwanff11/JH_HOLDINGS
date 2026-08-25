from __future__ import annotations

import html
import re
import secrets
import threading
import time
from dataclasses import dataclass
from decimal import Decimal

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

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
        telebot.types.BotCommand("signal", "개별 매수 주문 승인"),
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
            "위험축소 매도가 아직 끝나지 않아 새 매수는 자동으로 막혀 있습니다.",
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

    def _replace_active_batch(self, plan: _BatchBuyPlan | None) -> None:
        with self._buy_batch_lock:
            previous = self._active_buy_batch
            self._active_buy_batch = plan
        if previous is not None and (plan is None or previous.batch_id != plan.batch_id):
            self._cancel_batch_approvals(previous)

    def _prepare_today_buy_batch(self) -> tuple[str, InlineKeyboardMarkup | None]:
        barrier = _format_batch_barrier(self.repository.open_orders())
        if barrier is not None:
            self._replace_active_batch(None)
            return barrier, None
        if self._portfolio_safe_mode():
            self._replace_active_batch(None)
            return (
                "🚨 <b>[오늘 주문 중단]</b>\n\n"
                "SAFE_MODE가 활성화되어 새 매수를 만들지 않습니다. "
                "<code>/errors</code>와 정합성 상태를 먼저 확인하세요.",
                None,
            )

        rank = {symbol: index for index, symbol in enumerate(ALLOCATION_SYMBOLS)}
        signals = sorted(
            self.trading_service.active_signals(),
            key=lambda item: rank.get(str(item["symbol"]), len(rank)),
        )
        if not signals:
            self._replace_active_batch(None)
            return (
                "✅ <b>[오늘 주문]</b>\n\n"
                "현재 선행 매도나 새 매수 승인 대기가 없습니다.\n"
                "오늘 목표비중에 맞춘 추가 주문이 없는 상태입니다.",
                None,
            )

        self._replace_active_batch(None)
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
        except Exception:
            temporary = _BatchBuyPlan(
                batch_id="failed",
                created_monotonic=time.monotonic(),
                items=tuple(items),
            )
            self._cancel_batch_approvals(temporary)
            raise

        total = sum((item.estimated_total for item in items), Decimal("0"))
        cash = (
            Decimal(str(self.portfolio_service.snapshot().get("cash", 0)))
            if self.portfolio_service is not None
            else Decimal("0")
        )
        if self.portfolio_service is not None and total > cash:
            temporary = _BatchBuyPlan(
                batch_id="insufficient-cash",
                created_monotonic=time.monotonic(),
                items=tuple(items),
            )
            self._cancel_batch_approvals(temporary)
            return (
                "⚠️ <b>[오늘 주문 중단]</b>\n\n"
                f"전체 예상 매수금액 <code>{telegram_bot_module._money(total)}</code>이 "
                f"현재 JDSS 현금 <code>{telegram_bot_module._money(cash)}</code>을 초과합니다.\n"
                "개별 주문을 임의로 줄이지 않고 목표비중을 다시 계산하도록 중단했습니다.",
                None,
            )

        plan = _BatchBuyPlan(
            batch_id=secrets.token_hex(4),
            created_monotonic=time.monotonic(),
            items=tuple(items),
        )
        self._replace_active_batch(plan)

        lines = [
            "🧾 <b>[오늘 주문 한번에 검토]</b>",
            "",
            "• 선행 위험축소 매도 : ✅ 미체결 SELL 없음",
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
            ]
        )
        if self.portfolio_service is not None:
            lines.append(
                f"💵 실행 전 현금 : <code>{telegram_bot_module._money(cash)}</code> · "
                f"예상 잔여 <code>{telegram_bot_module._money(max(Decimal('0'), cash - total))}</code>"
            )
        lines.extend(
            [
                "",
                f"⏰ <i>{self.config.global_.execution_token_ttl_seconds}초 안에 최종 실행해야 합니다. "
                "실행 버튼을 누르는 순간 각 종목의 가격·수량·현금·세션을 다시 검증합니다.</i>",
            ]
        )

        action = "모의매수" if self.settings.trading_mode == "dry_run" else "실매수"
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                f"✅ 오늘 {action} {len(plan.items)}건 전체 실행",
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

    def _take_batch(self, batch_id: str) -> _BatchBuyPlan | None:
        with self._buy_batch_lock:
            plan = self._active_buy_batch
            if plan is None or plan.batch_id != batch_id:
                return None
            self._active_buy_batch = None
            return plan

    def _execute_today_buy_batch(self, batch_id: str) -> str:
        plan = self._take_batch(batch_id)
        if plan is None:
            return (
                "⚠️ <b>[오늘 주문]</b>\n\n"
                "이미 사용·취소됐거나 새 검토로 교체된 버튼입니다. "
                "대시보드에서 다시 검토해 주세요."
            )

        ttl = self.config.global_.execution_token_ttl_seconds
        if time.monotonic() - plan.created_monotonic > ttl:
            self._cancel_batch_approvals(plan)
            return (
                "⏰ <b>[오늘 주문 만료]</b>\n\n"
                "최종 실행 시간이 지나 모든 매수 승인을 취소했습니다. "
                "최신 가격으로 다시 검토해 주세요."
            )

        barrier = _format_batch_barrier(self.repository.open_orders())
        if barrier is not None:
            self._cancel_batch_approvals(plan)
            return barrier
        if self._portfolio_safe_mode():
            self._cancel_batch_approvals(plan)
            return (
                "🚨 <b>[오늘 주문 중단]</b>\n\n"
                "검토 후 SAFE_MODE가 활성화되어 전체 매수를 취소했습니다."
            )

        receipts = []
        failure: Exception | None = None
        failure_index: int | None = None
        for index, item in enumerate(plan.items):
            try:
                receipt = self.trading_service.execute(
                    item.execution_approval_id,
                    item.execution_token,
                )
                receipts.append(receipt)
                if receipt.status in {"UNKNOWN", "CANCELED", "REJECTED", "REPLACED"}:
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
                items=plan.items[failure_index + 1 :],
            )
            self._cancel_batch_approvals(remaining)

        mode_text = "모의매수" if self.settings.trading_mode == "dry_run" else "실매수"
        lines = [f"✅ <b>[오늘 {mode_text} 처리 결과]</b>", ""]
        for receipt in receipts:
            lines.append(
                f"• <b>{html.escape(receipt.symbol)}</b> "
                f"<code>{telegram_bot_module._quantity(receipt.filled_quantity)} / "
                f"{telegram_bot_module._quantity(receipt.quantity)}주</code> · "
                f"<code>{html.escape(receipt.status)}</code> · "
                f"주문 <code>{html.escape(receipt.broker_order_id)}</code>"
            )
        if not receipts:
            lines.append("• 제출 완료된 매수 주문 없음")

        if failure is None:
            lines.extend(
                [
                    "",
                    "✅ 검토한 매수 주문을 모두 제출했습니다.",
                    "미체결 주문은 <code>미체결 주문 보기</code>에서 계속 확인할 수 있습니다.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "⚠️ <b>중간에 조건이 바뀌어 남은 매수는 자동 중단했습니다.</b>",
                    f"<code>{html.escape(str(failure))}</code>",
                    "앞에서 이미 제출된 주문은 되돌리지 않습니다. "
                    "미체결 상태를 확인한 뒤 다시 ‘오늘 주문 한번에 검토’를 눌러 주세요.",
                ]
            )
        return "\n".join(lines)

    def _cancel_today_buy_batch(self, batch_id: str) -> str:
        plan = self._take_batch(batch_id)
        if plan is None:
            return "이미 사용·취소됐거나 새 검토로 교체된 버튼입니다."
        self._cancel_batch_approvals(plan)
        return "오늘 매수 승인을 모두 취소했습니다."

    def _send_today_order_review(self) -> None:
        text, markup = self._prepare_today_buy_batch()
        self._send(text, markup=markup)

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
                    answer = "오늘 주문을 검토했습니다."
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
                    answer = "오늘 매수 묶음을 처리했습니다."
                elif action == "cancel":
                    answer = self._cancel_today_buy_batch(batch_id)
                    self._send(f"❌ <b>[오늘 주문 취소]</b>\n\n{html.escape(answer)}")
                else:
                    raise ValueError("지원하지 않는 일괄 주문 동작입니다.")
                bot.answer_callback_query(call.id, answer)
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("오늘 일괄 주문 처리 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)
            finally:
                self._clear_callback_markup(call)

    def run(self) -> None:
        self.bot.set_my_commands(_v322_bot_commands())
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info("JDSS V3.2.2 Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
