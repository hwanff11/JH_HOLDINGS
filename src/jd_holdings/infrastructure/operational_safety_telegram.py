from __future__ import annotations

import html
import threading

import telebot

from jd_holdings.application.operational_safety import OperatorSafetyService

from . import telegram_bot as telegram_bot_module
from .initial_onboarding_telegram import InitialOnboardingTelegramBotApp
from .telegram_bot_v322 import _v322_bot_commands

RESUME_CONFIRMATION = "RESUME_BUYS"
RESUME_REVIEW_CALLBACK = "ops|resume|review"
RESUME_CONFIRM_CALLBACK = "ops|resume|confirm"


def _operator_bot_commands() -> list[telebot.types.BotCommand]:
    """Return the exact first-level menu used by the live operator app."""
    commands = _v322_bot_commands()
    onboarding_index = next(
        index for index, command in enumerate(commands) if command.command == "onboarding"
    )
    commands.insert(
        onboarding_index + 1,
        telebot.types.BotCommand("halt", "긴급 신규 매수 중지"),
    )
    return commands


def _live_mode_operator_text(text: str, trading_mode: str) -> str:
    """Remove dry-run-only guidance once the explicitly commissioned runtime is live."""
    if trading_mode != "live":
        return text
    return text.replace(
        "• 실거래는 잠겨 있고 forced dry-run만 허용합니다.",
        "• 실계좌가 연결되어 있으며 신규 매수는 운영자 잠금과 2단계 승인으로 통제합니다.",
    ).replace(
        "🧪 모의운용에서는 승인해도 실제 토스 주문이 전송되지 않습니다.",
        "🔥 실거래에서는 신규 매수 잠금 해제와 2단계 승인을 모두 통과해야 "
        "실제 토스 주문이 전송됩니다.",
    )


