from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import ApprovalStage, PositionState, RiskReviewLevel
from jd_holdings.core.models import IdleCashState, PositionSnapshot, ScoreResult, TradeDecision

OPEN_ORDER_STATUSES = (
    "CREATED",
    "SUBMITTED",
    "PENDING",
    "PARTIAL_FILLED",
    "PENDING_CANCEL",
    "PENDING_REPLACE",
    "UNKNOWN",
)
TERMINAL_ORDER_STATUSES = ("FILLED", "CANCELED", "REJECTED", "REPLACED")
ALL_ORDER_STATUSES = (*OPEN_ORDER_STATUSES, *TERMINAL_ORDER_STATUSES)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


class StateConflictError(RuntimeError):
    pass


class ApprovalError(RuntimeError):
    pass


class SQLiteRepository:
    def __init__(self, db_path: str | Path, config: StrategyConfig) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self._lock = threading.RLock()
        self._transaction_local = threading.local()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._transaction_local, "connection", None)
        if active is not None:
            # Reads inside a unit of work must see its uncommitted writes without
            # allowing a nested connection context to commit the outer transaction.
            yield active
            return
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._transaction_local, "connection", None)
        if active is not None:
            savepoint = "nested_" + secrets.token_hex(8)
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
            except BaseException:
                active.execute(f"ROLLBACK TO {savepoint}")
                raise
            finally:
                active.execute(f"RELEASE {savepoint}")
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._transaction_local.connection = connection
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                self._transaction_local.connection = None

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    cycle_id TEXT,
                    qty INTEGER NOT NULL DEFAULT 0 CHECK(qty >= 0),
                    avg_price TEXT NOT NULL DEFAULT '0',
                    current_cost_basis TEXT NOT NULL DEFAULT '0',
                    cycle_exposure_cap TEXT NOT NULL DEFAULT '0',
                    staged_entry_capital TEXT NOT NULL DEFAULT '0',
                    cash_remaining TEXT NOT NULL DEFAULT '0',
                    entry_count INTEGER NOT NULL DEFAULT 0,
                    anchor_price TEXT NOT NULL DEFAULT '0',
                    last_entry_price TEXT NOT NULL DEFAULT '0',
                    last_entry_date TEXT,
                    rebuy_count INTEGER NOT NULL DEFAULT 0,
                    rebuy_recovery_armed INTEGER NOT NULL DEFAULT 0,
                    tp1_filled_qty INTEGER NOT NULL DEFAULT 0,
                    risk_review_level TEXT NOT NULL DEFAULT 'NONE',
                    tp_plan_id INTEGER,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signals (
                    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT,
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    score_detail_json TEXT NOT NULL,
                    atr_pct TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_stage INTEGER,
                    signal_close TEXT NOT NULL,
                    stage_trigger_price TEXT,
                    max_chase_price TEXT NOT NULL,
                    planned_budget TEXT NOT NULL,
                    cycle_exposure_cap TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    processed INTEGER NOT NULL DEFAULT 0,
                    expired_reason TEXT,
                    strategy_version TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    code_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(symbol, trade_date, strategy_version, config_version, action)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL REFERENCES signals(signal_id),
                    approval_token_hash TEXT NOT NULL,
                    approval_stage TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used_at TEXT
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    broker_order_id TEXT UNIQUE,
                    client_order_id TEXT NOT NULL UNIQUE,
                    signal_id INTEGER REFERENCES signals(signal_id),
                    cycle_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    price TEXT,
                    qty INTEGER NOT NULL,
                    filled_qty INTEGER NOT NULL DEFAULT 0,
                    average_fill_price TEXT,
                    status TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    applied INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    fee TEXT NOT NULL,
                    realized_pnl TEXT,
                    fill_time TEXT NOT NULL,
                    broker_order_id TEXT NOT NULL,
                    UNIQUE(broker_order_id, side, price, qty, fill_time)
                );

                CREATE TABLE IF NOT EXISTS tp_plans (
                    tp_plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT,
                    symbol TEXT NOT NULL,
                    source_event TEXT NOT NULL,
                    avg_price TEXT NOT NULL,
                    atr_pct TEXT NOT NULL,
                    tp1_price TEXT NOT NULL,
                    tp1_target_qty INTEGER NOT NULL,
                    tp1_filled_qty INTEGER NOT NULL DEFAULT 0,
                    tp2_price TEXT NOT NULL,
                    tp2_target_qty INTEGER NOT NULL,
                    tp2_filled_qty INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT,
                    symbol TEXT NOT NULL,
                    previous_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_logs (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    severity TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    message TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS idle_cash_state (
                    symbol TEXT PRIMARY KEY,
                    managed_qty INTEGER NOT NULL DEFAULT 0 CHECK(managed_qty >= 0),
                    avg_price TEXT NOT NULL DEFAULT '0',
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS idle_cash_fill_progress (
                    client_order_id TEXT PRIMARY KEY REFERENCES orders(client_order_id),
                    applied_filled_qty INTEGER NOT NULL DEFAULT 0 CHECK(applied_filled_qty >= 0),
                    applied_notional TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cash_release_intents (
                    intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL UNIQUE REFERENCES signals(signal_id),
                    required_amount TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'WAITING_SGOV_FILL',
                    execution_approval_id INTEGER REFERENCES approvals(approval_id),
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS core_positions (
                    symbol TEXT PRIMARY KEY,
                    underlying TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 0 CHECK(qty >= 0),
                    avg_price TEXT NOT NULL DEFAULT '0',
                    cost_basis TEXT NOT NULL DEFAULT '0',
                    target_weight TEXT NOT NULL DEFAULT '0',
                    target_qty INTEGER NOT NULL DEFAULT 0 CHECK(target_qty >= 0),
                    trend_active INTEGER NOT NULL DEFAULT 0,
                    signal_trade_date TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS core_fill_progress (
                    client_order_id TEXT PRIMARY KEY REFERENCES orders(client_order_id),
                    applied_filled_qty INTEGER NOT NULL DEFAULT 0,
                    applied_notional TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_signals_active
                    ON signals(status, symbol, trade_date);
                CREATE INDEX IF NOT EXISTS idx_orders_status
                    ON orders(status, symbol);
                CREATE INDEX IF NOT EXISTS idx_approvals_status
                    ON approvals(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_cash_release_intents_status
                    ON cash_release_intents(status, expires_at);
                """
            )
            now = utc_now().isoformat()
            for symbol, underlying in self.config.portfolio.core_underlyings.items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO core_positions(
                        symbol, underlying, target_weight, updated_at
                    ) VALUES (?, ?, '0', ?)
                    """,
                    (symbol, underlying, now),
                )
            tp_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(tp_plans)").fetchall()
            }
            if "revision" not in tp_columns:
                connection.execute(
                    "ALTER TABLE tp_plans ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            idle_fill_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(idle_cash_fill_progress)"
                ).fetchall()
            }
            if "applied_notional" not in idle_fill_columns:
                connection.execute(
                    """
                    ALTER TABLE idle_cash_fill_progress
                    ADD COLUMN applied_notional TEXT NOT NULL DEFAULT '0'
                    """
                )
            core_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(core_positions)"
                ).fetchall()
            }
            if "target_qty" not in core_columns:
                connection.execute(
                    """
                    ALTER TABLE core_positions
                    ADD COLUMN target_qty INTEGER NOT NULL DEFAULT 0
                    """
                )
            core_fill_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(core_fill_progress)"
                ).fetchall()
            }
            if "applied_notional" not in core_fill_columns:
                connection.execute(
                    """
                    ALTER TABLE core_fill_progress
                    ADD COLUMN applied_notional TEXT NOT NULL DEFAULT '0'
                    """
                )
                legacy_rows = connection.execute(
                    """
                    SELECT progress.client_order_id, progress.applied_filled_qty,
                           orders.average_fill_price, orders.price
                    FROM core_fill_progress AS progress
                    JOIN orders
                      ON orders.client_order_id = progress.client_order_id
                    WHERE progress.applied_filled_qty > 0
                    """
                ).fetchall()
                for legacy in legacy_rows:
                    price = _decimal(
                        legacy["average_fill_price"] or legacy["price"]
                    )
                    connection.execute(
                        """
                        UPDATE core_fill_progress
                        SET applied_notional = ?, updated_at = ?
                        WHERE client_order_id = ?
                        """,
                        (
                            str(price * int(legacy["applied_filled_qty"])),
                            now,
                            legacy["client_order_id"],
                        ),
                    )
            for symbol in self.config.enabled_symbols:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO positions(
                        symbol, state, cash_remaining, updated_at
                    ) VALUES (?, 'EMPTY', ?, ?)
                    """,
                    (symbol, str(self.config.global_.capital_per_symbol), now),
                )
            if self.config.idle_cash.enabled:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO idle_cash_state(symbol, updated_at)
                    VALUES (?, ?)
                    """,
                    (self.config.idle_cash.symbol, now),
                )

    def get_idle_cash_state(self) -> IdleCashState:
        symbol = self.config.idle_cash.symbol
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM idle_cash_state WHERE symbol = ?", (symbol,)
            ).fetchone()
        if row is None:
            return IdleCashState(symbol=symbol)
        return IdleCashState(
            symbol=str(row["symbol"]),
            managed_quantity=int(row["managed_qty"]),
            average_price=_decimal(row["avg_price"]),
            version=int(row["version"]),
        )

    def apply_idle_cash_fill(self, client_order_id: str) -> IdleCashState:
        """Apply only the newly reported cumulative fill quantity for one SGOV order."""
        terminal_statuses = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}
        now = utc_now().isoformat()
        with self.transaction() as connection:
            order = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            if order is None:
                raise KeyError(client_order_id)
            if not str(order["purpose"]).startswith("SGOV_"):
                raise ValueError("SGOV 자금관리 주문이 아닙니다")
            state = connection.execute(
                "SELECT * FROM idle_cash_state WHERE symbol = ?", (order["symbol"],)
            ).fetchone()
            if state is None:
                raise KeyError(f"idle_cash_state:{order['symbol']}")
            progress = connection.execute(
                """
                SELECT applied_filled_qty, applied_notional FROM idle_cash_fill_progress
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            applied_qty = int(progress["applied_filled_qty"]) if progress else 0
            applied_notional = _decimal(progress["applied_notional"]) if progress else Decimal("0")
            cumulative_qty = int(order["filled_qty"])
            delta_qty = cumulative_qty - applied_qty
            if delta_qty < 0:
                raise StateConflictError("SGOV 누적 체결수량이 이전 반영값보다 작습니다")
            managed_qty = int(state["managed_qty"])
            average_price = _decimal(state["avg_price"])
            if delta_qty > 0:
                fill_price = _decimal(order["average_fill_price"] or order["price"])
                cumulative_notional = fill_price * cumulative_qty
                delta_notional = cumulative_notional - applied_notional
                if delta_notional < 0:
                    raise StateConflictError("SGOV 누적 체결금액이 이전 반영값보다 작습니다")
                if order["side"] == "BUY":
                    new_qty = managed_qty + delta_qty
                    average_price = (
                        average_price * managed_qty + delta_notional
                    ) / new_qty
                    managed_qty = new_qty
                elif order["side"] == "SELL":
                    if delta_qty > managed_qty:
                        raise StateConflictError("JDSS 관리 SGOV 수량보다 많은 매도 체결입니다")
                    managed_qty -= delta_qty
                    if managed_qty == 0:
                        average_price = Decimal("0")
                else:
                    raise ValueError("SGOV 주문 방향이 BUY/SELL이 아닙니다")
                connection.execute(
                    """
                    UPDATE idle_cash_state
                    SET managed_qty = ?, avg_price = ?, version = version + 1, updated_at = ?
                    WHERE symbol = ?
                    """,
                    (managed_qty, str(average_price), now, order["symbol"]),
                )
            connection.execute(
                """
                INSERT INTO idle_cash_fill_progress(
                    client_order_id, applied_filled_qty, applied_notional, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    applied_filled_qty = excluded.applied_filled_qty,
                    applied_notional = excluded.applied_notional,
                    updated_at = excluded.updated_at
                """,
                (
                    client_order_id,
                    cumulative_qty,
                    str(
                        _decimal(order["average_fill_price"] or order["price"])
                        * cumulative_qty
                    )
                    if cumulative_qty > 0
                    else str(applied_notional),
                    now,
                ),
            )
            if str(order["status"]) in terminal_statuses:
                connection.execute(
                    "UPDATE orders SET applied = 1, updated_at = ? WHERE client_order_id = ?",
                    (now, client_order_id),
                )
        return self.get_idle_cash_state()

    def strategy_invested_capital(self) -> Decimal:
        with self._connect() as connection:
            booster_rows = connection.execute(
                "SELECT current_cost_basis FROM positions"
            ).fetchall()
            core_rows = connection.execute(
                "SELECT cost_basis FROM core_positions"
            ).fetchall()
        booster = sum(
            (_decimal(row["current_cost_basis"]) for row in booster_rows), Decimal("0")
        )
        core = sum((_decimal(row["cost_basis"]) for row in core_rows), Decimal("0"))
        return booster + core

    def has_active_approvals(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM approvals
                WHERE status = 'ACTIVE' AND expires_at >= ? LIMIT 1
                """,
                (utc_now().isoformat(),),
            ).fetchone()
        return row is not None

    def next_idle_cash_order_attempt(self, symbol: str, purpose: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS attempts FROM orders
                WHERE symbol = ? AND purpose = ?
                """,
                (symbol.upper(), purpose),
            ).fetchone()
        return int(row["attempts"]) + 1

    def get_position(self, symbol: str) -> PositionSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM positions WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
        if row is None:
            raise KeyError(f"등록되지 않은 종목: {symbol}")
        return PositionSnapshot(
            symbol=row["symbol"],
            state=PositionState(row["state"]),
            cycle_id=row["cycle_id"],
            quantity=int(row["qty"]),
            average_price=_decimal(row["avg_price"]),
            current_cost_basis=_decimal(row["current_cost_basis"]),
            cycle_exposure_cap=_decimal(row["cycle_exposure_cap"]),
            staged_entry_capital=_decimal(row["staged_entry_capital"]),
            cash_remaining=_decimal(row["cash_remaining"]),
            entry_count=int(row["entry_count"]),
            anchor_price=_decimal(row["anchor_price"]),
            last_entry_price=_decimal(row["last_entry_price"]),
            last_entry_date=date.fromisoformat(row["last_entry_date"])
            if row["last_entry_date"]
            else None,
            rebuy_count=int(row["rebuy_count"]),
            rebuy_recovery_armed=bool(row["rebuy_recovery_armed"]),
            tp1_filled_qty=int(row["tp1_filled_qty"]),
            risk_review_level=RiskReviewLevel(row["risk_review_level"]),
            version=int(row["version"]),
        )

    def transition_position(
        self,
        symbol: str,
        *,
        expected_state: PositionState,
        new_state: PositionState,
        reason_code: str,
        updates: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> PositionSnapshot:
        allowed_columns = {
            "cycle_id",
            "qty",
            "avg_price",
            "current_cost_basis",
            "cycle_exposure_cap",
            "staged_entry_capital",
            "cash_remaining",
            "entry_count",
            "anchor_price",
            "last_entry_price",
            "last_entry_date",
            "rebuy_count",
            "rebuy_recovery_armed",
            "tp1_filled_qty",
            "risk_review_level",
            "tp_plan_id",
        }
        values = dict(updates or {})
        unknown = set(values) - allowed_columns
        if unknown:
            raise ValueError(f"허용되지 않은 position 필드: {sorted(unknown)}")
        now = utc_now().isoformat()
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT state, cycle_id, version FROM positions WHERE symbol = ?",
                (symbol.upper(),),
            ).fetchone()
            if current is None:
                raise KeyError(symbol)
            if current["state"] != expected_state.value:
                raise StateConflictError(
                    f"상태 충돌: expected={expected_state.value}, actual={current['state']}"
                )
            if expected_version is not None and int(current["version"]) != expected_version:
                raise StateConflictError("position version 충돌")
            assignments = ["state = ?", "version = version + 1", "updated_at = ?"]
            parameters: list[Any] = [new_state.value, now]
            for key, value in values.items():
                assignments.append(f"{key} = ?")
                if isinstance(value, (Decimal, date, datetime)):
                    value = _iso(value) if isinstance(value, (date, datetime)) else str(value)
                elif isinstance(value, bool):
                    value = int(value)
                elif isinstance(value, RiskReviewLevel):
                    value = value.value
                parameters.append(value)
            parameters.extend([symbol.upper(), current["version"]])
            cursor = connection.execute(
                f"UPDATE positions SET {', '.join(assignments)} WHERE symbol = ? AND version = ?",  # nosec B608
                parameters,
            )
            if cursor.rowcount != 1:
                raise StateConflictError("동시 상태 변경을 감지했습니다")
            cycle_id = values.get("cycle_id", current["cycle_id"])
            connection.execute(
                """
                INSERT INTO state_history(
                    cycle_id, symbol, previous_state, new_state, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    symbol.upper(),
                    expected_state.value,
                    new_state.value,
                    reason_code,
                    now,
                ),
            )
        return self.get_position(symbol)

    def create_signal(
        self,
        *,
        symbol: str,
        trade_date: date,
        score: ScoreResult,
        atr_pct: Decimal,
        decision: TradeDecision,
        signal_close: Decimal,
        max_chase_price: Decimal,
        valid_until: datetime,
        code_version: str,
        cycle_id: str | None,
    ) -> tuple[int, bool]:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO signals(
                    cycle_id, symbol, trade_date, score, grade, regime,
                    score_detail_json, action, target_stage, signal_close,
                    atr_pct, stage_trigger_price, max_chase_price, planned_budget,
                    cycle_exposure_cap, valid_until, strategy_version,
                    config_version, code_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    symbol.upper(),
                    trade_date.isoformat(),
                    score.total,
                    score.grade.value,
                    score.regime.value,
                    json.dumps(score.detail(), ensure_ascii=False, sort_keys=True),
                    decision.action.value,
                    decision.target_stage,
                    str(signal_close),
                    str(atr_pct),
                    str(decision.stage_trigger_price)
                    if decision.stage_trigger_price is not None
                    else None,
                    str(max_chase_price),
                    str(decision.planned_budget),
                    str(decision.cycle_exposure_cap),
                    valid_until.astimezone(UTC).isoformat(),
                    self.config.version,
                    self.config.config_version,
                    code_version,
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                return int(cursor.lastrowid), True
            row = connection.execute(
                """
                SELECT signal_id FROM signals
                WHERE symbol = ? AND trade_date = ? AND strategy_version = ?
                  AND config_version = ? AND action = ?
                """,
                (
                    symbol.upper(),
                    trade_date.isoformat(),
                    self.config.version,
                    self.config.config_version,
                    decision.action.value,
                ),
            ).fetchone()
            return int(row["signal_id"]), False

    def get_signal(self, signal_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"signal_id={signal_id}")
        result = dict(row)
        result["score_detail"] = json.loads(result.pop("score_detail_json"))
        return result

    def active_signals(self, symbol: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM signals WHERE status = 'ACTIVE' AND processed = 0"
        params: list[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def invalidate_active_signals(
        self,
        symbol: str,
        *,
        reason: str,
        keep_trade_date: date | None = None,
        keep_action: str | None = None,
    ) -> int:
        query = """
            UPDATE signals
            SET status = 'INVALID', processed = 1,
                expired_reason = ?, updated_at = ?
            WHERE symbol = ? AND status = 'ACTIVE' AND processed = 0
        """
        params: list[Any] = [reason, utc_now().isoformat(), symbol.upper()]
        if keep_trade_date is not None and keep_action is not None:
            query += """
                AND NOT (
                    trade_date = ? AND action = ?
                    AND strategy_version = ? AND config_version = ?
                )
            """
            params.extend(
                [
                    keep_trade_date.isoformat(),
                    keep_action,
                    self.config.version,
                    self.config.config_version,
                ]
            )
        with self.transaction() as connection:
            cursor = connection.execute(query, params)
            return cursor.rowcount

    def mark_signal(
        self, signal_id: int, *, status: str, processed: bool, reason: str | None = None
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE signals
                SET status = ?, processed = ?, expired_reason = ?, updated_at = ?
                WHERE signal_id = ?
                """,
                (status, int(processed), reason, utc_now().isoformat(), signal_id),
            )

    def expire_stale_signals(self, now: datetime | None = None) -> int:
        current = (now or utc_now()).astimezone(UTC).isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE signals
                SET status = 'EXPIRED', processed = 1,
                    expired_reason = 'SIGNAL_EXPIRED', updated_at = ?
                WHERE status = 'ACTIVE' AND processed = 0 AND valid_until < ?
                """,
                (current, current),
            )
            return cursor.rowcount

    def create_approval(
        self,
        signal_id: int,
        stage: ApprovalStage,
        ttl: timedelta,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        token = secrets.token_urlsafe(16)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO approvals(
                    signal_id, approval_token_hash, approval_stage, payload_json,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    token_hash,
                    stage.value,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    (now + ttl).isoformat(),
                    now.isoformat(),
                ),
            )
            return int(cursor.lastrowid), token

    def consume_approval(
        self, approval_id: int, token: str, expected_stage: ApprovalStage
    ) -> tuple[int, dict[str, Any]]:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError("승인 요청을 찾을 수 없습니다")
            if row["status"] != "ACTIVE":
                raise ApprovalError("이미 사용되었거나 취소된 승인입니다")
            if row["approval_stage"] != expected_stage.value:
                raise ApprovalError("승인 단계가 일치하지 않습니다")
            if datetime.fromisoformat(row["expires_at"]) < now:
                connection.execute(
                    "UPDATE approvals SET status = 'EXPIRED' WHERE approval_id = ?",
                    (approval_id,),
                )
                if expected_stage == ApprovalStage.EXECUTION:
                    ttl_label = (
                        f"{self.config.global_.execution_token_ttl_seconds}초"
                    )
                else:
                    ttl_label = f"{self.config.global_.review_token_ttl_minutes}분"
                raise ApprovalError(
                    f"{expected_stage.value} 승인 유효시간({ttl_label})이 만료되었습니다. "
                    "최신 조건으로 다시 확인해 주세요."
                )
            if not hmac.compare_digest(row["approval_token_hash"], token_hash):
                raise ApprovalError("승인 토큰이 올바르지 않습니다")
            connection.execute(
                "UPDATE approvals SET status = 'USED', used_at = ? WHERE approval_id = ?",
                (now.isoformat(), approval_id),
            )
            return int(row["signal_id"]), json.loads(row["payload_json"])

    def cancel_approval(self, approval_id: int) -> tuple[int, int]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT signal_id, status FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError("승인 요청을 찾을 수 없습니다")
            signal_id = int(row["signal_id"])
            cursor = connection.execute(
                "UPDATE approvals SET status = 'CANCELED' "
                "WHERE signal_id = ? AND status = 'ACTIVE'",
                (signal_id,),
            )
            return signal_id, cursor.rowcount

    def upsert_cash_release_intent(
        self, signal_id: int, required_amount: Decimal, expires_at: datetime
    ) -> dict[str, Any]:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO cash_release_intents(
                    signal_id, required_amount, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    required_amount = excluded.required_amount,
                    status = CASE
                        WHEN cash_release_intents.status IN ('COMPLETED', 'CANCELED', 'EXPIRED')
                        THEN 'WAITING_SGOV_FILL'
                        ELSE cash_release_intents.status
                    END,
                    execution_approval_id = CASE
                        WHEN cash_release_intents.status IN ('COMPLETED', 'CANCELED', 'EXPIRED')
                        THEN NULL
                        ELSE cash_release_intents.execution_approval_id
                    END,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    signal_id,
                    str(required_amount),
                    expires_at.astimezone(UTC).isoformat(),
                    now,
                    now,
                ),
            )
        return self.get_cash_release_intent(signal_id)

    def get_cash_release_intent(self, signal_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cash_release_intents WHERE signal_id = ?", (signal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(signal_id)
        return dict(row)

    def cash_release_intent_is_active(self, signal_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM cash_release_intents
                WHERE signal_id = ?
                  AND status IN ('WAITING_SGOV_FILL', 'AWAITING_EXECUTION')
                  AND expires_at >= ?
                """,
                (signal_id, utc_now().isoformat()),
            ).fetchone()
        return row is not None

    def pending_cash_release_intents(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or utc_now()).astimezone(UTC).isoformat()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE approvals SET status = 'EXPIRED'
                WHERE status = 'ACTIVE' AND expires_at < ?
                """,
                (current,),
            )
            connection.execute(
                """
                UPDATE cash_release_intents
                SET status = 'CANCELED', updated_at = ?
                WHERE status = 'AWAITING_EXECUTION'
                  AND execution_approval_id IN (
                      SELECT approval_id FROM approvals WHERE status != 'ACTIVE'
                  )
                """,
                (current,),
            )
            connection.execute(
                """
                UPDATE cash_release_intents
                SET status = 'EXPIRED', updated_at = ?
                WHERE status IN ('WAITING_SGOV_FILL', 'AWAITING_EXECUTION')
                  AND expires_at < ?
                """,
                (current, current),
            )
            rows = connection.execute(
                """
                SELECT * FROM cash_release_intents
                WHERE status = 'WAITING_SGOV_FILL' AND expires_at >= ?
                ORDER BY created_at
                """,
                (current,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_active_cash_release_intents(self, now: datetime | None = None) -> bool:
        current = (now or utc_now()).astimezone(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM cash_release_intents
                WHERE status IN ('WAITING_SGOV_FILL', 'AWAITING_EXECUTION')
                  AND expires_at >= ? LIMIT 1
                """,
                (current,),
            ).fetchone()
        return row is not None

    def reserved_cash_release_amount(self, exclude_signal_id: int | None = None) -> Decimal:
        query = """
            SELECT required_amount FROM cash_release_intents
            WHERE status = 'AWAITING_EXECUTION' AND expires_at >= ?
        """
        params: list[Any] = [utc_now().isoformat()]
        if exclude_signal_id is not None:
            query += " AND signal_id != ?"
            params.append(exclude_signal_id)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return sum((_decimal(row["required_amount"]) for row in rows), Decimal("0"))

    def update_cash_release_intent(
        self,
        signal_id: int,
        *,
        status: str,
        execution_approval_id: int | None = None,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE cash_release_intents
                SET status = ?, execution_approval_id = ?, updated_at = ?
                WHERE signal_id = ?
                """,
                (status, execution_approval_id, utc_now().isoformat(), signal_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(signal_id)

    def reserve_order(
        self,
        *,
        client_order_id: str,
        signal_id: int | None,
        cycle_id: str | None,
        symbol: str,
        side: str,
        order_type: str,
        price: Decimal | None,
        quantity: int,
        purpose: str,
    ) -> bool:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO orders(
                    client_order_id, signal_id, cycle_id, symbol, side, order_type,
                    price, qty, status, purpose, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?)
                """,
                (
                    client_order_id,
                    signal_id,
                    cycle_id,
                    symbol.upper(),
                    side,
                    order_type,
                    str(price) if price is not None else None,
                    quantity,
                    purpose,
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def update_order(
        self,
        client_order_id: str,
        *,
        status: str,
        broker_order_id: str | None = None,
        filled_qty: int | None = None,
        average_fill_price: Decimal | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = status.upper()
        if normalized_status not in ALL_ORDER_STATUSES:
            raise ValueError(f"지원하지 않는 주문 상태입니다: {status}")
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [normalized_status, utc_now().isoformat()]
        if broker_order_id:
            updates.append("broker_order_id = ?")
            params.append(broker_order_id)
        if filled_qty is not None:
            updates.append("filled_qty = ?")
            params.append(filled_qty)
        if average_fill_price is not None:
            updates.append("average_fill_price = ?")
            params.append(str(average_fill_price))
        if raw is not None:
            updates.append("raw_json = ?")
            params.append(json.dumps(raw, ensure_ascii=False, sort_keys=True))
        params.append(client_order_id)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT qty, filled_qty, status FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(client_order_id)
            prior_status = str(existing["status"]).upper()
            if (
                prior_status in TERMINAL_ORDER_STATUSES
                and normalized_status != prior_status
            ):
                raise StateConflictError(
                    f"종료 주문 상태를 되돌릴 수 없습니다: "
                    f"{prior_status} -> {normalized_status}"
                )
            if filled_qty is not None:
                prior_filled = int(existing["filled_qty"])
                ordered = int(existing["qty"])
                if filled_qty < prior_filled:
                    raise StateConflictError(
                        "주문 누적 체결수량이 이전 저장값보다 작습니다"
                    )
                if filled_qty > ordered:
                    raise StateConflictError(
                        "주문 누적 체결수량이 주문수량을 초과합니다"
                    )
                if filled_qty > 0 and (
                    average_fill_price is not None and average_fill_price <= 0
                ):
                    raise StateConflictError("체결평균가는 0보다 커야 합니다")
            cursor = connection.execute(
                f"UPDATE orders SET {', '.join(updates)} WHERE client_order_id = ?", params  # nosec B608
            )
            if cursor.rowcount != 1:  # pragma: no cover - guarded by SELECT above
                raise StateConflictError("주문 갱신 중 상태가 변경됐습니다")

    def mark_order_applied(self, client_order_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE orders SET applied = 1, updated_at = ?
                WHERE client_order_id = ? AND applied = 0
                """,
                (utc_now().isoformat(), client_order_id),
            )
            return cursor.rowcount == 1

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return dict(row) if row else None

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in OPEN_ORDER_STATUSES)
        query = f"SELECT * FROM orders WHERE status IN ({placeholders})"  # nosec B608
        params: list[Any] = list(OPEN_ORDER_STATUSES)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def create_tp_plan(
        self,
        *,
        cycle_id: str | None,
        symbol: str,
        source_event: str,
        average_price: Decimal,
        atr_pct: Decimal,
        tp1_price: Decimal,
        tp1_target_qty: int,
        tp2_price: Decimal,
        tp2_target_qty: int,
    ) -> int:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE tp_plans SET active = 0, updated_at = ? WHERE symbol = ? AND active = 1",
                (now, symbol.upper()),
            )
            cursor = connection.execute(
                """
                INSERT INTO tp_plans(
                    cycle_id, symbol, source_event, avg_price, atr_pct,
                    tp1_price, tp1_target_qty, tp2_price, tp2_target_qty,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    symbol.upper(),
                    source_event,
                    str(average_price),
                    str(atr_pct),
                    str(tp1_price),
                    tp1_target_qty,
                    str(tp2_price),
                    tp2_target_qty,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def active_tp_plan(self, symbol: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM tp_plans
                WHERE symbol = ? AND active = 1
                ORDER BY tp_plan_id DESC LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def update_tp_fills(self, tp_plan_id: int, *, leg: str, filled_qty: int) -> None:
        column = {"TP1": "tp1_filled_qty", "TP2": "tp2_filled_qty"}.get(leg)
        if column is None:
            raise ValueError("leg는 TP1 또는 TP2여야 합니다")
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE tp_plans SET {column} = ?, updated_at = ? WHERE tp_plan_id = ?",  # nosec B608
                (filled_qty, utc_now().isoformat(), tp_plan_id),
            )

    def bump_tp_revision(self, tp_plan_id: int) -> int:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE tp_plans
                SET revision = revision + 1, updated_at = ?
                WHERE tp_plan_id = ? AND active = 1
                """,
                (utc_now().isoformat(), tp_plan_id),
            )
            row = connection.execute(
                "SELECT revision FROM tp_plans WHERE tp_plan_id = ? AND active = 1",
                (tp_plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(tp_plan_id)
            return int(row["revision"])

    def deactivate_tp_plan(self, tp_plan_id: int) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE tp_plans SET active = 0, updated_at = ? WHERE tp_plan_id = ?",
                (utc_now().isoformat(), tp_plan_id),
            )

    def set_system_value(self, key: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, utc_now().isoformat()),
            )

    def get_system_value(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def log_event(
        self,
        severity: str,
        event_type: str,
        message: str,
        *,
        symbol: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO event_logs(
                    severity, event_type, symbol, message, context_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    severity,
                    event_type,
                    symbol,
                    message,
                    json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
                    utc_now().isoformat(),
                ),
            )

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM event_logs ORDER BY event_id DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def get_core_position(self, symbol: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM core_positions WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
        if row is None:
            raise KeyError(symbol)
        return dict(row)

    def core_positions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM core_positions ORDER BY symbol"
                ).fetchall()
            ]

    def set_core_target(
        self,
        symbol: str,
        *,
        active: bool,
        target_weight: Decimal,
        signal_trade_date: date,
        target_qty: int | None = None,
    ) -> None:
        if target_qty is not None and target_qty < 0:
            raise ValueError("target_qty는 0 이상이어야 합니다")
        with self.transaction() as connection:
            assignments = [
                "trend_active = ?",
                "target_weight = ?",
                "signal_trade_date = ?",
            ]
            values: list[Any] = [
                int(active),
                str(target_weight),
                signal_trade_date.isoformat(),
            ]
            if target_qty is not None:
                assignments.append("target_qty = ?")
                values.append(target_qty)
            assignments.extend(["version = version + 1", "updated_at = ?"])
            values.extend([utc_now().isoformat(), symbol.upper()])
            connection.execute(
                f"UPDATE core_positions SET {', '.join(assignments)} WHERE symbol = ?",  # nosec B608
                values,
            )

    def create_core_buy_signal(
        self,
        *,
        symbol: str,
        trade_date: date,
        signal_close: Decimal,
        planned_budget: Decimal,
        valid_until: datetime,
        code_version: str,
        reactivate_existing: bool = False,
    ) -> tuple[int, bool]:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO signals(
                    cycle_id, symbol, trade_date, score, grade, regime,
                    score_detail_json, atr_pct, action, target_stage,
                    signal_close, stage_trigger_price, max_chase_price,
                    planned_budget, cycle_exposure_cap, valid_until,
                    strategy_version, config_version, code_version,
                    created_at, updated_at
                ) VALUES (
                    NULL, ?, ?, 0, 'NO_TRADE', 'YELLOW', '{}', '0',
                    'CORE_REBALANCE_BUY', NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    symbol.upper(),
                    trade_date.isoformat(),
                    str(signal_close),
                    str(
                        signal_close
                        * (Decimal("1") + self.config.global_.entry_max_chase_pct)
                    ),
                    str(planned_budget),
                    str(planned_budget),
                    valid_until.isoformat(),
                    self.config.version,
                    self.config.config_version,
                    code_version,
                    now,
                    now,
                ),
            )
            if cursor.rowcount:
                return int(cursor.lastrowid), True
            row = connection.execute(
                """
                SELECT signal_id, status FROM signals
                WHERE symbol = ? AND trade_date = ? AND strategy_version = ?
                  AND config_version = ? AND action = 'CORE_REBALANCE_BUY'
                """,
                (
                    symbol.upper(),
                    trade_date.isoformat(),
                    self.config.version,
                    self.config.config_version,
                ),
            ).fetchone()
            if row is None:
                raise StateConflictError("코어 리밸런싱 신호 생성에 실패했습니다")
            if reactivate_existing and str(row["status"]) not in {"ACTIVE", "UNKNOWN"}:
                signal_id = int(row["signal_id"])
                connection.execute(
                    """
                    UPDATE approvals SET status = 'CANCELED'
                    WHERE signal_id = ? AND status = 'ACTIVE'
                    """,
                    (signal_id,),
                )
                connection.execute(
                    """
                    UPDATE signals
                    SET signal_close = ?, max_chase_price = ?, planned_budget = ?,
                        cycle_exposure_cap = ?, valid_until = ?, code_version = ?,
                        status = 'ACTIVE', processed = 0, expired_reason = NULL,
                        updated_at = ?
                    WHERE signal_id = ?
                    """,
                    (
                        str(signal_close),
                        str(
                            signal_close
                            * (Decimal("1") + self.config.global_.entry_max_chase_pct)
                        ),
                        str(planned_budget),
                        str(planned_budget),
                        valid_until.isoformat(),
                        code_version,
                        now,
                        signal_id,
                    ),
                )
                return signal_id, True
            return int(row["signal_id"]), False

    def unapplied_core_fill_order_ids(self) -> list[str]:
        """Return core orders whose cumulative quantity/notional is not in the ledger."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT orders.client_order_id, orders.filled_qty,
                       orders.average_fill_price, orders.price,
                       COALESCE(core_fill_progress.applied_filled_qty, 0)
                           AS applied_filled_qty,
                       COALESCE(core_fill_progress.applied_notional, '0')
                           AS applied_notional
                FROM orders
                LEFT JOIN core_fill_progress
                  ON core_fill_progress.client_order_id = orders.client_order_id
                WHERE orders.purpose IN ('CORE_REBALANCE_BUY', 'CORE_REBALANCE_SELL')
                  AND orders.filled_qty > 0
                ORDER BY orders.order_id
                """
            ).fetchall()
        pending: list[str] = []
        for row in rows:
            filled = int(row["filled_qty"])
            cumulative_notional = (
                _decimal(row["average_fill_price"] or row["price"]) * filled
            )
            if (
                filled != int(row["applied_filled_qty"])
                or cumulative_notional != _decimal(row["applied_notional"])
            ):
                pending.append(str(row["client_order_id"]))
        return pending

    def apply_core_fill(self, client_order_id: str) -> None:
        with self.transaction() as connection:
            order = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            if order is None:
                raise KeyError(client_order_id)
            filled = int(order["filled_qty"])
            progress = connection.execute(
                """
                SELECT applied_filled_qty, applied_notional
                FROM core_fill_progress WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            applied = int(progress["applied_filled_qty"]) if progress else 0
            applied_notional = (
                _decimal(progress["applied_notional"]) if progress else Decimal("0")
            )
            delta = filled - applied
            if delta < 0:
                raise StateConflictError("코어 누적 체결수량이 이전 반영값보다 작습니다")
            row = connection.execute(
                "SELECT * FROM core_positions WHERE symbol = ?", (order["symbol"],)
            ).fetchone()
            if row is None:
                raise KeyError(order["symbol"])
            current_qty = int(row["qty"])
            avg = _decimal(row["avg_price"])
            price = _decimal(order["average_fill_price"] or order["price"])
            cumulative_notional = price * filled
            delta_notional = cumulative_notional - applied_notional
            if delta > 0 and delta_notional < 0:
                raise StateConflictError("코어 누적 체결금액이 이전 반영값보다 작습니다")
            if delta == 0 and delta_notional == 0:
                return
            if order["side"] == "BUY":
                next_qty = current_qty + delta
                next_cost = _decimal(row["cost_basis"]) + delta_notional
                if next_cost < 0:
                    raise StateConflictError("코어 체결가 정정으로 원가가 음수가 됩니다")
                next_avg = next_cost / next_qty if next_qty else Decimal("0")
            elif order["side"] == "SELL":
                if delta > current_qty:
                    raise StateConflictError("코어 보유수량보다 많은 매도 체결입니다")
                next_qty = current_qty - delta
                next_avg = avg if next_qty else Decimal("0")
                next_cost = next_avg * next_qty
            else:
                raise ValueError("코어 주문 방향이 BUY/SELL이 아닙니다")
            connection.execute(
                """
                UPDATE core_positions
                SET qty = ?, avg_price = ?, cost_basis = ?, version = version + 1,
                    updated_at = ? WHERE symbol = ?
                """,
                (
                    next_qty,
                    str(next_avg),
                    str(next_cost),
                    utc_now().isoformat(),
                    order["symbol"],
                ),
            )
            connection.execute(
                """
                INSERT INTO core_fill_progress(
                    client_order_id, applied_filled_qty, applied_notional, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    applied_filled_qty = excluded.applied_filled_qty,
                    applied_notional = excluded.applied_notional,
                    updated_at = excluded.updated_at
                """,
                (
                    client_order_id,
                    filled,
                    str(cumulative_notional),
                    utc_now().isoformat(),
                ),
            )
