from __future__ import annotations

import html
import logging
from decimal import Decimal

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings.application.initial_onboarding_portfolio import (
    InitialOnboardingPortfolioService,
)
from jd_holdings.core.initial_onboarding import (
    STATUS_ACTIVE,
    STATUS_BYPASSED,
    STATUS_COMPLETED,
    STATUS_DISABLED,
    STATUS_NOT_STARTED,
)
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .telegram_bot_runtime import RuntimeTelegramBotApp

LOGGER = logging.getLogger(__name__)


def _augment_help_message(text: str) -> str:
    """Keep the base runtime help in sync without duplicating the whole handler."""
    if "[JDSS V3.2.2 운영 메뉴]" not in text or "<code>/onboarding</code>" in text:
        return text
    marker = "<b>승인·검증</b>"
    addition = (
        "<b>최초진입</b>\n"
        "• <code>/onboarding</code> — 50% → 75% → 100% 매수 한도·대기일 확인\n\n"
    )
    if marker in text:
        return text.replace(marker, addition + marker, 1)
    return text + "\n\n" + addition.rstrip()


def _parse_onboarding_callback(data: str) -> tuple[str, int]:
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != "onboard":
        raise ValueError("지원하지 않는 최초진입 작업입니다")
    action = parts[1]
    if action not in {"start", "advance"}:
        raise ValueError("지원하지 않는 최초진입 작업입니다")
    try:
        expected_stage = int(parts[2])
    except ValueError as exc:
        raise ValueError("최초진입 버튼 단계 정보가 올바르지 않습니다") from exc
    if expected_stage < 0:
        raise ValueError("최초진입 버튼 단계 정보가 올바르지 않습니다")
    return action, expected_stage


def _validate_onboarding_callback(action: str, expected_stage: int, snapshot: dict) -> None:
    """Reject stale Telegram buttons after onboarding state has moved on."""
    status = str(snapshot["status"])
    stage = int(snapshot["stage"])
    if action == "start":
        if expected_stage != 0:
            raise RuntimeError("오래된 최초진입 시작 버튼입니다. /onboarding을 다시 확인하세요.")
        if status not in {STATUS_NOT_STARTED, STATUS_BYPASSED} or not bool(
            snapshot.get("can_start")
        ):
            raise RuntimeError("현재 상태와 맞지 않는 시작 버튼입니다. /onboarding을 다시 확인하세요.")
        return
    if action == "advance":
        if status != STATUS_ACTIVE or stage != expected_stage:
            raise RuntimeError("오래된 단계 버튼입니다. /onboarding에서 최신 단계를 다시 확인하세요.")
        if not bool(snapshot.get("next_stage_ready")):
            raise RuntimeError("아직 다음 단계 진입 조건을 충족하지 않았습니다.")
        return
    raise ValueError("지원하지 않는 최초진입 작업입니다")


