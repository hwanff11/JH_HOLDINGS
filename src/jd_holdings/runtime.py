from __future__ import annotations

from jd_holdings.settings import load_runtime_settings


def main() -> None:
    settings = load_runtime_settings()
    if settings.trading_mode == "live":
        from jd_holdings.live_bot import main as run
    else:
        from jd_holdings.bot import main as run
    run()


if __name__ == "__main__":
    main()
