# Telegram HTML templates are intentionally kept as complete message lines.
# ruff: noqa: E501

from __future__ import annotations

import html
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings import __version__
from jd_holdings.application.analysis_service import AnalysisResult, AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import IdleCashManager, IdleCashReleasePending
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.trading_service import QuoteChangedError, TradingService
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestResult
from jd_holdings.backtest.runner import run_production_backtest
from jd_holdings.config import StrategyConfig
from jd_holdings.core.indicators import MarketDataError
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import RuntimeSettings

LOGGER = logging.getLogger(__name__)
IDLE_CASH_COMMANDS = ("sgov",)
SEOUL_TZ = ZoneInfo("Asia/Seoul")
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_MESSAGE_LIMIT = 3900
# Telegram callback alerts allow fewer characters than normal messages.
OPERATOR_ERROR_LIMIT = 180

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|bearer|access[_ -]?token|refresh[_ -]?token|"
    r"client[_ -]?secret|api[_ -]?key|password)\b"
    r"(?:\s*[:=]\s*|\s+)([^\s,;]+)"
)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.])(?:/(?:home|root|workspace|tmp|etc)/[^\s<>]+|[A-Za-z]:\\[^\s<>]+)"
)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{8,16}(?!\d)")


def _redact_operator_text(value: object) -> str:
    """Remove credentials and infrastructure details from Telegram-visible text."""
    text = " ".join(str(value).split())
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[보호됨]", text)
    text = _URL_RE.sub("[외부주소 생략]", text)
    text = _ABSOLUTE_PATH_RE.sub("[서버경로 생략]", text)
    text = _LONG_NUMBER_RE.sub("[식별번호 생략]", text)
    if len(text) > OPERATOR_ERROR_LIMIT:
        text = text[: OPERATOR_ERROR_LIMIT - 1].rstrip() + "…"
    return text


def _operator_error_summary(exc: BaseException) -> str:
    """Return a bounded operator message while retaining full details in server logs."""
    return _redact_operator_text(exc) or type(exc).__name__


class BacktestCommandError(ValueError):
    """Raised when a Telegram /backtest command is not safe or valid."""


@dataclass(frozen=True)
class TelegramBacktestRequest:
    symbols: tuple[str, ...]
    start: date
    end: date


def parse_backtest_request(
    text: str,
    enabled_symbols: tuple[str, ...],
    default_start: str,
    latest_completed: date,
) -> TelegramBacktestRequest:
    """Parse the production V3.2.2 portfolio-only Telegram command."""
    parts = (text or "").split()[1:]
    if not parts:
        parts = ["300"]
    if len(parts) == 1 and parts[0].lower() in {"full", "all"}:
        start = date.fromisoformat(default_start)
        end = latest_completed
        _validate_backtest_period(start, end, default_start)
        return TelegramBacktestRequest(enabled_symbols, start, end)
    if len(parts) == 1 and parts[0].isdigit():
        trading_days = int(parts[0])
        if not 2 <= trading_days <= 5000:
            raise BacktestCommandError("거래일은 2~5000 사이로 입력해 주세요.")
        calendar = MarketClock().calendar
        lookback_start = latest_completed - timedelta(days=trading_days * 2 + 30)
        sessions = calendar.sessions_in_range(lookback_start, latest_completed)
        if len(sessions) < trading_days:
            raise BacktestCommandError("요청한 거래일 기간을 계산하지 못했습니다.")
        start = pd.Timestamp(sessions[-trading_days]).date()
        _validate_backtest_period(start, latest_completed, default_start)
        return TelegramBacktestRequest(enabled_symbols, start, latest_completed)
    if parts and parts[0] and parts[0][0].isalpha():
        raise BacktestCommandError(
            "Telegram 백테스트는 V3.2.2 전체 포트폴리오 전용입니다. "
            "종목 티커를 빼고 입력해 주세요."
        )
    if len(parts) != 2:
        raise BacktestCommandError(
            "형식은 /bt, /bt [거래일수], /bt full, /bt [시작일] [종료일] 중 하나입니다."
        )
    try:
        start = date.fromisoformat(parts[0])
        end = date.fromisoformat(parts[1])
    except ValueError as exc:
        raise BacktestCommandError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    _validate_backtest_period(start, end, default_start, latest_completed)
    return TelegramBacktestRequest(enabled_symbols, start, end)


def _validate_backtest_period(
    start: date,
    end: date,
    default_start: str,
    latest_completed: date | None = None,
) -> None:
    minimum_start = date.fromisoformat(default_start)
    if start < minimum_start:
        raise BacktestCommandError(f"시작일은 {minimum_start.isoformat()} 이후여야 합니다.")
    if start > end:
        raise BacktestCommandError("시작일은 종료일보다 늦을 수 없습니다.")
    if latest_completed is not None and end > latest_completed:
        raise BacktestCommandError(
            f"종료일은 최신 완결 거래일 {latest_completed.isoformat()} 이하여야 합니다."
        )
    sessions = MarketClock().calendar.sessions_in_range(start, end)
    if len(sessions) < 2:
        raise BacktestCommandError("백테스트는 2개 이상의 미국 거래일이 필요합니다.")


def parse_history_request(
    text: str,
    enabled_symbols: tuple[str, ...],
    default_days: int = 7,
) -> tuple[tuple[str, ...], int]:
    """Parse /history [SYMBOL] [DAYS], defaulting to all configured symbols."""
    parts = (text or "").split()[1:]
    if len(parts) > 2:
        raise ValueError("형식: /history [종목] [거래일수]")
    symbols = enabled_symbols
    if parts and not parts[0].isdigit():
        symbol = parts.pop(0).upper()
        if symbol not in enabled_symbols:
            raise ValueError(
                f"지원하지 않는 종목입니다. 사용 가능 종목: {', '.join(enabled_symbols)}"
            )
        symbols = (symbol,)
    if parts:
        days = int(parts[0])
    else:
        days = default_days
    if not 1 <= days <= 90:
        raise ValueError("조회 기간은 1~90 거래일 사이로 입력해 주세요.")
    return symbols, days


def _is_toss_order_maintenance_window(now_kst: datetime) -> bool:
    return now_kst.hour == 8 and 50 <= now_kst.minute <= 59


def _daily_analysis_is_due(now: datetime, scheduled_time) -> bool:
    current = now.replace(tzinfo=SEOUL_TZ) if now.tzinfo is None else now.astimezone(SEOUL_TZ)
    return (current.hour, current.minute) >= (
        scheduled_time.hour,
        scheduled_time.minute,
    )


def _money(value: object) -> str:
    return f"${Decimal(str(value)):,.2f}"


def _signed_money(value: object) -> str:
    amount = Decimal(str(value))
    return f"{amount:+,.2f} USD"


def _won(value: object) -> str:
    return f"₩{Decimal(str(value)):,.0f}"


def _format_idle_cash_event(event: str, trading_mode: str) -> str:
    escaped = html.escape(event)
    if trading_mode == "dry_run":
        label = "모의체결" if "FILLED" in event or "체결 반영" in event else "모의처리"
        return f"🧪 {label} — {escaped}"
    return f"💵 {escaped}"


def _telegram_message_chunks(
    text: str, limit: int = TELEGRAM_SAFE_MESSAGE_LIMIT
) -> tuple[tuple[str, str | None], ...]:
    """Split operator output below Telegram's limit without breaking normal HTML lines."""
    if not 1 <= limit < TELEGRAM_MESSAGE_LIMIT:
        raise ValueError("Telegram 분할 길이가 유효하지 않습니다")
    if len(text) <= limit:
        return ((text, "HTML"),)

    chunks: list[tuple[str, str | None]] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append(("\n".join(current), "HTML"))
            current = []
            size = 0

    for line in text.splitlines():
        added = len(line) + (1 if current else 0)
        if len(line) > limit:
            flush()
            # A single dynamic field can exceed the limit. Strip presentation
            # tags before hard splitting so every emitted chunk remains valid.
            plain = html.unescape(re.sub(r"<[^>]*>", "", line))
            while plain:
                chunks.append((plain[:limit], None))
                plain = plain[limit:]
            continue
        if current and size + added > limit:
            flush()
        current.append(line)
        size += len(line) + (1 if len(current) > 1 else 0)
    flush()
    return tuple(chunks)