class InitialOnboardingTelegramBotApp(RuntimeTelegramBotApp):
    """Telegram operator controls for the one-time staged first entry."""

    @property
    def onboarding_service(self) -> InitialOnboardingPortfolioService | None:
        service = self.portfolio_service
        if isinstance(service, InitialOnboardingPortfolioService):
            return service
        return None

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(
            _augment_help_message(text),
            markup=markup,
            chat_id=chat_id,
        )

    def _register_handlers(self) -> None:
        super()._register_handlers()
        bot = self.bot

        @bot.message_handler(commands=["onboarding", "entry", "firstentry"])
        def onboarding(message):
            if not self._authorized_message(message):
                return
            try:
                self._send_onboarding_status()
            except Exception as exc:
                LOGGER.exception("최초진입 상태 조회 실패")
                self._send(
                    "❌ 최초진입 상태를 확인하지 못했습니다.\n"
                    f"<code>{html.escape(str(exc))}</code>"
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("onboard|"))
        def onboarding_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            service = self.onboarding_service
            if service is None:
                bot.answer_callback_query(
                    call.id, "최초진입 서비스가 비활성화되어 있습니다.", show_alert=True
                )
                return
            try:
                action, expected_stage = _parse_onboarding_callback(call.data)
                before = service.onboarding_snapshot()
                _validate_onboarding_callback(action, expected_stage, before)
                if action == "start":
                    snapshot = service.start_onboarding()
                    answer = "1차 최초진입을 열었습니다."
                else:
                    snapshot = service.advance_onboarding()
                    answer = f"{snapshot['stage']}차 최초진입을 열었습니다."
                self._clear_callback_markup(call)
                bot.answer_callback_query(call.id, answer)
                self._send(self._format_onboarding_message(snapshot))
                result = service.run_allocation()
                if result is not None:
                    for event in result.events:
                        self._send(f"🪜 {html.escape(event)}")
                    self.notify_portfolio_buy_batch_ready(result.signals)
            except Exception as exc:
                LOGGER.exception("최초진입 단계 처리 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

    def _send_onboarding_status(self) -> None:
        service = self.onboarding_service
        if service is None:
            self._send(
                "🪜 <b>[최초진입 분할]</b>\n\n"
                "최초진입 서비스가 현재 런타임에 연결되어 있지 않습니다."
            )
            return
        snapshot = service.onboarding_snapshot()
        self._send(
            self._format_onboarding_message(snapshot),
            markup=self._onboarding_markup(snapshot),
        )

    def _format_onboarding_message(self, snapshot: dict) -> str:
        status = str(snapshot["status"])
        stage = int(snapshot["stage"])
        total = int(snapshot["total_stages"])
        fraction = Decimal(str(snapshot["fraction"]))
        labels = {
            STATUS_NOT_STARTED: "🕒 시작 전",
            STATUS_ACTIVE: "🪜 분할진입 진행 중",
            STATUS_COMPLETED: "✅ 완료",
            STATUS_BYPASSED: "⚠️ 기존 포지션 감지",
            STATUS_DISABLED: "⏸ 비활성",
        }
        minimum_sessions = snapshot["minimum_sessions_between_stages"]
        lines = [
            f"🪜 <b>[JDSS 최초 실전 진입 · {total}단계]</b>",
            "",
            f"• <b>상태</b> : {labels.get(status, html.escape(status))}",
            "• <b>방식</b> : <code>50% → 75% → 100%</code>",
            f"• <b>단계 간격</b> : 최소 <code>{minimum_sessions} 미국 거래일</code>",
        ]
        if status == STATUS_ACTIVE:
            lines.extend(
                [
                    f"• <b>현재 단계</b> : <code>{stage}/{total}</code>",
                    f"• <b>현재 활성 한도</b> : 최종 전략 목표의 <code>{fraction * 100:.0f}%</code>",
                    f"• <b>현재 단계 체결</b> : {'✅ 완료' if snapshot['stage_filled'] else '⏳ 진행 중'}",
                ]
            )
            if stage < total:
                next_fraction = Decimal(str(snapshot["next_fraction"]))
                if snapshot["next_stage_ready"]:
                    lines.append(
                        f"• <b>다음 단계</b> : ✅ 누적 "
                        f"<code>{next_fraction * 100:.0f}%</code> 진입 가능"
                    )
                elif snapshot["stage_filled"]:
                    remaining = snapshot["sessions_remaining"]
                    lines.append(
                        "• <b>다음 단계</b> : 미국 거래일 "
                        f"<code>{remaining}일</code> 후 가능"
                    )
                else:
                    lines.append("• <b>다음 단계</b> : 현재 단계 목표수량 체결 후 대기 시작")
        elif status in {STATUS_NOT_STARTED, STATUS_BYPASSED}:
            if snapshot["can_start"]:
                lines.append("• <b>시작 가능</b> : ✅ 관리 포지션·미체결·SAFE_MODE 이상 없음")
            else:
                lines.append(
                    "• <b>시작 차단</b> : "
                    f"⚠️ {html.escape(str(snapshot['start_block_reason'] or '운영 조건 미충족'))}"
                )
        elif status == STATUS_COMPLETED:
            lines.append("• <b>현재 운용</b> : 최초진입 제한 해제 · V3.2.2 일반 운용")

        effective = dict(snapshot.get("effective_targets") or {})
        full = dict(snapshot.get("full_targets") or {})
        if full:
            lines.extend(["", "📦 <b>현재 고정 목표수량</b>"])
            for symbol in ALLOCATION_SYMBOLS:
                if symbol not in full:
                    continue
                lines.append(
                    f"• <b>{symbol}</b> : 단계 <code>{effective.get(symbol, 0)}주</code> / "
                    f"최종 <code>{full[symbol]}주</code>"
                )
        lines.extend(
            [
                "",
                "🛡️ <b>안전 원칙</b>",
                "• 단계 버튼은 매수 한도만 열며 주문을 제출하지 않습니다.",
                "• 실제 BUY는 ‘오늘 주문 한번에 검토’에서 묶어 승인하거나 /signal로 개별 승인합니다.",
                "• 시장이 악화되어 목표가 줄면 위험축소 SELL은 자동으로 먼저 실행합니다.",
                "• 이전 메시지의 단계 버튼은 상태가 바뀌면 무효 처리합니다.",
                f"• {total}차 완료 뒤에는 이 최초진입 로직이 다시 시작되지 않습니다.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _onboarding_markup(snapshot: dict) -> InlineKeyboardMarkup | None:
        markup = InlineKeyboardMarkup()
        status = str(snapshot["status"])
        if status in {STATUS_NOT_STARTED, STATUS_BYPASSED} and snapshot["can_start"]:
            markup.add(
                InlineKeyboardButton(
                    "🪜 1차 매수 한도 열기 · 50% (주문 전)",
                    callback_data="onboard|start|0",
                )
            )
            return markup
        if status == STATUS_ACTIVE and snapshot["next_stage_ready"]:
            stage = int(snapshot["stage"])
            next_fraction = Decimal(str(snapshot["next_fraction"]))
            markup.add(
                InlineKeyboardButton(
                    f"🪜 {stage + 1}차 매수 한도 열기 · 누적 "
                    f"{next_fraction * 100:.0f}% (주문 전)",
                    callback_data=f"onboard|advance|{stage}",
                )
            )
            return markup
        return None

    def _send_signal(self, signal: dict) -> None:
        if signal.get("action") == "CORE_REBALANCE_BUY":
            service = self.onboarding_service
            if service is not None:
                snapshot = service.onboarding_snapshot()
                if snapshot["status"] == STATUS_ACTIVE:
                    self._send(
                        "🪜 <b>[최초진입 분할 BUY]</b> "
                        f"{snapshot['stage']}/{snapshot['total_stages']}단계 · "
                        f"누적 <code>{Decimal(str(snapshot['fraction'])) * 100:.0f}%</code> 한도"
                    )
        super()._send_signal(signal)

    def _format_portfolio_message(self) -> str:
        base = super()._format_portfolio_message()
        service = self.onboarding_service
        if service is None:
            return base
        snapshot = service.onboarding_snapshot()
        status = str(snapshot["status"])
        if status == STATUS_ACTIVE:
            suffix = (
                "\n\n🪜 <b>최초진입</b> : "
                f"{snapshot['stage']}/{snapshot['total_stages']}단계 · "
                f"최종 목표의 <code>{Decimal(str(snapshot['fraction'])) * 100:.0f}%</code>까지 활성"
                "\n<code>/onboarding</code>에서 다음 단계 가능 여부를 확인하세요."
            )
        elif status == STATUS_NOT_STARTED:
            suffix = "\n\n🪜 <b>최초진입</b> : 시작 전 · <code>/onboarding</code>"
        elif status == STATUS_COMPLETED:
            suffix = "\n\n✅ <b>최초진입</b> : 단계 완료 · 일반 운용"
        else:
            suffix = "\n\n🪜 <b>최초진입</b> : <code>/onboarding</code>에서 상태 확인"
        return base + suffix
