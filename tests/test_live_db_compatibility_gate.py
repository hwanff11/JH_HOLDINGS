import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD_CONNECTION_METHODS = """    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

"""


def sources():
    candidate = (ROOT / "src/jd_holdings/application/database.py").read_text()
    start = candidate.index("    @contextmanager\n    def _connect(")
    end = candidate.index("    def _initialize(")
    previous = (candidate[:start] + OLD_CONNECTION_METHODS + candidate[end:]).replace(
        "        self._transaction_local = threading.local()\n", ""
    )
    return previous, candidate


def run_gate(tmp_path, previous, candidate):
    before, after = tmp_path / "previous.py", tmp_path / "candidate.py"
    before.write_text(previous)
    after.write_text(candidate)
    script = (ROOT / "deploy_live_armed.sh").read_text()
    code = script.split("<<'PY_DB_CONTRACT'\n", 1)[1].split("\nPY_DB_CONTRACT", 1)[0]
    return subprocess.run(
        [sys.executable, "-", str(before), str(after)], input=code,
        capture_output=True, text=True, check=False,
    )


def test_approved_update_preserves_all_non_connection_code(tmp_path):
    previous, candidate = sources()
    def contract(source):
        tree = ast.parse(source)
        repo = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                    and n.name == "SQLiteRepository")
        repo.body = [n for n in repo.body if not isinstance(n, ast.FunctionDef)
                     or n.name not in {"__init__", "_connect", "transaction"}]
        return ast.dump(tree)
    assert contract(previous) == contract(candidate)
    result = run_gate(tmp_path, previous, candidate)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("which", [0, 1])
def test_identical_database_code_still_passes(tmp_path, which):
    source = sources()[which]
    assert run_gate(tmp_path, source, source).returncode == 0


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("CREATE TABLE IF NOT EXISTS positions", "CREATE TABLE positions_v2"),
    lambda s: s.replace("PRAGMA foreign_keys = ON", "PRAGMA foreign_keys = OFF"),
    lambda s: s.replace("connection.rollback()", "connection.commit()"),
])
def test_unreviewed_schema_or_transaction_change_is_rejected(tmp_path, mutation):
    previous, candidate = sources()
    result = run_gate(tmp_path, previous, mutation(candidate))
    assert result.returncode != 0
    assert "DB 스키마 코드 변경은 금지" in result.stderr


def test_reverse_update_is_not_implicitly_approved(tmp_path):
    previous, candidate = sources()
    assert run_gate(tmp_path, candidate, previous).returncode != 0