def _is_network_error(exc: BaseException) -> bool:
    """Recognize network failures even when yfinance wraps the original error."""
    current: BaseException | None = exc
    visited: set[int] = set()
    network_markers = (
        "connection",
        "dns",
        "resolve host",
        "network",
        "timed out",
        "timeout",
    )
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        class_name = type(current).__name__.lower()
        message = str(current).lower()
        if any(marker in class_name or marker in message for marker in network_markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def _quantity(value: object) -> str:
    return f"{Decimal(str(value)):,.2f}".rstrip("0").rstrip(".")


def _profit_loss(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amountAfterCost", value.get("amount"))
    rate = value.get("rateAfterCost", value.get("rate"))
    if amount is None or rate is None:
        return None
    return _money(amount), f"{Decimal(str(rate)) * 100:+.2f}%"


def _regime_label(value: str) -> str:
    return {
        "GREEN": "🟢 강세장",
        "YELLOW": "🟡 중립장",
        "RED": "🔴 약세장",
        "BULLISH": "🟢 강세장",
        "NEUTRAL": "🟡 중립장",
        "BEARISH": "🔴 약세장",
    }.get(value, value)


def _grade_label(value: str) -> str:
    return {
        "S": "S등급·최우수",
        "A": "A등급·우수",
        "B": "B등급·관심",
        "WATCH": "관찰",
        "NO_TRADE": "매수 보류",
        "EXCELLENT": "강력 매수",
        "GOOD": "매수 관심",
        "FAIR": "중립",
        "WEAK": "관망",
        "POOR": "매수 보류",
    }.get(value, value)


def _state_label(value: str) -> str:
    return {
        "EMPTY": "☕ 관망 중",
        "WATCH": "☀️ 신호 감시 중",
        "CYCLING": "🔄 분할매수 진행 중",
        "HOLDING": "📦 보유 중",
        "SAFE_MODE": "🚨 안전 점검 필요",
    }.get(value, value.replace("_", " "))


def _format_position_state(position) -> str:
    state_val = (
        position.state.value
        if hasattr(position.state, "value")
        else str(position.state)
    )
    if state_val == "EMPTY":
        return "☕ 관망 중"
    if state_val == "WATCH":
        return "☀️ 신호 감시 중"
    if state_val in {"CYCLING", "HOLDING"}:
        count = getattr(position, "entry_count", 0)
        if count == 1:
            return "🟢 1차 진입 완료"
        elif count == 2:
            return "🟡 2차 분할매수"
        elif count == 3:
            return "🟠 3차 분할매수"
        elif count >= 4:
            return "🔴 4차 풀매수 완료"
        return "🔄 분할매수 진행 중"
    if state_val == "SAFE_MODE":
        return "🚨 안전 점검 필요"
    return _state_label(state_val)


def _action_label(value: str) -> str:
    return {
        "WAIT": "지금은 관망",
        "WATCH": "신호 관찰",
        "STAGE_BUY": "분할매수 검토",
        "REBUY": "재매수 검토",
        "HOLD": "보유 유지",
        "FIRST_ENTRY_CANDIDATE": "1차 매수 검토 가능",
        "ADD_ENTRY_CANDIDATE": "추가매수 검토 가능",
        "REBUY_CANDIDATE": "재매수 검토 가능",
        "CORE_REBALANCE_BUY": "V3.2.2 allocation BUY 승인 대기",
        "NO_ACTION": "현재 매수 조건 미충족",
    }.get(value, value.replace("_", " "))


def _cci_label(value: float) -> str:
    if value <= -200:
        return "극심한 과매도"
    if value <= -150:
        return "강한 과매도"
    if value <= -100:
        return "과매도"
    if value >= 100:
        return "과열"
    return "중립"


def _rsi_label(value: float) -> str:
    if value <= 20:
        return "극심한 과매도"
    if value <= 30:
        return "과매도"
    if value <= 40:
        return "약세"
    if value >= 70:
        return "과매수"
    if value >= 60:
        return "강세"
    return "중립"


def _atr_label(value: float) -> str:
    if value < 0.01:
        return "변동성 낮음"
    if value < 0.02:
        return "변동성 보통 이하"
    if value <= 0.10:
        return "전략 적정 변동성"
    return "변동성 매우 큼"


def _volume_label(value: float) -> str:
    if value < 1.0:
        return "평균 이하"
    if value < 1.2:
        return "평균 수준"
    if value < 1.5:
        return "거래 증가"
    if value < 2.0:
        return "거래 활발"
    return "거래량 급증"


def _close_position_label(value: float) -> str:
    if value < 0.3:
        return "당일 저가권 마감"
    if value < 0.5:
        return "당일 하단 마감"
    if value < 0.7:
        return "당일 상단 마감"
    return "당일 고가권 마감"


def _ema_label(snapshot) -> str:
    if snapshot.close > snapshot.ema20 > snapshot.ema60:
        return "중장기 상승 추세"
    if snapshot.close > snapshot.ema5:
        return "단기 반등 확인"
    return "단기 반등 미확인"


def _bollinger_label(snapshot) -> str:
    if snapshot.close <= snapshot.bb_lower * 0.98:
        return "하단 2% 이상 이탈 (강한 과매도)"
    if snapshot.close <= snapshot.bb_lower:
        return "하단 접촉·이탈 (과매도)"
    if snapshot.close <= snapshot.bb_lower * 1.02:
        return "하단 근처"
    return "하단 위"


def _condition_mark(condition: bool) -> str:
    return "✅ 충족" if condition else "⬜ 미충족"


def _guide_cards() -> tuple[str, ...]:
    """Return the current user-visible guide from the V3.2.2 contract."""
    return (
        (
            "📈 <b>[JDSS V3.2.2 전략 개요]</b>\n\n"
            "• 기본은 QQQ 시장에 계속 참여하고 위험도에 따라 "
            "<code>0.5 / 1.0 / 1.25 / 1.5x</code>로 노출을 조절합니다.\n"
            "• QQQ 20일 연환산 변동성이 30% 이상이면 0.5x로 방어합니다.\n"
            "• 새 달 첫 거래일 종가에서 추세와 반도체 상대강도를 다시 판정합니다.\n"
            "• 위험증가 BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동입니다.\n"
            "• 실거래는 잠겨 있고 forced dry-run만 허용합니다."
        ),
        (
            "🧭 <b>[추세·레버리지]</b>\n\n"
            "• SMA50/SMA200, 1·3·6개월 모멘텀, SMA200 기울기를 사용합니다.\n"
            "• 강한 상승장 1.5x, 중간 추세 1.25x, 일반 1.0x, 고변동 0.5x입니다.\n"
            "• 월중 고변동 브레이크는 감속만 하며 다음 월 reset 전에는 다시 가속하지 않습니다."
        ),
        (
            "💻 <b>[RS6M 반도체 슬리브]</b>\n\n"
            "• SOXX 126거래일 수익률이 양수이면서 QQQ보다 높으면 상대강도 ON입니다.\n"
            "• 레버리지 슬리브의 50%를 TQQQ, 50%를 SOXL로 나눕니다.\n"
            "• 월중 우위가 깨지면 SOXL을 TQQQ로 한 번 후퇴하고 다음 달까지 재진입을 금지합니다."
        ),
        (
            "💰 <b>[HWM75 통제복리]</b>\n\n"
            "• 시작 위험원금은 <code>$50,000</code>입니다.\n"
            "• 새 최고자산을 만들면 누적이익의 75%만 다음 위험예산 증가에 반영합니다.\n"
            "• 나머지 25%는 계좌 현금으로 남지만 위험예산 확대에는 쓰지 않습니다.\n"
            "• 손실이 나도 개인자금으로 자동 보충하지 않습니다."
        ),
        (
            "🎯 <b>[JDSS 5% 오버레이·안전장치]</b>\n\n"
            "• H40-S3 과매도 로직은 독립 매수전략이 아니라 오버레이 신호로 사용합니다.\n"
            "• 활성 시 QQQ 최대 5%를 TQQQ/SOXL로 교체합니다.\n"
            "• QQQ·TQQQ·SOXL 개인물량을 같은 Toss 계좌에 섞지 않습니다.\n"
            "• 주문결과 불명확·수량불일치·레거시 direct 상태는 SAFE_MODE로 신규 BUY를 막습니다."
        ),
        (
            "🪜 <b>[최초진입 50% → 75% → 100%]</b>\n\n"
            "• <code>/onboarding</code>에서 최초진입 상태와 다음 단계 가능일을 확인합니다.\n"
            "• 1차는 최종 목표의 50%, 최소 3 미국 거래일 뒤 75%, 다시 최소 3 거래일 뒤 100%입니다.\n"
            "• 단계 버튼은 매수 한도만 열며 주문을 바로 제출하지 않습니다.\n"
            "• 실제 BUY는 <b>매수 주문 검토하기 → 최종 매수 실행</b> 버튼을 모두 눌러야 합니다.\n"
            "• 백테스트도 요청 시작일을 새 계좌의 최초진입일로 보고 같은 분할진입을 적용합니다."
        ),
    )


def _review_buy_button_text(symbol: str) -> str:
    return f"🛒 {symbol} 매수 주문 검토하기"


def _execute_buy_button_text(symbol: str, quantity: object, trading_mode: str) -> str:
    action = "모의매수 실행" if trading_mode == "dry_run" else "실매수 실행"
    return f"✅ {symbol} {_quantity(quantity)}주 {action}"


def _is_us_holding(item: dict) -> bool:
    country = str(item.get("marketCountry") or item.get("country") or "").upper()
    currency = str(item.get("currency") or "").upper()
    return country in {"US", "USA", "UNITED_STATES"} or currency == "USD"


class TelegramBotApp:
    def __init__(
        self,
        config: StrategyConfig,
        settings: RuntimeSettings,
        repository: SQLiteRepository,
        analysis_service: AnalysisService,
        trading_service: TradingService,
        order_monitor: OrderMonitor,
        reconciliation_service: ReconciliationService,
        data_source: YFinanceDataSource,
        market_clock: MarketClock,
        account_client: TossClient | None = None,
        idle_cash_manager: IdleCashManager | None = None,
        portfolio_service: PortfolioService | None = None,
    ) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다")
        if len(settings.allowed_chat_ids) != 1:
            raise ValueError("JDSS Telegram은 정확히 1개의 관리자 Chat ID만 허용합니다")
        self.config = config
        self.settings = settings
        self.repository = repository
        self.analysis_service = analysis_service
        self.trading_service = trading_service
        self.order_monitor = order_monitor
        self.reconciliation_service = reconciliation_service
        self.data_source = data_source
        self.market_clock = market_clock
        self.account_client = account_client
        self.idle_cash_manager = idle_cash_manager
        self.portfolio_service = portfolio_service
        self.allowed_chat_id = settings.allowed_chat_ids[0]
        if self.allowed_chat_id <= 0:
            raise ValueError("JDSS Telegram은 그룹이 아닌 관리자 1:1 Chat ID만 허용합니다")
        self.bot = telebot.TeleBot(settings.telegram_bot_token, threaded=True)
        self._stop = threading.Event()
        self._backtest_lock = threading.Lock()
        self._last_monitor = 0.0
        self._last_idle_cash_sweep = 0.0
        self._register_handlers()

    def _authorized_message(self, message) -> bool:
        sender = getattr(message, "from_user", None)
        return (
            getattr(message.chat, "type", None) == "private"
            and sender is not None
            and int(message.chat.id) == self.allowed_chat_id
            and int(sender.id) == self.allowed_chat_id
        )

    def _authorized_callback(self, call) -> bool:
        return (
            getattr(call.message.chat, "type", None) == "private"
            and int(call.message.chat.id) == self.allowed_chat_id
            and int(call.from_user.id) == self.allowed_chat_id
        )

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        chunks = _telegram_message_chunks(text)
        for index, (chunk, parse_mode) in enumerate(chunks):
            self.bot.send_message(
                self.allowed_chat_id if chat_id is None else chat_id,
                chunk,
                parse_mode=parse_mode,
                reply_markup=markup if index == len(chunks) - 1 else None,
                disable_web_page_preview=True,
            )

    def _send_long(self, text: str, limit: int = 3500) -> None:
        for chunk, parse_mode in _telegram_message_chunks(text, min(limit, 3900)):
            self.bot.send_message(
                self.allowed_chat_id,
                chunk,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )

    def _clear_callback_markup(self, call) -> None:
        """Make a consumed, expired, or canceled approval button visibly one-shot."""
        try:
            self.bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None,
            )
        except Exception:
            LOGGER.warning("Telegram 소비된 승인 버튼 제거 실패", exc_info=True)

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
        for symbol in ALLOCATION_SYMBOLS:
            buttons = [
                InlineKeyboardButton(f"📊 {symbol} 배분", callback_data=f"status|{symbol}")
            ]
            if symbol in self.config.enabled_symbols:
                buttons.append(
                    InlineKeyboardButton(
                        f"🎯 {symbol} 오버레이", callback_data=f"score|{symbol}"
                    )
                )
            markup.row(
                *buttons,
            )
        return markup

    @staticmethod
    def _format_score_history(
        symbol: str,
        history: list[dict[str, object]],
        requested_days: int,
    ) -> str:
        lines = [
            f"📈 <b>{symbol} 최근 {requested_days}거래일 JDSS 점수</b>",
            "<i>완결 일봉 기준 재계산 결과</i>",
            "",
        ]
        if not history:
            return "\n".join(lines + ["조회 가능한 점수 이력이 없습니다."])
        for item in history:
            trade_date = item["trade_date"]
            lines.append(
                f"• <code>{trade_date}</code>  "
                f"<b>{item['score']}점</b> · {item['grade']} · {item['regime']}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_score_message(result: AnalysisResult) -> str:
        snapshot = result.snapshot
        score = result.score
        regime_value = score.regime.value
        return (
            f"🎯 <b>[{result.symbol} 5% 가상 오버레이 분석]</b>\n\n"
            f"📊 <b>총점</b> : <code>{score.total}점</code> / 100점 ({_grade_label(score.grade.value)})\n"
            f"🌐 <b>시장 국면</b> : {_regime_label(regime_value)}\n\n"
            "✨ <b>점수 구성</b>\n"
            f"• 시장 국면 : <code>{score.regime_score:2d} / 25점</code>\n"
            f"• 과매도 점수 : <code>{score.oversold_score:2d} / 40점</code>\n"
            f"• 반등 점수 : <code>{score.reversal_score:2d} / 20점</code>\n"
            f"• 거래량 점수 : <code>{score.volume_score:2d} / 10점</code>\n"
            f"• ATR 변동성 : <code>{score.atr_score:2d} / 5점</code>\n\n"
            "🔎 <b>보조 지표 해석</b>\n"
            f"• CCI 5 / 10 : <code>{snapshot.cci5:.1f} / {snapshot.cci10:.1f}</code> "
            f"→ {_cci_label(snapshot.cci5)} / {_cci_label(snapshot.cci10)}\n"
            f"• RSI 5 / 14 : <code>{snapshot.rsi5:.1f} / {snapshot.rsi14:.1f}</code> "
            f"→ {_rsi_label(snapshot.rsi5)} / {_rsi_label(snapshot.rsi14)}\n"
            f"• EMA 추세 : <b>{_ema_label(snapshot)}</b> "
            f"(종가 <code>{snapshot.close:.2f}</code> / EMA5 <code>{snapshot.ema5:.2f}</code>)\n"
            f"• 볼린저 하단 : <b>{_bollinger_label(snapshot)}</b> "
            f"(하단 <code>{snapshot.bb_lower:.2f}</code>)\n"
            f"• 거래량 : <code>{snapshot.volume_ratio:.2f}배</code> → {_volume_label(snapshot.volume_ratio)}\n"
            f"• ATR : <code>{snapshot.atr_pct * 100:.2f}%</code> → {_atr_label(snapshot.atr_pct)}\n"
            f"• 종가 위치 : <code>{snapshot.close_position:.2f}</code> → {_close_position_label(snapshot.close_position)}\n\n"
            "🚦 <b>JDSS 가상 신호 핵심 조건</b>\n"
            f"• 총점 55점 이상 : {_condition_mark(score.total >= 55)}\n"
            f"• 반등 5점 이상 : {_condition_mark(score.reversal_score >= 5)}\n"
            f"• RED 국면 아님 : {_condition_mark(regime_value not in {'RED', 'BEARISH'})}\n\n"
            f"💡 <b>가상 신호</b> : "
            f"<b>{'활성 후보' if result.decision.allowed else '비활성'}</b>\n\n"
            "ℹ️ 이 결과는 독립 매수 신호가 아닙니다. V3.2.2 목표 배분에서 "
            "QQQ 최대 5%를 TQQQ·SOXL로 교체할지 계산하는 입력입니다."
        )

    def _format_status_message(self, symbol: str) -> str:
        core = self.repository.get_core_position(symbol)
        open_orders = self.repository.open_orders(symbol)
        safe_reasons: list[str] = []
        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            safe_reasons.append("포트폴리오 SAFE_MODE")
        if symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            if position.state.value == "SAFE_MODE":
                safe_reasons.append("종목 SAFE_MODE")
            elif position.quantity > 0 or position.state.value != "EMPTY":
                safe_reasons.append("구버전 direct 상태 확인 필요")
        state = "🚨 " + " / ".join(safe_reasons) if safe_reasons else "✅ 정상"
        lines = [
            f"📊 <b>[{symbol} V3.2.2 목표배분]</b>",
            "",
            f"• <b>운영 상태</b> : {state}",
            f"• <b>목표 비중</b> : <code>{Decimal(str(core['target_weight'])) * 100:.2f}%</code>",
            f"• <b>배분 원장 수량</b> : <code>{_quantity(core['qty'])}주</code>",
            f"• <b>배분 원가</b> : <code>{_money(core['cost_basis'])}</code>",
            f"• <b>목표 기준일</b> : <code>{core['signal_trade_date'] or '-'}</code>",
            f"• <b>열린 주문</b> : <code>{len(open_orders)}건</code>",
        ]
        if open_orders:
            lines.extend(
                [
                    "",
                    "📋 <b>주문 상태</b>",
                    *[
                        f"• {item['side']} {_quantity(item['qty'])}주 · "
                        f"<code>{html.escape(str(item['status']))}</code>"
                        for item in open_orders
                    ],
                ]
            )
        lines.extend(
            [
                "",
                "ℹ️ V3.2.2는 QQQ·TQQQ·SOXL을 하나의 allocation 원장으로 관리합니다.",
            ]
        )
        return "\n".join(lines)

    def _format_portfolio_message(self) -> str:
        if self.portfolio_service is None:
            return "📊 <b>[V3 포트폴리오]</b>\n\n포트폴리오 서비스가 비활성화되어 있습니다."
        snapshot = self.portfolio_service.snapshot()
        mode = "🧪 모의운용" if self.settings.trading_mode == "dry_run" else "🔥 실거래"
        equity = Decimal(str(snapshot["equity"]))
        cash = Decimal(str(snapshot.get("cash", 0)))
        invested = Decimal(str(snapshot.get("invested_market_value", equity - cash)))
        profit = Decimal(str(snapshot.get("net_profit", 0)))
        cash_weight = cash / equity * 100 if equity else Decimal("0")
        invested_weight = invested / equity * 100 if equity else Decimal("0")
        lines = [
            "📊 <b>[JDSS 포트폴리오 현황]</b>",
            "",
            "💰 <b>자산 구성</b>",
            f"• 총 평가자산  <code>{_money(equity)}</code>",
            f"• 보유 평가액  <code>{_money(invested)}</code> · {invested_weight:.1f}%",
            f"• 남은 현금    <code>{_money(cash)}</code> · {cash_weight:.1f}%",
            f"• 원금 대비    <code>{_signed_money(profit)}</code>",
            "",
            "🛡️ <b>자금관리</b>",
            f"• 최고 평가액  <code>{_money(snapshot['high_water'])}</code>",
            f"• 투자 위험한도 <code>{_money(snapshot['risk_budget'])}</code>",
            f"• 운영 상태    {mode} · 실거래 🔒",
            "",
            "📦 <b>종목별 보유·목표</b>",
        ]
        rows = {row["symbol"]: row for row in snapshot["rows"]}
        for symbol in ALLOCATION_SYMBOLS:
            row = rows[symbol]
            market_value = Decimal(str(row.get("core_market_value", 0)))
            unrealized = Decimal(str(row.get("unrealized_profit", 0)))
            lines.extend([
                f"<b>{symbol}</b> · {_quantity(row['core_quantity'])}주 · <code>{_money(market_value)}</code>",
                f"└ 현재 {Decimal(str(row['core_weight'])) * 100:.1f}% / "
                f"목표 {Decimal(str(row['target_weight'])) * 100:.1f}% · "
                f"평가손익 <code>{_signed_money(unrealized)}</code>",
            ])
        lines.extend([
            "",
            f"🗓️ 목표 기준일 <code>{rows['QQQ']['signal_trade_date'] or '-'}</code>",
            "ℹ️ 모의 원장 기준입니다. 실제 잔고는 /account에서 확인하세요.",
        ])
        return "\n".join(lines)

    def _format_daily_portfolio_report(self, trade_date: str) -> str:
        """One operator-friendly message replacing fragmented allocation events."""
        if self.portfolio_service is None:
            return f"☀️ <b>[{trade_date} 일일 운용보고]</b>\n\n포트폴리오 서비스가 비활성화되어 있습니다."
        snapshot = self.portfolio_service.snapshot()
        rows = {row["symbol"]: row for row in snapshot["rows"]}
        state = (self.repository.get_system_value("last_v322_allocation_state") or "").split("|")
        leverage = state[0] if len(state) >= 1 and state[0] else "-"
        rs6m = "ON" if len(state) >= 2 and state[1] == "1" else "OFF"
        equity = Decimal(str(snapshot["equity"]))
        cash = Decimal(str(snapshot.get("cash", 0)))
        invested = Decimal(str(snapshot.get("invested_market_value", equity - cash)))
        lines = [
            f"☀️ <b>[{trade_date} JDSS 일일 운용보고]</b>",
            "",
            "🧭 <b>오늘의 운용 판단</b>",
            f"• 목표 노출 <code>{leverage}배</code> · 반도체 상대강도 <code>{rs6m}</code>",
            "• 과매도 보조신호는 종목별 점수 보고에서 확인",
            "",
            "💰 <b>현재 자산</b>",
            f"• 총자산 <code>{_money(equity)}</code>",
            f"• 투자중 <code>{_money(invested)}</code> · 현금 <code>{_money(cash)}</code>",
            f"• 최고자산 <code>{_money(snapshot['high_water'])}</code>",
            f"• 투자 위험한도 <code>{_money(snapshot['risk_budget'])}</code>",
            "",
            "📊 <b>목표 / 현재 비중</b>",
        ]
        for symbol in ALLOCATION_SYMBOLS:
            row = rows[symbol]
            lines.append(
                f"• {symbol} <code>{Decimal(str(row['target_weight'])) * 100:.1f}%</code> / "
                f"<code>{Decimal(str(row['core_weight'])) * 100:.1f}%</code> "
                f"({_quantity(row['core_quantity'])}주)"
            )
        lines.extend([
            "",
            "📝 <b>운영자 확인</b>",
            "• 목표보다 부족한 매수는 /signal에서 승인",
            "• 상세 자산구성은 /portfolio, 안전상태는 /dashboard",
            "• 실거래는 🔒 잠금 상태",
        ])
        return "\n".join(lines)

    def _format_idle_cash_message(self) -> str:
        if self.idle_cash_manager is None or not self.config.idle_cash.enabled:
            return "💵 <b>[유휴자금 운용]</b>\n\nSGOV 운용이 비활성화되어 있습니다."
        snapshot = self.idle_cash_manager.snapshot()
        state_label = "🚨 SAFE_MODE" if snapshot.safe_mode else "✅ 정상"
        personal_qty = max(0, snapshot.broker_quantity - snapshot.state.managed_quantity)
        return (
            "💵 <b>[JDSS SGOV 유휴자금]</b>\n\n"
            f"• <b>운용 상태</b> : {state_label}\n"
            f"• <b>JDSS 관리 수량</b> : <code>{snapshot.state.managed_quantity}주</code>\n"
            f"• <b>관리분 평가액</b> : <code>{_money(snapshot.market_value)}</code>\n"
            f"• <b>SGOV 현재가</b> : <code>{_money(snapshot.price)}</code>\n"
            f"• <b>목표 유휴자금</b> : <code>{_money(snapshot.target_value)}</code>\n"
            f"• <b>달러 주문가능</b> : <code>{_money(snapshot.buying_power)}</code>\n"
            f"• <b>비관리 SGOV</b> : <code>{personal_qty}주</code>\n\n"
            "💡 <i>비관리 수량은 기존 개인 보유분으로 간주하며 자동 매도하지 않습니다.</i>"
        )

    def _send_account(self) -> None:
        if self.account_client is None:
            self._send("🔐 토스 계좌 연결 정보를 확인해 주세요.")
            return
        try:
            buying_power = self.account_client.get_buying_power("USD")
            krw_buying_power = self.account_client.get_buying_power("KRW")
            holdings = [item for item in self.account_client.get_holdings() if _is_us_holding(item)]
            lines = [
                "☀️ <b>[JH홀딩스 미국주식 계좌]</b>",
                "",
                "💰 <b>계좌 요약</b>",
                f"• <b>달러 주문가능</b> : <code>{_money(buying_power)}</code>",
                f"• <b>원화 주문가능</b> : <code>{_won(krw_buying_power)}</code>",
                f"• <b>보유 종목</b> : <code>{len(holdings)}개</code>",
            ]
            if holdings:
                lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            for item in holdings:
                symbol = str(item.get("symbol") or item.get("stockCode") or "-").upper()
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice")
                current = item.get("currentPrice") or item.get("lastPrice")
                detail = f"✨ <b>{html.escape(symbol)}</b>"
                detail += f"\n• <b>보유 수량</b> : <code>{_quantity(quantity)}주</code>"
                if average is not None:
                    detail += f"\n• <b>평균 단가</b> : <code>{_money(average)}</code>"
                if current is not None:
                    detail += f"\n• <b>현재 가격</b> : <code>{_money(current)}</code>"
                profit = _profit_loss(item.get("profitLoss"))
                if profit:
                    detail += f"\n• <b>평가 손익</b> : <code>{profit[0]}</code> (<code>{profit[1]}</code>)"
                lines.append(detail)
            if not holdings:
                lines.append("현재 보유 중인 미국주식 종목이 없습니다.")

            lines.extend(
                [
                    "━━━━━━━━━━━━━━━━━━━━",
                    "💡 <i>안전을 위해 조회 전용 모드로 작동하고 있습니다.</i>",
                ]
            )
            self._send("\n".join(lines))
        except Exception as exc:
            LOGGER.exception("계좌 조회 실패")
            self._send(
                "❌ 토스 계좌 조회 중 오류 발생:\n"
                f"<code>{html.escape(_operator_error_summary(exc))}</code>"
            )

    def _get_account_lines(self) -> list[str]:
        if self.account_client is None:
            return ["💰 <b>[토스 실시간 계좌 잔고]</b>", "💡 <i>토스 계좌 정보 연결 대기 중 (조회 전용)</i>"]
        try:
            buying_power = self.account_client.get_buying_power("USD")
            holdings = [item for item in self.account_client.get_holdings() if _is_us_holding(item)]
            lines = [
                "💰 <b>[토스 실시간 계좌 잔고]</b>",
                f"• <b>주문가능 달러</b> : <code>{_money(buying_power)}</code>",
            ]
            for item in holdings:
                symbol = str(item.get("symbol") or item.get("stockCode") or "-").upper()
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice")
                current = item.get("currentPrice") or item.get("lastPrice")
                detail = f"\n✨ <b>{html.escape(symbol)}</b>"
                detail += f"\n• <b>수량 / 평단</b> : <code>{_quantity(quantity)}주</code> (평단 <code>{_money(average) if average else '$0.00'}</code>)"
                profit = _profit_loss(item.get("profitLoss"))
                if current is not None and profit:
                    detail += f"\n• <b>현재가 / 손익</b> : <code>{_money(current)}</code> (<code>{profit[0]}</code>, <code>{profit[1]}</code>)"
                elif current is not None:
                    detail += f"\n• <b>현재가</b> : <code>{_money(current)}</code>"
                lines.append(detail)
            if not holdings:
                lines.append("현재 보유 중인 미국주식 종목이 없습니다.")
            return lines
        except Exception as exc:
            LOGGER.warning("대시보드 계좌 요약 조회 실패: %s", type(exc).__name__)
            return ["💰 <b>[토스 실시간 계좌 잔고]</b>", "💡 <i>실시간 계좌 정보 조회 중...</i>"]

    def _register_handlers(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["ping", "p"])
        def ping(message):
            if not self._authorized_message(message):
                return
            lock_text = (
                "🟢 해제 (실거래 가능)"
                if self.settings.live_trading_enabled
                else "🔒 잠금 (모의 전용)"
            )
            mode_label = (
                "🧪 모의투자"
                if self.settings.trading_mode == "dry_run"
                else "🔥 실거래"
            )
            self._send(
                "✨ <b>[JH홀딩스 JDSS 봇 상태]</b>\n\n"
                f"• <b>버전</b> : v{__version__}\n"
                f"• <b>운영 모드</b> : {mode_label}\n"
                f"• <b>실주문 잠금</b> : {lock_text}\n\n"
                "✅ <b>정상 가동 중입니다.</b>"
            )

        @bot.message_handler(commands=["dashboard", "d"])
        def dashboard(message):
            if not self._authorized_message(message):
                return
            try:
                results = self.analysis_service.analyze_all()
                mode_label = (
                    "🧪 모의운용(Yahoo 시세·SQLite 원장)"
                    if self.settings.trading_mode == "dry_run"
                    else "🔥 실거래"
                )
                lines = [
                    "🌟 <b>[JDSS V3.2.2 운영 대시보드]</b>",
                    "",
                    f"• <b>운영 모드</b> : {mode_label}",
                    "• <b>실거래</b> : 🔒 잠금",
                ]
                if results:
                    lines.extend(
                        [
                            f"• <b>완결봉 기준일</b> : <code>{results[0].trade_date.isoformat()}</code>",
                            f"• <b>시장 국면</b> : {_regime_label(results[0].score.regime.value)}",
                        ]
                    )
                safe_mode = self.repository.get_system_value("v322_portfolio_safe_mode") == "1"
                lines.append(
                    f"• <b>계좌 대조</b> : "
                    f"{'🚨 안전정지(SAFE_MODE)' if safe_mode else '✅ 정상'}"
                )
                lines.append("━━━━━━━━━━━━━━━━━━━━")
                if self.portfolio_service is not None:
                    snapshot = self.portfolio_service.snapshot()
                    equity = Decimal(str(snapshot["equity"]))
                    cash = Decimal(str(snapshot.get("cash", 0)))
                    invested = Decimal(str(snapshot.get("invested_market_value", equity - cash)))
                    profit = Decimal(str(snapshot.get("net_profit", 0)))
                    lines.extend(
                        [
                            "💰 <b>자산 요약</b>",
                            f"• 총자산 <code>{_money(equity)}</code> · 손익 <code>{_signed_money(profit)}</code>",
                            f"• 투자중 <code>{_money(invested)}</code> · 현금 <code>{_money(cash)}</code>",
                            f"• 투자한도(HWM75) <code>{_money(snapshot['risk_budget'])}</code>",
                            "",
                            "📊 <b>목표 / 현재 보유비중</b>",
                        ]
                    )
                    rows = {row["symbol"]: row for row in snapshot["rows"]}
                    for symbol in ALLOCATION_SYMBOLS:
                        row = rows[symbol]
                        lines.append(
                            f"• <b>{symbol}</b> "
                            f"<code>{Decimal(str(row['target_weight'])) * 100:.2f}%</code> / "
                            f"<code>{Decimal(str(row['core_weight'])) * 100:.2f}%</code> "
                            f"({_quantity(row['core_quantity'])}주)"
                        )
                lines.extend(["", "🎯 <b>추가매수 판단</b>"])
                for result in results:
                    lines.append(
                        f"• <b>{result.symbol}</b> <code>{result.score.total}점</code> · "
                        f"{'활성 후보' if result.decision.allowed else '비활성'}"
                    )
                pending_count = len(self.trading_service.active_signals())
                lines.extend(
                    [
                        "",
                        f"📨 <b>매수 승인 대기</b> : <code>{pending_count}건</code>",
                    ]
                )
                lines.extend(
                    [
                        "━━━━━━━━━━━━━━━━━━━━",
                        "ℹ️ 평소에는 아래 ‘오늘 주문 확인’만 누르면 됩니다. "
                        "개별 매수 승인은 <code>/signal</code>에서 자세히 볼 수 있습니다.",
                    ]
                )
                self._send("\n".join(lines), markup=self._dashboard_markup())
            except Exception as exc:
                LOGGER.exception("dashboard 실패")
                self._send(
                    "❌ 대시보드 수집 실패:\n"
                    f"<code>{html.escape(_operator_error_summary(exc))}</code>"
                )

        @bot.message_handler(commands=["account", "acct", "balance"])
        def account(message):
            if not self._authorized_message(message):
                return
            self._send_account()

        @bot.message_handler(commands=["portfolio", "pf"])
        def portfolio(message):
            if not self._authorized_message(message):
                return
            try:
                self._send(self._format_portfolio_message())
            except Exception:
                LOGGER.exception("portfolio 실패")
                self._send(
                    "❌ 포트폴리오 현황을 가져오지 못했습니다.\n"
                    "<code>/errors</code>에서 운영 기록을 확인하세요."
                )

        @bot.message_handler(commands=list(IDLE_CASH_COMMANDS))
        def idle_cash(message):
            if not self._authorized_message(message):
                return
            try:
                self._send(self._format_idle_cash_message())
            except Exception as exc:
                LOGGER.exception("SGOV 유휴자금 조회 실패")
                self._send(
                    "❌ SGOV 유휴자금 조회 중 오류 발생:\n"
                    f"<code>{html.escape(_operator_error_summary(exc))}</code>"
                )

        @bot.message_handler(commands=["history", "h"])
        def history(message):
            if not self._authorized_message(message):
                return
            try:
                symbols, days = parse_history_request(
                    message.text, self.config.enabled_symbols
                )
                self._send(f"⏳ 최근 {days}거래일 점수를 계산 중입니다...")
                for symbol in symbols:
                    rows = self.analysis_service.score_history(symbol, days)
                    self._send(self._format_score_history(symbol, rows, days))
            except Exception as exc:
                LOGGER.exception("history 실패")
                self._send(
                    "❌ 점수 이력 조회 중 오류 발생:\n"
                    f"<code>{html.escape(_operator_error_summary(exc))}</code>"
                )

        @bot.message_handler(commands=["score", "sc", "indicator", "i"])
        def score(message):
            if not self._authorized_message(message):
                return
            requested = self._requested_symbol(message.text)
            if requested and requested not in self.config.enabled_symbols:
                self._send(
                    "⚠️ 가상 오버레이 점수는 "
                    f"<code>{'</code>, <code>'.join(self.config.enabled_symbols)}</code>만 지원합니다."
                )
                return
            try:
                results = self.analysis_service.analyze_all()
                for result in results:
                    if requested and result.symbol != requested:
                        continue
                    self._send(self._format_score_message(result))
                self.notify_new_signals(results)
            except Exception as exc:
                LOGGER.exception("score 실패")
                self._send(
                    "❌ 점수 계산 중 오류 발생:\n"
                    f"<code>{html.escape(_operator_error_summary(exc))}</code>"
                )

        @bot.message_handler(commands=["signal", "sg"])
        def signal(message):
            if not self._authorized_message(message):
                return
            signals = self.trading_service.active_signals()
            if not signals:
                self._send(
                    "✅ <b>현재 대기 중인 위험증가 BUY 승인이 없습니다.</b>\n"
                    "목표비중이 모두 채워졌다는 뜻은 아닐 수 있으므로 <code>/portfolio</code>도 확인하세요."
                )
                return
            for item in signals:
                self._send_signal(item)

        @bot.message_handler(commands=["status", "st"])
        def status(message):
            if not self._authorized_message(message):
                return
            requested = self._requested_symbol(message.text)
            if requested and requested not in ALLOCATION_SYMBOLS:
                self._send("⚠️ <code>/status</code>는 QQQ, TQQQ, SOXL만 지원합니다.")
                return
            for symbol in ALLOCATION_SYMBOLS:
                if requested and symbol != requested:
                    continue
                self._send(self._format_status_message(symbol))

        @bot.message_handler(commands=["guide", "g", "info", "explain"])
        def guide(message):
            if not self._authorized_message(message):
                return
            try:
                for card in _guide_cards():
                    self._send(card, chat_id=message.chat.id)
            except Exception as exc:
                LOGGER.exception("guide 실패")
                self._send(
                    "❌ 가이드 출력 실패:\n"
                    f"<code>{html.escape(_operator_error_summary(exc))}</code>",
                    chat_id=message.chat.id,
                )

        @bot.message_handler(commands=["order", "o"])
        def orders(message):
            if not self._authorized_message(message):
                return
            values = self.repository.open_orders()
            if not values:
                self._send("✅ <b>[미체결 주문]</b>\n\n현재 대기 중인 주문이 없습니다.")
                return
            lines = ["✨ <b>[JDSS 미체결 주문]</b>", ""]
            for item in values:
                price_str = _money(item["price"]) if item["price"] else "시장가"
                lines.append(
                    f"🌟 <b>{item['symbol']}</b> · {item['purpose']}\n"
                    f"<code>├ 방향 : {'매수' if item['side'] == 'BUY' else '매도'}</code>\n"
                    f"<code>├ 수량 : {_quantity(item['qty'])}주</code>\n"
                    f"<code>├ 가격 : {price_str}</code>\n"
                    f"<code>└ 상태 : {item['status']}</code>\n"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["errors", "err"])
        def errors(message):
            if not self._authorized_message(message):
                return
            events = self.repository.recent_events(10)
            if not events:
                self._send("✅ <b>[시스템 이벤트]</b>\n\n최근 기록된 이벤트가 없습니다.")
                return
            lines = ["✨ <b>[최근 JDSS 시스템 이벤트]</b>", ""]
            for event in events:
                lines.append(
                    f"<code>[{event['severity']}] {event['created_at'][11:19]}</code>\n"
                    f"<b>{html.escape(event['event_type'])}</b>: "
                    f"{html.escape(_redact_operator_text(event['message']))}\n"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["backtest", "bt"])
        def backtest(message):
            """Run one canonical V3.2.2 portfolio backtest in a worker thread."""
            if not self._authorized_message(message):
                return
            try:
                completed = self.market_clock.latest_completed_session()
                request = parse_backtest_request(
                    message.text,
                    self.config.enabled_symbols,
                    self.config.backtest.default_start,
                    completed,
                )
            except BacktestCommandError as exc:
                self._send(
                    f"⚠️ <b>{html.escape(_operator_error_summary(exc))}</b>\n\n"
                    "💡 <b>사용 예시</b>\n"
                    "<code>/bt</code> — 최근 300거래일\n"
                    "<code>/bt 100</code> — 최근 100거래일\n"
                    "<code>/bt full</code> — 2011년부터 전체\n"
                    "<code>/bt 2024-01-01 2025-12-31</code> — 지정 기간\n\n"
                    "ℹ️ Telegram 백테스트는 V3.2.2 QQQ·TQQQ·SOXL "
                    "통합 포트폴리오만 실행합니다."
                )
                return
            if not self._backtest_lock.acquire(blocking=False):
                self._send(
                    "⏳ <b>다른 백테스트가 이미 실행 중입니다.</b>\n완료 알림 후 다시 시도해 주세요."
                )
                return
            self._send(
                "⏳ <b>[JDSS V3.2.2 통합 백테스트 시작]</b>\n\n"
                f"• <b>시작일</b> : <code>{request.start}</code>\n"
                f"• <b>종료일</b> : <code>{request.end}</code>\n"
                "• <b>계산</b> : 2011년부터 가상 오버레이 상태를 연결한 뒤 "
                "요청 기간만 평가\n"
                "• <b>최초진입</b> : 시작일 50% → 3거래일 뒤 75% → "
                "3거래일 뒤 100%\n\n"
                "ℹ️ 수 분 걸릴 수 있으며, 완료되면 결과를 보냅니다."
            )
            try:
                threading.Thread(
                    target=self._run_backtest_and_send,
                    args=(request,),
                    daemon=True,
                ).start()
            except Exception:
                self._backtest_lock.release()
                raise

        @bot.message_handler(commands=["help", "menu", "start"])
        def help_handler(message):
            if not self._authorized_message(message):
                return
            self._send(
                "🤖 <b>[JDSS 운영 도움말]</b>\n\n"
                "<b>평소에는 네 가지만 사용합니다</b>\n"
                "• <code>/dashboard</code> — 오늘 상태와 해야 할 일\n"
                "• <code>/today</code> — 오늘 주문 확인\n"
                "• <code>/account</code> — 실제 토스 계좌 조회\n"
                "• <code>/onboarding</code> — 첫 투자 50% → 75% → 100% 단계\n\n"
                "<b>문제가 생겼을 때</b>\n"
                "• <code>/halt</code> — 신규 매수 즉시 중지\n"
                "• <code>/order</code> — 진행 중인 주문 확인\n"
                "• <code>/errors</code> — 최근 문제와 안전정지 기록\n"
                "• <code>/ping</code> — 봇 작동 상태 확인\n"
                "• <code>/resume</code> — 계좌 대조 후 신규 매수 재개\n\n"
                "<b>자세히 보고 싶을 때</b>\n"
                "• <code>/portfolio</code> — 목표·보유·투자한도\n"
                "• <code>/status [QQQ|TQQQ|SOXL]</code> — 종목별 상세\n"
                "• <code>/score [TQQQ|SOXL]</code> — 추가매수 판단\n"
                "• <code>/history [TQQQ|SOXL] [2~90]</code> — 최근 판단 변화\n"
                "• <code>/signal</code> — 개별 매수 승인(비상·상세용)\n"
                "• <code>/guide</code> — 전략 쉬운 설명\n"
                "• <code>/bt</code> / <code>/bt 100</code> / <code>/bt full</code> — 과거검증\n\n"
                "🛡️ 매수는 <b>주문 검토 → 60초 안에 최종 승인</b>의 두 단계를 거칩니다. "
                "위험을 줄이는 매도는 시스템이 먼저 처리합니다.\n"
                "🧪 모의운용에서는 승인해도 실제 토스 주문이 전송되지 않습니다."
            )

        @bot.callback_query_handler(
            func=lambda call: call.data.startswith("score|")
            or call.data.startswith("status|")
        )
        def dashboard_detail_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                command, symbol = call.data.split("|", 1)
                supported = (
                    self.config.enabled_symbols if command == "score" else ALLOCATION_SYMBOLS
                )
                if symbol not in supported:
                    raise ValueError("지원하지 않는 종목입니다.")
                if command == "score":
                    result = next(
                        item
                        for item in self.analysis_service.analyze_all()
                        if item.symbol == symbol
                    )
                    self._send(self._format_score_message(result))
                    answer = f"{symbol} 점수를 불러왔습니다."
                else:
                    self._send(self._format_status_message(symbol))
                    answer = f"{symbol} 포지션을 불러왔습니다."
                bot.answer_callback_query(call.id, answer)
            except Exception as exc:
                LOGGER.exception("대시보드 세부 조회 실패")
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("rv|"))
        def review_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, approval_id, token = call.data.split("|", 2)
                quote = self.trading_service.consume_review(int(approval_id), token)
                self._send_final_quote(quote)
                bot.answer_callback_query(call.id, "최종 주문조건을 계산했습니다.")
            except IdleCashReleasePending as exc:
                markup = InlineKeyboardMarkup()
                if exc.signal_id is not None:
                    markup.add(
                        InlineKeyboardButton(
                            "❌ 현금화 후 매수 취소",
                            callback_data=f"cancel|cash|{exc.signal_id}",
                        )
                    )
                self._send(
                    "💵 <b>SGOV 현금화를 진행하고 있습니다.</b>\n\n"
                    "체결과 달러 매수가능금액이 확인되면 최종 매수 승인 버튼을 자동으로 보내드립니다.",
                    markup=markup,
                )
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )
            except Exception as exc:
                LOGGER.exception("매수 검토 실패")
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )
            finally:
                self._clear_callback_markup(call)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("ex|"))
        def execute_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, approval_id, token = call.data.split("|", 2)
                receipt = self.trading_service.execute(int(approval_id), token)
                mode_text = "모의주문" if self.settings.trading_mode == "dry_run" else "실주문"
                self._send(
                    f"✅ <b>[{mode_text} 전송 완료]</b> ✨\n\n"
                    f"• <b>주문 번호</b> : <code>{html.escape(receipt.broker_order_id)}</code>\n"
                    f"• <b>처리 상태</b> : <code>{html.escape(receipt.status)}</code>\n"
                    f"• <b>체결 수량</b> : <code>{_quantity(receipt.filled_quantity)} / {_quantity(receipt.quantity)}주</code>"
                )
                bot.answer_callback_query(call.id, f"{mode_text}이 처리되었습니다.")
            except QuoteChangedError as exc:
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )
            except Exception as exc:
                LOGGER.exception("최종 주문 실행 실패")
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )
            finally:
                self._clear_callback_markup(call)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("cancel|"))
        def cancel_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, kind, raw_id = call.data.split("|", 2)
                if kind == "approval":
                    canceled = self.trading_service.cancel_approval(int(raw_id))
                elif kind == "cash":
                    self.trading_service.cancel_cash_release(int(raw_id))
                    canceled = True
                else:
                    raise ValueError("지원하지 않는 취소 유형입니다.")
            except Exception as exc:
                bot.answer_callback_query(
                    call.id, _operator_error_summary(exc), show_alert=True
                )
                return
            finally:
                self._clear_callback_markup(call)
            if not canceled:
                bot.answer_callback_query(
                    call.id,
                    "이미 사용·만료·취소된 승인입니다.",
                    show_alert=True,
                )
                return
            self._send(
                "❌ <b>위험증가 BUY 승인을 취소했습니다.</b>\n"
                "같은 신호의 1·2차 활성 승인 버튼은 모두 종료됩니다."
            )
            bot.answer_callback_query(call.id, "취소했습니다.")

    def notify_new_signals(self, results: list[AnalysisResult]) -> None:
        for result in results:
            if result.signal_created and result.signal_id is not None:
                self._send_signal(self.repository.get_signal(result.signal_id))

    def _send_signal(self, signal: dict) -> None:
        approval_id, token = self.trading_service.create_review_approval(signal["signal_id"])
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                _review_buy_button_text(signal["symbol"]),
                callback_data=f"rv|{approval_id}|{token}",
            )
        )
        markup.add(
            InlineKeyboardButton(
                "❌ 무시 / 취소", callback_data=f"cancel|approval|{approval_id}"
            )
        )
        is_allocation = signal["action"] == "CORE_REBALANCE_BUY"
        if is_allocation:
            core = self.repository.get_core_position(signal["symbol"])
            context_lines = (
                f"<code>├ 목표 비중 : {Decimal(str(core['target_weight'])) * 100:.2f}%</code>\n"
                "<code>├ 실행 기준 : 완결봉 다음 미국 거래일</code>\n"
            )
            title = "V3.2.2 allocation 위험증가 BUY"
        else:
            context_lines = (
                f"<code>├ 신호 점수 : {signal['score']:>3}점 · {_grade_label(signal['grade'])}</code>\n"
                f"<code>├ 시장 국면 : {_regime_label(signal['regime'])}</code>\n"
            )
            title = "구버전 direct 신호 재검증"
        self._send(
            f"🌟 <b>[{signal['symbol']} {title}]</b>\n\n"
            f"{context_lines}"
            f"<code>├ 신호 기준일 : {signal['trade_date']}</code>\n"
            f"<code>├ 투자 금액 : {_money(signal['planned_budget']):>11}</code>\n"
            f"<code>├ 신호 가격 : {_money(signal['signal_close']):>11}</code>\n"
            f"<code>└ 추격 상한 : {_money(signal['max_chase_price']):>11}</code>\n\n"
            "💡 <b>‘매수 주문 검토하기’ 버튼은 아직 주문을 제출하지 않습니다.</b> "
            "최신 시세·허용가·수량·자금을 "
            "다시 검증한 뒤, 60초 유효한 2차 최종 승인을 보냅니다.",
            markup=markup,
        )

    def _send_final_quote(self, quote) -> None:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                _execute_buy_button_text(
                    quote.symbol,
                    quote.quantity,
                    self.settings.trading_mode,
                ),
                callback_data=f"ex|{quote.execution_approval_id}|{quote.execution_token}",
            )
        )
        markup.add(
            InlineKeyboardButton(
                "❌ 취소",
                callback_data=f"cancel|approval|{quote.execution_approval_id}",
            )
        )
        signal = self.repository.get_signal(quote.signal_id)
        sleeve = (
            "V3.2.2 allocation"
            if signal["action"] == "CORE_REBALANCE_BUY"
            else "구버전 direct"
        )
        mode_text = "모의매수" if self.settings.trading_mode == "dry_run" else "실매수"
        self._send(
            f"🌞 <b>[{quote.symbol} {sleeve} 2차 최종 {mode_text} 승인]</b>\n\n"
            f"• <b>현재 가격</b> : <code>{_money(quote.current_price)}</code>\n"
            f"• <b>지정 가격</b> : <code>{_money(quote.limit_price)}</code>\n"
            f"• <b>주문 수량</b> : <code>{_quantity(quote.quantity)}주</code>\n"
            f"• <b>투자 금액</b> : <code>{_money(quote.planned_budget)}</code>\n"
            f"• <b>예상 수수료</b> : <code>{_money(quote.estimated_fee)}</code>\n\n"
            f"⏰ <i>{self.config.global_.execution_token_ttl_seconds}초 안에 실행해야 하며, "
            "버튼을 눌러도 가격·수량·상태를 즉시 다시 검증합니다.</i>",
            markup=markup,
        )

    def _run_backtest_and_send(self, request: TelegramBacktestRequest) -> None:
        try:
            run = run_production_backtest(
                self.config,
                self.data_source,
                symbols=request.symbols,
                start=request.start,
                end=request.end,
            )
            for warning in run.warnings:
                self._send(f"⚠️ <b>[백테스트 데이터 안내]</b>\n{html.escape(warning)}")
            if run.portfolio is None:
                raise RuntimeError("V3.2.2 통합 포트폴리오 결과가 생성되지 않았습니다")
            self._send_long(self._format_portfolio_backtest_result(run.portfolio))
        except (TimeoutError, ConnectionError) as exc:
            LOGGER.exception("Telegram 백테스트 네트워크 실패")
            self._send(
                "🌐 <b>[백테스트 네트워크 오류]</b>\n\n"
                "시세 서버에 연결하지 못했습니다. 인터넷 연결을 확인하고 잠시 후 다시 시도하세요."
            )
            self._log_backtest_failure("NETWORK", exc)
        except MarketDataError as exc:
            if _is_network_error(exc):
                LOGGER.exception("Telegram 백테스트 네트워크 실패")
                self._send(
                    "🌐 <b>[백테스트 네트워크 오류]</b>\n\n"
                    "시세 서버에 연결하지 못했습니다. 캐시가 없다면 인터넷 복구 후 다시 시도하세요."
                )
                self._log_backtest_failure("NETWORK", exc)
            else:
                LOGGER.exception("Telegram 백테스트 데이터 실패")
                self._send(
                    "📉 <b>[백테스트 데이터 오류]</b>\n\n"
                    "필요한 완결 일봉이 없거나 데이터가 비어 있습니다. "
                    "기간을 확인하거나 잠시 후 다시 시도하세요."
                )
                self._log_backtest_failure("DATA", exc)
        except ValueError as exc:
            LOGGER.exception("Telegram 백테스트 기간/데이터 실패")
            self._send(
                "📅 <b>[백테스트 기간·데이터 오류]</b>\n\n"
                f"{html.escape(_operator_error_summary(exc))}\n\n"
                "<code>/bt</code> 또는 <code>/bt full</code>로 다시 시도해 보세요."
            )
            self._log_backtest_failure("DATA_OR_PERIOD", exc)
        except Exception as exc:
            LOGGER.exception("Telegram 백테스트 실패")
            self._send(
                "🛠️ <b>[백테스트 내부 오류]</b>\n\n"
                "계산 결과를 만들거나 표시하는 중 오류가 발생했습니다. "
                "<code>/errors</code>에서 기록을 확인하세요."
            )
            self._log_backtest_failure("INTERNAL", exc)
        finally:
            self._backtest_lock.release()

    def _log_backtest_failure(self, category: str, exc: Exception) -> None:
        try:
            self.repository.log_event(
                "ERROR",
                "TELEGRAM_BACKTEST_FAILED",
                f"{category}:{type(exc).__name__}",
                context={"category": category, "exception": type(exc).__name__},
            )
        except Exception:
            LOGGER.exception("Telegram 백테스트 실패 이벤트 기록 오류")

    @staticmethod
    def _format_trade_timeline(result: BacktestResult, limit: int = 15) -> list[str]:
        events: list[tuple[str, int, str]] = []
        skipped_labels = {
            "SKIPPED_BY_CHASE_RULE": "추격상한초과",
            "STAGE_PRICE_RECOVERED": "가격회복미체결",
            "SLIPPAGE_EXCEEDS_PRICE_CEILING": "허용가초과",
            "ZERO_QUANTITY": "수량부족",
            "EXPOSURE_BLOCK": "한도초과",
        }
        for skipped in result.skipped_signals:
            reason = skipped_labels.get(str(skipped.get("reason")), "미체결")
            d = str(skipped["execution_date"]).replace("-", "")[2:]
            score_prefix = f"{skipped.get('score', 0)}점|" if "score" in skipped else ""
            details = f"[{score_prefix}{reason}]"
            events.append(
                (
                    str(skipped["execution_date"]),
                    1,
                    f"<code>⚪[{d}][매수미체결]{details}</code>",
                )
            )
        buy_counter: dict[str, int] = {}
        for trade in result.trades:
            purpose = str(trade["purpose"])
            cycle_id = str(trade.get("cycle_id", "default"))
            d = str(trade["date"]).replace("-", "")[2:]
            qty = trade["quantity"]
            price = Decimal(str(trade["price"]))
            price_str = _money(price)
            qty_str = f"{_quantity(qty)}주"

            if trade["side"] == "BUY":
                score_val = trade.get("score", 0)
                details = f"[{score_val}점|{qty_str}|{price_str}]"
                if purpose == "FIRST_ENTRY_CANDIDATE":
                    buy_counter[cycle_id] = 1
                    label = "1차매수"
                    icon = "🟢"
                elif purpose == "REBUY_CANDIDATE":
                    label = "재매수"
                    icon = "🟢"
                else:  # ADD_ENTRY_CANDIDATE
                    count = buy_counter.get(cycle_id, 1) + 1
                    buy_counter[cycle_id] = count
                    label = f"{count}차매수"
                    icon = {2: "🟡", 3: "🟠", 4: "🔴"}.get(count, "🔴")
                suffix = ""
            else:  # SELL
                details = f"[{qty_str}|{price_str}]"
                if purpose == "TP1":
                    label = "1차익절"
                    icon = "⚫"
                    suffix = "🎉"
                elif purpose == "TP2":
                    label = "2차완청"
                    icon = "⚫"
                    suffix = "🎉"
                elif purpose == "REMAINDER_EXIT":
                    label = "잔여청산"
                    icon = "⚫"
                    suffix = "🎉"
                else:
                    label = "매도"
                    icon = "⚫"
                    suffix = ""
            events.append(
                (
                    str(trade["date"]),
                    1,
                    f"<code>{icon}[{d}][{label}]{details}</code>{suffix}",
                )
            )
        events.sort(key=lambda item: (item[0], item[1]))
        omitted = max(0, len(events) - limit)
        selected = events[-limit:]
        lines = [event[2] for event in selected]
        if omitted:
            lines.insert(0, f"<code>…전체 {len(events)}건 중 최근 {limit}건 표시</code>")
        return lines

    def _format_backtest_results(self, results: dict[str, BacktestResult]) -> str:
        equity = pd.concat(
            [result.equity_curve.rename(symbol) for symbol, result in results.items()],
            axis=1,
            join="inner",
        ).sum(axis=1)
        initial = float(equity.iloc[0])
        final = float(equity.iloc[-1])
        total_return = final / initial - 1
        first_result = next(iter(results.values()))
        lines = [
            "🔬 <b>[JDSS 가상 오버레이 연구 결과]</b>",
            "",
            f"<code>├ 시작일   : {first_result.start_date}</code>",
            f"<code>├ 종료일   : {first_result.end_date}</code>",
            f"<code>├ 초기자산 : {_money(initial):>12}</code>",
            f"<code>├ 최종자산 : {_money(final):>12}</code>",
            f"<code>├ 누적수익 : {total_return * 100:>+10.2f}%</code>",
            f"<code>└ 최대낙폭 : {maximum_drawdown(equity) * 100:>10.2f}%</code>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        for symbol, result in results.items():
            metrics = result.metrics
            lines.extend(
                [
                    f"✨ <b>[{symbol} 성과]</b>",
                    f"<code>├ 수익률 : {metrics['total_return_pct']:>+8.2f}%</code>",
                    f"<code>├ MDD    : {metrics['mdd_pct']:>8.2f}%</code>",
                    f"<code>├ 청산횟수 : {metrics['closed_cycles']:>8}회</code>",
                    f"<code>└ 승률    : {metrics['win_rate_pct']:>8.1f}%</code>",
                    "",
                ]
            )
            timeline = self._format_trade_timeline(result)
            lines.append(f"📜 <b>{symbol} 매매 내역</b>")
            if timeline:
                lines.extend(timeline)
            else:
                lines.append("조건에 도달한 매수 신호가 없었습니다.")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_portfolio_backtest_result(
        result: PortfolioBacktestResult,
    ) -> str:
        metrics = result.metrics
        final_cash = metrics.get("final_cash")
        holdings_value = metrics.get("final_holdings_value")
        total_fees = metrics.get("total_fees")
        net_profit = metrics.get(
            "net_profit", metrics["final_equity"] - metrics["initial_equity"]
        )
        lines = [
            "✅ <b>[JDSS V3.2.2 통합 백테스트 완료]</b>",
            "",
            f"<code>├ 기간     : {result.start_date} ~ {result.end_date}</code>",
            f"<code>├ 초기자산 : {_money(metrics['initial_equity']):>12}</code>",
            f"<code>├ 최종자산 : {_money(metrics['final_equity']):>12}</code>",
            f"<code>├ 누적수익 : {metrics['total_return_pct']:>+10.2f}%</code>",
            f"<code>├ CAGR     : {metrics['cagr_pct']:>+10.2f}%</code>",
            f"<code>├ 최대낙폭 : {metrics['mdd_pct']:>10.2f}%</code>",
            f"<code>├ Sharpe   : {metrics['sharpe']:>10.3f}</code>",
            f"<code>├ Sortino  : {metrics['sortino']:>10.3f}</code>",
            f"<code>└ 평균노출 : {metrics['average_exposure_pct']:>10.2f}%</code>",
            "",
            "💰 <b>종료일 자산 구성</b>",
            f"• 순손익 : <code>{_signed_money(net_profit)}</code>",
            *([f"• 남은 현금 : <code>{_money(final_cash)}</code>"] if final_cash is not None else []),
            *([f"• 보유 평가액 : <code>{_money(holdings_value)}</code>"] if holdings_value is not None else []),
            *([f"• 누적 수수료 : <code>{_money(total_fees)}</code>"] if total_fees is not None else []),
            "━━━━━━━━━━━━━━━━━━",
            "📦 <b>운영 계약</b>",
            "• 최초진입: <code>50% → 75% → 100%</code> "
            f"(각 <code>{metrics['initial_onboarding_minimum_sessions']}거래일</code>)",
            f"• allocation 체결: <code>{metrics['component_fills']['allocation']}회</code>",
            f"• HWM 이익 재투입: <code>{metrics['hwm_reinvestment_fraction'] * 100:.0f}%</code>",
            f"• 최대 sizing base: <code>{_money(metrics['maximum_sizing_base'])}</code>",
            f"• 슬리피지: <code>{result.slippage * 100:.2f}%</code>",
            "• SGOV: <code>OFF</code>",
        ]
        holdings = metrics.get("final_holdings", {})
        if holdings:
            lines.extend(["", "📦 <b>종료일 보유수량</b>"])
            for symbol in ALLOCATION_SYMBOLS:
                item = holdings.get(symbol, {})
                lines.append(
                    f"• {symbol} {_quantity(item.get('quantity', 0))}주 · "
                    f"<code>{_money(item.get('market_value', 0))}</code>"
                )
        lines.extend(["", "📜 <b>최근 allocation 체결</b>"])
        for trade in result.trades[-15:]:
            side = "매수" if trade["side"] == "BUY" else "매도"
            purpose = str(trade.get("purpose", ""))
            stage_match = re.match(r"INITIAL_ENTRY_STAGE_(\d+)_", purpose)
            if stage_match:
                reason = f"최초진입 {stage_match.group(1)}차"
            elif trade["side"] == "SELL":
                reason = "위험축소"
            else:
                reason = "목표변경"
            lines.append(
                f"<code>[{str(trade['date'])[2:].replace('-', '')}] "
                f"{trade['symbol']} {reason} {side} "
                f"{trade['quantity']}주 @{_money(trade['price'])}</code>"
            )
        if not result.trades:
            lines.append("해당 기간 체결이 없습니다.")
        lines.append(
            "\nℹ️ <i>완료봉 신호·다음 거래일 체결·매수/매도 수수료·슬리피지와 "
            "2011년부터 이어진 가상 오버레이 상태와 요청 시작일의 최초 분할진입을 "
            "반영했습니다. 과거 성과는 미래 수익을 보장하지 않습니다.</i>"
        )
        return "\n".join(lines)

    @staticmethod
    def _requested_symbol(text: str) -> str | None:
        parts = (text or "").split()
        return parts[1].upper() if len(parts) >= 2 else None

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            daily_due = _daily_analysis_is_due(
                datetime.now(SEOUL_TZ),
                self.config.scheduler.daily_analysis_time_kst,
            )
            try:
                completed = (
                    self.market_clock.latest_completed_session(
                        delay_minutes=self.config.scheduler.signal_delay_minutes
                    )
                    if daily_due
                    else None
                )
                last_analysis = self.repository.get_system_value("last_analysis_trade_date")
                if self.portfolio_service is not None and completed is not None:
                    portfolio_run = self.portfolio_service.run_month_end()
                    if portfolio_run is not None:
                        self._send(
                            self._format_daily_portfolio_report(portfolio_run.trade_date)
                        )
                        for event in portfolio_run.events:
                            if event.startswith("V3.2.2 배분 ") or event.startswith("HWM USD "):
                                continue
                            self._send(f"⚠️ {html.escape(event)}")
                        for signal_id in portfolio_run.signals:
                            self._send_signal(self.repository.get_signal(signal_id))
                if completed is not None and last_analysis != completed.isoformat():
                    results = self.analysis_service.analyze_all()
                    self.notify_new_signals(results)
                monitor_due = (
                    time.monotonic() - self._last_monitor
                    >= self.config.scheduler.order_monitor_interval_seconds
                )

                # 토스증권 주간거래 전 주문 취소·리셋 시간에는 주문 상태 갱신을 건너뜁니다.
                if _is_toss_order_maintenance_window(datetime.now(SEOUL_TZ)):
                    monitor_due = False

                if monitor_due:
                    for event in self.order_monitor.run_once():
                        self._send(f"ℹ️ {html.escape(event)}")
                    if self.idle_cash_manager is not None:
                        for event in self.idle_cash_manager.refresh_orders():
                            self._send(_format_idle_cash_event(event, self.settings.trading_mode))
                        for quote in self.trading_service.resume_cash_releases():
                            self._send(
                                f"💵 <b>[{quote.symbol} SGOV 현금화 완료]</b>\n"
                                "최신 주문조건을 다시 확인했습니다."
                            )
                            self._send_final_quote(quote)
                    mismatches = self.reconciliation_service.run()
                    for symbol, issues in mismatches.items():
                        self._send(
                            f"🚨 <b>[{symbol} SAFE_MODE 경고]</b>\n"
                            + "\n".join(html.escape(issue) for issue in issues)
                        )
                    self._last_monitor = time.monotonic()
                cash_due = (
                    self.idle_cash_manager is not None
                    and time.monotonic() - self._last_idle_cash_sweep
                    >= self.config.idle_cash.sweep_interval_seconds
                )
                if cash_due:
                    for event in self.idle_cash_manager.run_once():
                        self._send(_format_idle_cash_event(event, self.settings.trading_mode))
                    self._last_idle_cash_sweep = time.monotonic()
                self.repository.expire_stale_signals()
            except Exception as exc:
                LOGGER.exception("scheduler 실패")
                self.repository.log_event("WARNING", "SCHEDULER_ERROR", str(exc))

    def run(self) -> None:
        self.bot.set_my_commands(
            [
                telebot.types.BotCommand("dashboard", "☀️ 통합 대시보드"),
                telebot.types.BotCommand("portfolio", "📊 V3.2.2 배분·HWM75 현황"),
                telebot.types.BotCommand("account", "💰 토스 계좌 잔고"),
                telebot.types.BotCommand("status", "✨ 종목별 allocation 상세"),
                telebot.types.BotCommand("score", "🎯 5% 가상 오버레이 분석"),
                telebot.types.BotCommand("history", "📈 최근 점수 이력"),
                telebot.types.BotCommand("signal", "🚨 위험증가 BUY 승인"),
                telebot.types.BotCommand("backtest", "🌞 V3.2.2 통합 백테스트"),
                telebot.types.BotCommand("guide", "📖 V3.2.2 전략 설명서"),
                telebot.types.BotCommand("order", "🌟 미체결 주문 현황"),
                telebot.types.BotCommand("errors", "🛡 최근 시스템 기록"),
                telebot.types.BotCommand("ping", "🏓 봇 상태 확인"),
                telebot.types.BotCommand("help", "🤖 메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        LOGGER.info("JDSS Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
