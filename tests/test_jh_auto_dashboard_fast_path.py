from __future__ import annotations

from types import SimpleNamespace

from jd_holdings.infrastructure.jh_auto_live_display import LiveJHAutoTelegramBotApp
from jd_holdings.infrastructure.jh_auto_telegram import JHAutoTelegramBotApp


class _FakeBot:
    def __init__(self) -> None:
        self.handlers: list[tuple[tuple[str, ...], object]] = []

    def message_handler(self, *, commands):
        def decorator(func):
            self.handlers.append((tuple(commands), func))
            return func

        return decorator


class _Message:
    text = "/dashboard"


def test_live_dashboard_fast_path_registers_before_legacy_handler(monkeypatch):
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app.bot = _FakeBot()
    sent: list[tuple[str, object]] = []
    app._authorized_message = lambda _message: True
    app._format_auto_dashboard = lambda: "FAST_AUTO_DASHBOARD"
    app._dashboard_markup = lambda: "FAST_MARKUP"
    app._send = lambda text, *, markup=None, chat_id=None: sent.append((text, markup))

    legacy_called = {"value": False}

    def fake_parent_register(self):
        @self.bot.message_handler(commands=["dashboard", "d"])
        def legacy_dashboard(_message):
            legacy_called["value"] = True

    monkeypatch.setattr(JHAutoTelegramBotApp, "_register_handlers", fake_parent_register)

    LiveJHAutoTelegramBotApp._register_handlers(app)

    assert app.bot.handlers[0][0] == ("dashboard", "d")
    assert app.bot.handlers[1][0] == ("dashboard", "d")

    # Mirror pyTelegramBotAPI's first-match handler behavior: the direct AUTO handler
    # must win, so the inherited dashboard never reaches analyze_all().
    for commands, handler in app.bot.handlers:
        if "dashboard" in commands:
            handler(_Message())
            break

    assert sent == [("FAST_AUTO_DASHBOARD", "FAST_MARKUP")]
    assert legacy_called["value"] is False


def test_prelaunch_hwm_relabel_does_not_request_second_performance_snapshot():
    app = object.__new__(LiveJHAutoTelegramBotApp)
    app.auto_service = SimpleNamespace(
        settings=lambda: SimpleNamespace(launch_authorized=False)
    )
    app._display_performance = lambda: (_ for _ in ()).throw(
        AssertionError("prelaunch HWM relabel must not fetch another snapshot")
    )

    text = "\n".join(
        [
            "• 최고 평가액 : <code>$50,000.00</code>",
            "• HWM75 위험한도 : <code>$50,000.00</code>",
        ]
    )

    rendered = app._replace_hwm_lines(text)

    assert "• 최고 평가액 : <b>시작 전</b>" in rendered
    assert "• HWM75 현재 위험예산 : <b>시작 전</b>" in rendered
    assert "$50,000.00" not in rendered
