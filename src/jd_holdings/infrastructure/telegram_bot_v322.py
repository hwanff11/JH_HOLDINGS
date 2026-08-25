from __future__ import annotations

import html
import re
import threading

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


def _v322_bot_commands() -> list[telebot.types.BotCommand]:
    return [
        telebot.types.BotCommand("dashboard", "통합 대시보드"),
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


class V322TelegramBotApp(TelegramBotApp):
    """Production entry point for the JDSS V3.2.2 Telegram application."""

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(_v322_runtime_text(text), markup=markup, chat_id=chat_id)

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
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
                "🛒 매수 승인 대기 보기",
                callback_data="ops|signals",
            ),
            InlineKeyboardButton(
                "📋 미체결 주문 보기",
                callback_data="ops|orders",
            ),
        )
        return markup

    def _register_handlers(self) -> None:
        super()._register_handlers()
        bot = self.bot

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
                if action == "signals":
                    signals = self.trading_service.active_signals()
                    if not signals:
                        self._send(
                            "✅ <b>현재 매수 주문 승인 대기가 없습니다.</b>\n"
                            "목표비중과 실제 보유비중은 위 종목 버튼에서 확인할 수 있습니다."
                        )
                    else:
                        for signal in signals:
                            self._send_signal(signal)
                    answer = f"매수 주문 승인 대기 {len(signals)}건을 확인했습니다."
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

    def run(self) -> None:
        self.bot.set_my_commands(_v322_bot_commands())
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info("JDSS V3.2.2 Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
