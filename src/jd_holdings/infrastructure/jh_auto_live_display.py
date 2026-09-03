from __future__ import annotations

from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp, _money


class LiveJHAutoTelegramBotApp(JHAutoTelegramBotApp):
    """Live-only presentation guard for JH AUTO capital/HWM and operator messages.

    JDSS 3.2.2 keeps the canonical $50,000 research/backtest baseline, but LIVE
    capital is operator-configured. Before the first launch authorization, legacy
    V3.2.2 HWM state may still exist in the shared ledger. Never present that stale
    research-era value as if it were the current JH AUTO trading limit.

    The live app also inherits several historical semi-auto messages. Keep those
    internal/manual paths for compatibility, but normalize the operator-facing text so
    it never tells the operator that a per-trade human approval is required in JH AUTO.
    """

    _RESEARCH_CAPITAL_NOTE = (
        "ℹ️ JDSS의 <code>$50,000</code>은 공식 연구·백테스트 기준값이며 "
        "실거래 한도가 아닙니다."
    )
    _MUTABLE_CAPITAL_NOTE = (
        "ℹ️ 운용 기준자금은 최초 시작 후에도 변경할 수 있습니다. "
        "증액은 추가분을 단계적으로 열고, 감액은 위험축소를 우선합니다."
    )

    def _replace_hwm_lines(self, text: str) -> str:
        settings = self.auto_service.settings()
        perf = self._display_performance()
        current_hwm = _money(perf["high_water"])
        current_budget = _money(perf["risk_budget"])

        if settings.launch_authorized:
            return text.replace("HWM75 위험한도", "HWM75 현재 위험예산")

        text = text.replace(
            f"• 최고 평가액 : <code>{current_hwm}</code>",
            "• 최고 평가액 : <b>시작 전</b>",
        )
        text = text.replace(
            f"• HWM75 위험한도 : <code>{current_budget}</code>",
            "• HWM75 현재 위험예산 : <b>시작 전</b>",
        )
        return text

    @staticmethod
    def _insert_before(text: str, marker: str, extra: str) -> str:
        if extra in text or marker not in text:
            return text
        return text.replace(marker, f"{extra}\n{marker}", 1)

    def _normalize_inherited_auto_text(self, text: str) -> str:
        """Remove stale semi-auto guidance from inherited live messages."""
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
                (
                    "실계좌가 연결되어 있으며 신규 매수는 최초 시작승인·대표 긴급정지·"
                    "JH AUTO 내부 안전검증으로 통제합니다."
                ),
            ),
            (
                (
                    "🔥 실거래에서는 신규 매수 잠금 해제와 2단계 승인을 모두 통과해야 "
                    "실제 토스 주문이 전송됩니다."
                ),
                (
                    "🔥 실거래에서는 JH AUTO 시작승인과 내부 2단계 검증·최종 주문경계를 "
                    "모두 통과해야 실제 토스 주문이 전송됩니다."
                ),
            ),
            (
                (
                    "현재 신규 BUY는 안전조건을 통과한 상태이며 실제 주문은 기존 2단계 "
                    "승인 후에만 제출됩니다."
                ),
                (
                    "현재 신규 BUY는 안전조건을 통과한 상태이며 JH AUTO가 다음 독립 "
                    "안전주기에서 내부 2단계 검증 후에만 제출할 수 있습니다."
                ),
            ),
            (
                "이제 신규 BUY 승인 절차를 진행할 수 있습니다.",
                "이제 JH AUTO가 다음 독립 안전주기에서 신규 BUY 여부를 다시 판단할 수 있습니다.",
            ),
            (
                "⚠️ 실제 주문은 여전히 기존 JDSS 2단계 승인 후에만 제출됩니다.",
                (
                    "⚠️ 실제 주문은 별도 개별 승인 없이 JH AUTO 내부 2단계 검증과 "
                    "최종 주문경계를 통과한 경우에만 제출됩니다."
                ),
            ),
            (
                "각 BUY는 기존 2단계 주문 승인이 추가로 필요합니다.",
                (
                    "별도 개별 BUY 승인은 필요하지 않으며 JH AUTO 내부 2단계 검증과 "
                    "최종 안전경계를 다시 통과해야 합니다."
                ),
            ),
            (
                "• 위험증가 BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동입니다.",
                "• 위험증가 BUY는 JH AUTO 내부 2단계 검증, 위험축소 SELL은 자동입니다.",
            ),
        )
        for old, new in replacements:
            text = text.replace(old, new)

        if "[JDSS 실거래 아침 브리핑]" in text:
            text = text.replace("[JDSS 실거래 아침 브리핑]", "[JH AUTO 아침 브리핑]")
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

    def _format_auto_dashboard(self) -> str:
        text = self._replace_hwm_lines(super()._format_auto_dashboard())
        text = self._insert_before(
            text,
            "🛡️ <b>안전상태</b>",
            f"{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
        )
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
            hwm_line = (
                "• HWM75 현재 위험예산은 현재 허용원금과 실제 운용성과를 기준으로 "
                "계산됩니다."
            )
        else:
            hwm_line = "• HWM75 현재 위험예산 : <b>시작 후 현재 허용원금 기준으로 계산</b>"
        text = self._insert_before(
            text,
            "기준자금 또는 자동운용비율 변경은",
            f"{hwm_line}\n{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
        )
        return text, markup

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(
            self._normalize_inherited_auto_text(text),
            markup=markup,
            chat_id=chat_id,
        )
