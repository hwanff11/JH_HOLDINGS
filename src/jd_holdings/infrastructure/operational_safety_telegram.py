from __future__ import annotations

import html
import threading

import telebot

from jd_holdings.application.operational_safety import OperatorSafetyService

from . import telegram_bot as telegram_bot_module
from .initial_onboarding_telegram import InitialOnboardingTelegramBotApp
from .telegram_bot_v322 import _v322_bot_commands

RESUME_CONFIRMATION = "RESUME_BUYS"


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
        if "[JDSS V3.2.2 운영 대시보드]" in text:
            halt_state = (
                "🚨 BUY 긴급정지"
                if self.repository.get_system_value("operator_buy_halt") == "1"
                else "✅ 정상"
            )
            marker = "• <b>실거래</b> : 🔒 잠금"
            if marker in text:
                text = text.replace(
                    marker,
                    marker + f"\n• <b>운영자 BUY 차단</b> : {halt_state}",
                    1,
                )
        super()._send(text, markup=markup, chat_id=chat_id)

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
                        f"해제 명령: <code>/resume {RESUME_CONFIRMATION}</code>",
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
            if len(parts) != 2 or parts[1] != RESUME_CONFIRMATION:
                self._send(
                    "🔒 <b>[BUY 차단 해제 확인]</b>\n\n"
                    "해제 전에 브로커/원장 정합성과 미체결 BUY가 자동 검사됩니다.\n"
                    f"계속하려면 <code>/resume {RESUME_CONFIRMATION}</code>를 정확히 입력해 주세요."
                )
                return
            try:
                result = self.operational_safety.resume()
                if not result["resumed"]:
                    self._send("✅ BUY 긴급정지는 이미 해제되어 있습니다.")
                    return
                self._send(
                    "✅ <b>[BUY 긴급정지 해제]</b>\n\n"
                    "브로커/원장 정합성 및 미체결 BUY 검사를 통과했습니다.\n"
                    "새 BUY는 기존 JDSS 승인 절차를 다시 거쳐야 합니다."
                )
            except Exception as exc:
                telegram_bot_module.LOGGER.exception("운영자 BUY 차단 해제 거부")
                self._send(
                    "⛔ <b>[BUY 차단 해제 거부]</b>\n\n"
                    "안전조건을 충족하지 못해 긴급정지를 유지합니다.\n"
                    f"<code>{html.escape(telegram_bot_module._operator_error_summary(exc))}</code>"
                )

    def run(self) -> None:
        commands = _v322_bot_commands()
        commands.insert(0, telebot.types.BotCommand("halt", "긴급 신규 BUY 차단"))
        commands.insert(1, telebot.types.BotCommand("resume", "정합성 확인 후 BUY 재개"))
        self.bot.set_my_commands(commands)
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info(
            "JDSS V3.2.2 Telegram polling 시작 (operator BUY halt enabled)"
        )
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
