from __future__ import annotations

from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp, _money


class LiveJHAutoTelegramBotApp(JHAutoTelegramBotApp):
    """Live-only presentation guard for JH AUTO capital/HWM messages.

    JDSS 3.2.2 keeps the canonical $50,000 research/backtest baseline, but LIVE
    capital is operator-configured. Before the first launch authorization, legacy
    V3.2.2 HWM state may still exist in the shared ledger. Never present that stale
    research-era value as if it were the current JH AUTO trading limit.
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
            hwm_line = "• HWM75 현재 위험예산은 현재 허용원금과 실제 운용성과를 기준으로 계산됩니다."
        else:
            hwm_line = "• HWM75 현재 위험예산 : <b>시작 후 현재 허용원금 기준으로 계산</b>"
        text = self._insert_before(
            text,
            "기준자금 또는 자동운용비율 변경은",
            f"{hwm_line}\n{self._RESEARCH_CAPITAL_NOTE}\n{self._MUTABLE_CAPITAL_NOTE}\n",
        )
        return text, markup