class OperationalSafetyTelegramBotApp(InitialOnboardingTelegramBotApp):
    """Add an operator circuit breaker without weakening automatic risk reduction."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.operational_safety = OperatorSafetyService(
            self.repository,
            self.trading_service.broker,
            self.reconciliation_service,
        )

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        text = _live_mode_operator_text(text, self.settings.trading_mode)
        halted = self.repository.get_system_value("operator_buy_halt") == "1"
        halt_state = "🚨 매수 잠금" if halted else "✅ 매수 가능"
        if "[JDSS V3.2.2 운영 대시보드]" in text:
            marker = "• <b>실거래</b> : 🔒 잠금"
            if marker in text:
                live_state = (
                    "🔥 LIVE 실계좌 연결"
                    if self.settings.trading_mode == "live"
                    else "🔒 모의운용"
                )
                text = text.replace(
                    marker,
                    f"• <b>실계좌 연결</b> : {live_state}\n"
                    f"• <b>신규 매수</b> : {halt_state}",
                    1,
                )
        if "[JH홀딩스 JDSS 봇 상태]" in text and self.settings.trading_mode == "live":
            text = text.replace(
                "• <b>실주문 잠금</b> : 🟢 해제 (실거래 가능)",
                f"• <b>실계좌 연결</b> : 🔥 실거래\n• <b>신규 매수</b> : {halt_state}",
                1,
            )
        super()._send(text, markup=markup, chat_id=chat_id)

    @staticmethod
    def _resume_review_markup():
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                "🔓 BUY 잠금 해제 검토",
                callback_data=RESUME_REVIEW_CALLBACK,
            )
        )
        return markup

    @staticmethod
    def _resume_confirm_markup():
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                "✅ 정말 BUY 잠금 해제",
                callback_data=RESUME_CONFIRM_CALLBACK,
            )
        )
        return markup

    def _resume_buy(self) -> None:
        result = self.operational_safety.resume()
        if not result["resumed"]:
            self._send("✅ BUY 긴급정지는 이미 해제되어 있습니다.")
            return
        self._send(
            "✅ <b>[BUY 긴급정지 해제]</b>\n\n"
            "브로커/원장 정합성 및 미체결 BUY 검사를 통과했습니다.\n"
            "이제 신규 BUY 승인 절차를 진행할 수 있습니다.\n"
            "⚠️ 실제 주문은 여전히 기존 JDSS 2단계 승인 후에만 제출됩니다."
        )

    def _register_handlers(self) -> None:
        super()._register_handlers()
        bot = self.bot

        @bot.message_handler(commands=["halt"])
        def halt(message):
            if not self._authorized_message(message):
                return
            try:
                result = self.operational_safety.halt()
                lines = [
                    "🚨 <b>[운영자 BUY 긴급정지]</b>",
                    "",
                    "신규 BUY를 즉시 차단했습니다.",
                    "SELL·주문감시·브로커/원장 정합성 점검은 계속 동작합니다.",
                    f"• 취소 요청 BUY : <code>{len(result.canceled_order_ids)}건</code>",
                    f"• 상태 확인 필요 : <code>{len(result.uncertain_order_ids)}건</code>",
                    f"• 폐기한 승인 : <code>{result.canceled_approvals}건</code>",
                ]
                if result.uncertain_order_ids:
                    lines.extend(
                        [
                            "",
                            "⚠️ 일부 BUY 주문의 취소 결과를 확정할 수 없습니다.",
                            "<code>/order</code>와 <code>/errors</code>를 확인해 주세요.",
                        ]
                    )
                lines.extend(
                    [
                        "",
                        "차단 해제는 정합성 검증 후에만 가능합니다.",
                        "메뉴의 <code>/resume</code>을 누르면 2단계 확인 버튼이 표시됩니다.",
                        f"수동 명령: <code>/resume {RESUME_CONFIRMATION}</code>",
                    ]
                )
                self._send("\n".join(lines))
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("운영자 BUY 긴급정지 실패")
                self._send(
                    "❌ BUY 긴급정지 처리 중 오류가 발생했습니다.\n"
                    "안전을 위해 서버의 차단 상태를 직접 확인해 주세요.\n"
                    f"<code>{html.escape(telegram_bot_module._operator_error_summary(exc))}</code>"
                )

        @bot.message_handler(commands=["resume"])
        def resume(message):
            if not self._authorized_message(message):
                return
            parts = (message.text or "").split()
            if len(parts) == 2 and parts[1] == RESUME_CONFIRMATION:
                try:
                    self._resume_buy()
                except Exception as exc:
                    telegram_bot_module.LOGGER.exception("운영자 BUY 차단 해제 거부")
                    self._send(
                        "⛔ <b>[BUY 차단 해제 거부]</b>\n\n"
                        "안전조건을 충족하지 못해 긴급정지를 유지합니다.\n"
                        f"<code>{html.escape(telegram_bot_module._operator_error_summary(exc))}</code>"
                    )
                return
            self._send(
                "🔒 <b>[BUY 잠금 해제]</b>\n\n"
                "실계좌의 신규 BUY를 허용하는 작업입니다.\n"
                "다음 단계에서 브로커/원장 정합성과 미체결 BUY를 자동 검사합니다.\n"
                "계속하려면 아래 버튼을 누르세요.",
                markup=self._resume_review_markup(),
            )

        @bot.callback_query_handler(func=lambda call: call.data == RESUME_REVIEW_CALLBACK)
        def resume_review_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            self._clear_callback_markup(call)
            bot.answer_callback_query(call.id, "최종 확인 단계입니다.")
            self._send(
                "⚠️ <b>[최종 확인 · BUY 잠금 해제]</b>\n\n"
                "이 버튼을 누르면 안전조건 통과 후 실제 BUY 주문을 승인할 수 있는 상태가 됩니다.\n"
                "자동 주문이 즉시 나가지는 않으며, 각 BUY는 기존 2단계 주문 승인이 추가로 필요합니다.\n\n"
                "정말 해제하려면 아래 버튼을 누르세요.",
                markup=self._resume_confirm_markup(),
            )

        @bot.callback_query_handler(func=lambda call: call.data == RESUME_CONFIRM_CALLBACK)
        def resume_confirm_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                self._resume_buy()
                self._clear_callback_markup(call)
                bot.answer_callback_query(call.id, "BUY 잠금 해제 검증을 완료했습니다.")
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("운영자 BUY 버튼 해제 거부")
                bot.answer_callback_query(
                    call.id,
                    telegram_bot_module._operator_error_summary(exc),
                    show_alert=True,
                )

    def run(self) -> None:
        self.bot.set_my_commands(_operator_bot_commands())
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info(
            "JDSS V3.2.2 Telegram polling 시작 (operator BUY halt enabled)"
        )
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
